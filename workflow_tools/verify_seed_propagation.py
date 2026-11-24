"""Independent scalar audit of seed-model relative propagation, without production replay."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

import numpy as np


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf-8')).hexdigest()


def verify(directory):
    if not __debug__:raise ValueError('数值核验不能使用-O')
    out=Path(directory);report=read(out/'seed_propagation_report.json');inventory=read(out/'manifest.json')
    assert set(inventory)=={p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file() and p.name not in ('manifest.json','独立标量核验.json')}
    for name,value in inventory.items():assert sha(out/name)==value,name
    protocol=read(out/'预登记方案.json');assert report['protocol']==protocol and digest(protocol)==report['protocol_sha256']
    assert protocol['seeds']==[20261122,2026112401,2026112402,2026112403,2026112404]
    assert protocol['control_seed']==20261122 and protocol['retained_fractions']==[0,.25,.5,.75,1]
    assert protocol['additional_fitting'] is False and protocol['seed_selection'] is False and protocol['output_clipping'] is False
    source=Path(report['source_seed_directory']);assert sha(source/'manifest.json')==report['source_manifest_sha256']
    for name,value in read(source/'manifest.json').items():assert sha(source/name)==value
    seed_report=read(source/'seed_report.json');original=Path(seed_report['source']['directory'])
    for name,value in read(original/'完整包校验.json').items():assert sha(original/name)==value
    rows=read(original/'04_FDNA拟合/输入_SE记录.json');fit=read(original/'04_FDNA拟合/workflow_report.json')
    assert digest(rows)==seed_report['source']['data_sha256']
    assert fit['input_sha256']==seed_report['source']['input_sha256']
    network=fit['network_snapshot'];nodes=sorted(network['nodes'])
    parents={n:sorted(e['feeder'] for e in network['edges'] if e['receiver']==n) for n in nodes}
    order=[];pairs=[]
    while len(order)<len(nodes):
        ready=[n for n in nodes if n not in order and all(p in order for p in parents[n])];assert ready;order.extend(ready)
    for start in nodes:
        seen=set();pending=[n for n in nodes if start in parents[n]]
        while pending:
            n=pending.pop()
            if n not in seen:seen.add(n);pending.extend(x for x in nodes if n in parents[x])
        pairs.extend([start,n] for n in sorted(seen))
    jobs=sorted({(f['fit_group'],f['model']) for f in read(source/'逐种子记录/seed_20261122.json')['fits']})
    cases=sorted({r['base_case_id'] for r in rows if r['split']=='holdout'});training=sorted({r['base_case_id'] for r in rows if r['split']=='train'})
    assert len(cases)==30 and len(training)==90 and not set(cases)&set(training)
    axes=report['axes'];assert axes['nodes']==nodes and axes['pairs']==pairs and axes['jobs']==[list(j) for j in jobs]
    assert axes['seeds']==protocol['seeds'] and axes['fractions']==protocol['retained_fractions'] and axes['base_cases']==cases
    lookup={(r['fit_group'],r['base_case_id']):r for r in rows};assert len(lookup)==len(rows)==360
    with np.load(out/'逐种子逐基准数组.npz',allow_pickle=False) as archive:arrays={k:archive[k] for k in archive.files}
    assert set(arrays)=={'decrease','changed','forced','valid','outside','extrapolated','baseline'}
    for name,a in arrays.items():
        assert a.shape==((5,6,30,9) if name=='baseline' else (5,6,9,5,30))
        assert a.dtype==np.dtype(bool if name in ('valid','outside','extrapolated') else float)
    maximum=0.;responses=0;non_descendants=0;outside=0;extra=0;seed_means={};focus_expected={}
    def close(a,b):
        nonlocal maximum
        if a is None or b is None:assert a is b;return
        assert math.isfinite(a) and math.isfinite(b)
        difference=abs(a-b);maximum=max(maximum,difference);assert difference<=1e-8,(a,b)
    def forward(fits,roots,forced=None):
        values=dict(roots)
        for n in order:
            if forced and n==forced[0]:values[n]=forced[1]
            elif parents[n]:
                f=fits[n];params=f['parameters']
                sod=math.fsum(100-p['alpha']*(100-values[p['feeder']]) for p in params)/len(params)
                cod=min(values[p['feeder']]+p['beta'] for p in params)
                values[n]=min(sod,cod)+(f['delta'] or 0.)
        return values
    for si,seed in enumerate(axes['seeds']):
        name=f'逐种子记录/seed_{seed}.json';assert sha(source/name)==report['source_record_sha256'][name]
        record=read(source/name);assert record['seed']==seed
        for j,(group,mode) in enumerate(jobs):
            fits={f['node']:f for f in record['fits'] if (f['fit_group'],f['model'])==(group,mode)}
            assert set(fits)=={n for n in nodes if parents[n]} and all(f['status']=='fitted' for f in fits.values())
            bounds={(n,p):(min(lookup[group,c]['node_values'][p] for c in training),max(lookup[group,c]['node_values'][p] for c in training)) for n in nodes for p in parents[n]}
            for k,(start,target) in enumerate(pairs):
                for fi,fraction in enumerate(axes['fractions']):
                    values_for_mean=[];cod_count=0
                    for b,case in enumerate(cases):
                        raw=lookup[group,case];assert raw['split']=='holdout'
                        roots={n:raw['node_values'][n] for n in nodes if not parents[n]};base=forward(fits,roots)
                        for ni,n in enumerate(nodes):close(arrays['baseline'][si,j,b,ni],base[n])
                        forced=base[start]*fraction;changed=forward(fits,roots,(start,forced));key=si,j,k,fi,b
                        assert 0<=base[start]<=100 and arrays['valid'][key]
                        decrease=base[target]-changed[target];assert decrease>=-1e-8
                        close(arrays['forced'][key],forced);close(arrays['changed'][key],changed[target]);close(arrays['decrease'][key],decrease)
                        if fraction==1:close(decrease,0.)
                        is_out=not 0<=changed[target]<=100
                        is_extra=any(changed[p]<bounds[target,p][0]-1e-8 or changed[p]>bounds[target,p][1]+1e-8 for p in parents[target])
                        assert bool(arrays['outside'][key])==is_out and bool(arrays['extrapolated'][key])==is_extra
                        outside+=is_out;extra+=is_extra;responses+=1;values_for_mean.append(decrease)
                        descendants={n for s,n in pairs if s==start}
                        for n in nodes:
                            if n!=start and n not in descendants:close(changed[n],base[n]);non_descendants+=1
                        p=fits[target]['parameters'];sod=math.fsum(100-v['alpha']*(100-changed[v['feeder']]) for v in p)/len(p)
                        cod=min(changed[v['feeder']]+v['beta'] for v in p);cod_count+=cod<sod-1e-8
                    condition=(group,mode,start,target,fraction);mean=math.fsum(values_for_mean)/30
                    seed_means[(seed,*condition)]=mean
                    if any(all(c[x]==v for x,v in zip(('group','model','intervened_node','target','retained_fraction'),condition)) for c in protocol['focus']):
                        f=fits[target];p=f['parameters'][0]
                        metrics={m['prediction']:m['rmse'] for m in record['metrics'] if (m['fit_group'],m['model'],m['node'],m['split'])==(group,mode,target,'holdout')}
                        focus_expected[seed,*condition]=dict(target_alpha=p['alpha'],target_beta=p['beta'],target_delta=f['delta'],
                            training_beta_inactive='beta:'+p['feeder'] in f['sensitivity']['inactive_parameters'],mean_decrease=mean,
                            complete_baselines=True,local_holdout_rmse=metrics['local'],recursive_holdout_rmse=metrics['recursive'],unique_cod_responses=int(cod_count))
    assert responses==40500==report['audit']['responses']==report['audit']['available']
    assert outside==report['audit']['outside'] and extra==report['audit']['target_extrapolated']
    assert report['audit']['conditions']==270 and report['audit']['complete_condition_models']==report['audit']['planned_condition_models']==1350
    frozen_path=Path(report['frozen_report_path']);assert sha(frozen_path)==report['frozen_report_sha256'];frozen=read(frozen_path)
    for name,value in read(frozen_path.parent/'manifest.json').items():assert sha(frozen_path.parent/name)==value
    sample_to_case={(r['fit_group'],r['sample_id']):r['base_case_id'] for r in rows if r['split']=='holdout'}
    control_keys=set()
    for e in frozen['analysis']['effects']:
        j=jobs.index((e['group'],e['model']));k=pairs.index([e['intervened_node'],e['target']]);fi=axes['fractions'].index(e['retained_fraction'])
        b=cases.index(sample_to_case[e['group'],e['baseline_sample']]);key=0,j,k,fi,b
        assert key not in control_keys;control_keys.add(key)
        close(arrays['decrease'][key],-e['effect']);close(arrays['changed'][key],e['intervened']);close(arrays['forced'][key],e['forced_value'])
        close(arrays['baseline'][0,j,b,nodes.index(e['target'])],e['baseline'])
    assert len(control_keys)==8100==report['control']['responses']
    frozen_means={tuple(r[k] for k in ('group','model','intervened_node','target','retained_fraction')):r['mean_decrease'] for r in frozen['summary']}
    summaries=report['summary'];seen=set()
    for r in summaries:
        condition=tuple(r[k] for k in ('group','model','intervened_node','target','retained_fraction'));assert condition not in seen;seen.add(condition)
        values=[seed_means[(seed,*condition)] for seed in axes['seeds']]
        for name,value in [('minimum',min(values)),('median',statistics.median(values)),('maximum',max(values)),('original_mean',frozen_means[condition])]:close(r[name],value)
        assert r['planned_models']==r['complete_models']==5 and r['baseline_records']==30
        j=jobs.index(condition[:2]);k=pairs.index(list(condition[2:4]));fi=axes['fractions'].index(condition[4])
        assert r['outside_responses']==int(arrays['outside'][:,j,k,fi].sum())
        assert r['target_extrapolated_responses']==int(arrays['extrapolated'][:,j,k,fi].sum())
        close(seed_means[20261122,*condition],r['original_mean'])
    assert len(seen)==270 and len(seed_means)==1350
    for name,expected_type in [('传播范围摘要.csv','summary'),('逐种子平均响应.csv','means'),('固定示例.csv','focus')]:
        with (out/name).open(encoding='utf-8-sig',newline='') as stream:csvrows=list(csv.DictReader(stream))
        assert len(csvrows)=={'summary':270,'means':1350,'focus':10}[expected_type]
        csvseen=set()
        for ri,r in enumerate(csvrows):
            condition=(r['group'],r['model'],r['intervened_node'],r['target'],float(r['retained_fraction']))
            key=condition if expected_type=='summary' else (int(r['seed']),*condition)
            assert key not in csvseen;csvseen.add(key)
            if expected_type=='summary':expected=summaries[ri]
            elif expected_type=='means':expected=dict(mean_decrease=seed_means[key],complete_baselines=True,evaluated=30)
            else:expected=focus_expected[key]
            for field,value in expected.items():
                if isinstance(value,bool):assert r[field]==str(value)
                elif isinstance(value,(float,int)):close(float(r[field]),value)
                elif value is None:assert r[field]==''
                else:assert r[field]==value
    return dict(status='passed',responses_checked=responses,non_descendant_checks=non_descendants,
        condition_summaries=270,seed_means=1350,focus_records=10,control_responses=8100,outside=outside,target_extrapolated=extra,
        maximum_absolute_difference=maximum,additional_fitting=False,
        interpretation='independent_scalar_verification_not_real_intervention_validation')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report-dir',required=True);args=parser.parse_args()
    result=verify(args.report_dir);text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    (Path(args.report_dir)/'独立标量核验.json').write_text(text,encoding='utf-8',newline='\n');print(text)
