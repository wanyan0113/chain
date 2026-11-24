"""Scalar verification of common-clamp responses and paired algebraic contrasts."""
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
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf-8')).hexdigest()


def verify(directory):
    if not __debug__:raise ValueError('核验不能使用-O')
    out=Path(directory);report=read(out/'absolute_report.json');inventory=read(out/'manifest.json')
    assert set(inventory)=={p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file() and p.name not in ('manifest.json','独立标量核验.json')}
    for name,value in inventory.items():assert sha(out/name)==value,name
    protocol=read(out/'预登记方案.json');assert protocol==report['protocol'] and digest(protocol)==report['protocol_sha256']
    assert protocol['reference_seed']==20261122 and protocol['seeds']==[20261122,2026112401,2026112402,2026112403,2026112404]
    assert protocol['fractions']==[0,.25,.5,.75,1] and protocol['additional_fitting'] is False and protocol['model_selection'] is False
    source=Path(report['source_relative_directory']);assert sha(source/'manifest.json')==report['source_manifest_sha256']
    for name,value in read(source/'manifest.json').items():assert sha(source/name)==value
    old=read(source/'seed_propagation_report.json');seed_dir=Path(old['source_seed_directory'])
    assert sha(seed_dir/'manifest.json')==old['source_manifest_sha256']
    for name,value in read(seed_dir/'manifest.json').items():assert sha(seed_dir/name)==value
    seed_report=read(seed_dir/'seed_report.json');original=Path(seed_report['source']['directory'])
    for name,value in read(original/'完整包校验.json').items():assert sha(original/name)==value
    rows=read(original/'04_FDNA拟合/输入_SE记录.json');fit=read(original/'04_FDNA拟合/workflow_report.json');network=fit['network_snapshot']
    assert digest(rows)==seed_report['source']['data_sha256'] and fit['input_sha256']==seed_report['source']['input_sha256']
    nodes=sorted(network['nodes']);parents={n:sorted(e['feeder'] for e in network['edges'] if e['receiver']==n) for n in nodes};order=[];pairs=[]
    while len(order)<len(nodes):
        ready=[n for n in nodes if n not in order and all(p in order for p in parents[n])];assert ready;order.extend(ready)
    for start in nodes:
        pending=[n for n in nodes if start in parents[n]];seen=set()
        while pending:
            n=pending.pop()
            if n not in seen:seen.add(n);pending.extend(x for x in nodes if n in parents[x])
        pairs.extend([start,n] for n in sorted(seen))
    records=[read(seed_dir/f'逐种子记录/seed_{seed}.json') for seed in protocol['seeds']]
    for seed in protocol['seeds']:
        name=f'逐种子记录/seed_{seed}.json';assert sha(seed_dir/name)==old['source_record_sha256'][name]
    jobs=sorted({(f['fit_group'],f['model']) for f in records[0]['fits']});cases=sorted({r['base_case_id'] for r in rows if r['split']=='holdout'})
    training=sorted({r['base_case_id'] for r in rows if r['split']=='train'});assert len(cases)==30 and len(training)==90 and not set(cases)&set(training)
    axes=report['axes'];assert axes==old['axes'] and axes['seeds']==protocol['seeds'] and axes['fractions']==protocol['fractions']
    assert axes['nodes']==nodes and axes['pairs']==pairs and axes['jobs']==[list(j) for j in jobs] and axes['base_cases']==cases
    lookup={(r['fit_group'],r['base_case_id']):r for r in rows};assert len(lookup)==len(rows)==360
    with np.load(out/'共同绝对值响应数组.npz',allow_pickle=False) as a:fixed={k:a[k] for k in a.files}
    with np.load(source/'逐种子逐基准数组.npz',allow_pickle=False) as a:relative={k:a[k] for k in a.files}
    assert set(fixed)=={'decrease','changed','forced','baseline','valid','outside','extrapolated','negative_decrease','reference_exceeds_own'}
    for name,a in fixed.items():
        assert a.shape==((5,6,30,9) if name=='baseline' else (5,6,9,5,30))
        assert a.dtype==np.dtype(bool if name in ('valid','outside','extrapolated','negative_decrease','reference_exceeds_own') else float)
    maximum=0.;checked=0;negative=0;exceeds=0;outside=0;extra=0;non_descendants=0;means={};controls=0;zero_controls=0;root_controls=0
    def close(a,b):
        nonlocal maximum
        if a is None or b is None:assert a is b;return
        assert math.isfinite(a) and math.isfinite(b)
        d=abs(a-b);maximum=max(maximum,d);assert d<=1e-8,(a,b)
    def forward(fits,roots,forced=None):
        values=dict(roots)
        for n in order:
            if forced and n==forced[0]:values[n]=forced[1]
            elif parents[n]:
                f=fits[n];params=f['parameters'];sod=math.fsum(100-p['alpha']*(100-values[p['feeder']]) for p in params)/len(params)
                cod=min(values[p['feeder']]+p['beta'] for p in params);values[n]=min(sod,cod)+(f['delta'] or 0.)
        return values
    for si,(seed,record) in enumerate(zip(axes['seeds'],records)):
        assert record['seed']==seed
        for j,(group,mode) in enumerate(jobs):
            fits={f['node']:f for f in record['fits'] if (f['fit_group'],f['model'])==(group,mode)}
            control_fits={f['node']:f for f in records[0]['fits'] if (f['fit_group'],f['model'])==(group,mode)}
            assert set(fits)=={n for n in nodes if parents[n]} and all(f['status']=='fitted' for f in fits.values())
            bounds={(n,p):(min(lookup[group,c]['node_values'][p] for c in training),max(lookup[group,c]['node_values'][p] for c in training)) for n in nodes for p in parents[n]}
            for k,(start,target) in enumerate(pairs):
                descendants={n for s,n in pairs if s==start}
                for fi,fraction in enumerate(axes['fractions']):
                    values={n:[] for n in ('fixed','relative','reference_contribution','baseline_contrast','output_contrast')}
                    for b,case in enumerate(cases):
                        raw=lookup[group,case];assert raw['split']=='holdout';roots={n:raw['node_values'][n] for n in nodes if not parents[n]}
                        base=forward(fits,roots);refbase=forward(control_fits,roots)
                        clamp=refbase[start]*fraction;state=forward(fits,roots,(start,clamp));refstate=forward(control_fits,roots,(start,clamp))
                        relative_state=forward(fits,roots,(start,base[start]*fraction));key=si,j,k,fi,b
                        assert 0<=base[start]<=100 and 0<=refbase[start]<=100 and fixed['valid'][key] and relative['valid'][key]
                        for ni,n in enumerate(nodes):close(fixed['baseline'][si,j,b,ni],base[n]);close(relative['baseline'][si,j,b,ni],base[n])
                        decrease=base[target]-state[target];reld=base[target]-relative_state[target];baseline_diff=base[target]-refbase[target];output_diff=state[target]-refstate[target]
                        close(fixed['forced'][key],clamp);close(fixed['changed'][key],state[target]);close(fixed['decrease'][key],decrease)
                        close(relative['changed'][key],relative_state[target]);close(relative['decrease'][key],reld)
                        close((decrease-(refbase[target]-refstate[target])),baseline_diff-output_diff)
                        close(reld-decrease,state[target]-relative_state[target])
                        is_out=not 0<=state[target]<=100;is_extra=any(state[p]<bounds[target,p][0]-1e-8 or state[p]>bounds[target,p][1]+1e-8 for p in parents[target])
                        is_negative=decrease < -1e-8;is_exceeds=clamp>base[start]+1e-8
                        for name,expected in [('outside',is_out),('extrapolated',is_extra),('negative_decrease',is_negative),('reference_exceeds_own',is_exceeds)]:assert bool(fixed[name][key])==expected
                        outside+=is_out;extra+=is_extra;negative+=is_negative;exceeds+=is_exceeds;checked+=1
                        if si==0 or fraction==0 or not parents[start]:
                            close(decrease,reld);close(state[target],relative_state[target])
                            controls+=si==0;zero_controls+=fraction==0;root_controls+=not parents[start]
                        for n in nodes:
                            if n!=start and n not in descendants:close(state[n],base[n]);non_descendants+=1
                        for name,v in [('fixed',decrease),('relative',reld),('reference_contribution',reld-decrease),('baseline_contrast',baseline_diff),('output_contrast',output_diff)]:values[name].append(v)
                    means[(seed,group,mode,start,target,fraction)]={name:math.fsum(vs)/30 for name,vs in values.items()}
    assert checked==40500==report['audit']['responses']==report['audit']['available']
    assert negative==report['audit']['negative_decreases'] and exceeds==report['audit']['reference_exceeds_own']
    assert outside==report['audit']['outside'] and extra==report['audit']['extrapolated']
    summaries=report['summary'];seen=set()
    for r in summaries:
        condition=tuple(r[k] for k in ('group','model','intervened_node','target','retained_fraction'));assert condition not in seen;seen.add(condition)
        assert r['planned_models']==r['complete_models']==5 and r['baseline_records']==30
        for name in ('fixed','relative','reference_contribution','baseline_contrast','output_contrast'):
            vs=[means[seed,*condition][name] for seed in axes['seeds']]
            for stat,v in [('minimum',min(vs)),('median',statistics.median(vs)),('maximum',max(vs)),('span',max(vs)-min(vs))]:close(r[name+'_'+stat],v)
    assert len(seen)==270 and len(means)==1350
    for filename,kind in [('相对与绝对传播范围.csv','summary'),('逐种子配对分解.csv','means')]:
        with (out/filename).open(encoding='utf-8-sig',newline='') as stream:csvrows=list(csv.DictReader(stream))
        assert len(csvrows)==(270 if kind=='summary' else 1350);csvseen=set()
        for ri,r in enumerate(csvrows):
            condition=(r['group'],r['model'],r['intervened_node'],r['target'],float(r['retained_fraction']))
            key=condition if kind=='summary' else (int(r['seed']),*condition);assert key not in csvseen;csvseen.add(key)
            expected=summaries[ri] if kind=='summary' else dict(complete_baselines=True,evaluated=30,**means[key])
            for name,v in expected.items():
                if isinstance(v,bool):assert r[name]==str(v)
                elif isinstance(v,(int,float)):close(float(r[name]),v)
                else:assert r[name]==v
    return dict(status='passed',absolute_responses=checked,paired_relative_responses=checked,non_descendant_checks=non_descendants,
        reference_seed_controls=controls,zero_controls=zero_controls,root_controls=root_controls,condition_summaries=270,seed_means=1350,
        negative_decreases=negative,reference_exceeds_own=exceeds,outside=outside,extrapolated=extra,maximum_absolute_difference=maximum,
        additional_fitting=False,interpretation='model_algebra_and_shared_input_comparison_not_real_causality')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report-dir',required=True);args=parser.parse_args();result=verify(args.report_dir)
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n';(Path(args.report_dir)/'独立标量核验.json').write_text(text,encoding='utf-8',newline='\n');print(text)
