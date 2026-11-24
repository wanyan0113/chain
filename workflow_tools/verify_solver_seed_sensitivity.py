"""Independent scalar audit: no optimizer or sensitivity-summary implementation calls."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
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
    if __debug__ is False:raise ValueError('独立核验不能使用-O')
    out=Path(directory);report=read(out/'seed_report.json');protocol=read(out/'预登记方案.json')
    inventory=read(out/'manifest.json')
    assert set(inventory)=={p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file() and p.name not in ('manifest.json','独立标量核验.json')}
    for name,expected in inventory.items():assert sha(out/name)==expected,name
    assert protocol==report['protocol'] and digest(protocol)==report['protocol_sha256']
    seeds=[20261122,2026112401,2026112402,2026112403,2026112404]
    assert protocol['seeds']==seeds and protocol['control_seed']==seeds[0]
    assert protocol['starts']==6 and protocol['solver']=='L-BFGS-B' and protocol['max_nfev']==1500
    assert protocol['select_seed_using_holdout'] is False and protocol['warm_start'] is False and protocol['output_clipping'] is False
    source=Path(report['source']['directory']);original=read(source/'04_FDNA拟合/workflow_report.json')
    assert sha(source/'04_FDNA拟合/workflow_report.json')==report['source']['fit_sha256']
    for name,expected in read(source/'完整包校验.json').items():assert sha(source/name)==expected
    rows=read(source/'04_FDNA拟合/输入_SE记录.json');network=original['network_snapshot'];config=original['config_snapshot']
    assert digest(rows)==original['data_sha256']==report['source']['data_sha256']
    assert digest(dict(network=network,rows=rows,config=config))==original['input_sha256']==report['source']['input_sha256']
    train={r['base_case_id'] for r in rows if r['split']=='train'};held={r['base_case_id'] for r in rows if r['split']=='holdout'}
    assert len(train)==90 and len(held)==30 and not train&held and len(rows)==360
    parents={n:sorted(e['feeder'] for e in network['edges'] if e['receiver']==n) for n in network['nodes']}
    assert all(len(v)<=1 for v in parents.values())
    order=[]
    while len(order)<len(parents):
        ready=[n for n in sorted(parents) if n not in order and all(p in order for p in parents[n])];assert ready;order.extend(ready)
    lookup={(r['fit_group'],r['sample_id']):r for r in rows}
    source_fits={(j['fit_group'],j['model'],f['node']):f for j in original['jobs'] for f in j['local_fits']}
    source_points={(j['fit_group'],j['model'],p['node'],p['sample_id']):p for j in original['jobs'] for p in j['points'] if p['split']=='holdout'}
    maximum=0.;counts=Counter();allfits=defaultdict(list);allmetrics=defaultdict(list);allpoints=defaultdict(list);flags=Counter()
    def close(a,b):
        nonlocal maximum
        if a is None or b is None:assert a is b;return
        assert math.isfinite(a) and math.isfinite(b)
        difference=abs(a-b);maximum=max(maximum,difference);assert difference<=1e-8,(a,b)
    def forward(values,f):
        p=f['parameters'];sod=math.fsum(100-v['alpha']*(100-values[v['feeder']]) for v in p)/len(p)
        cod=min(values[v['feeder']]+v['beta'] for v in p)
        return min(sod,cod)+(f['delta'] or 0.)
    for si,seed in enumerate(seeds):
        record=read(out/f'逐种子记录/seed_{seed}.json')
        expected=deepcopy(config)
        for setting in expected['models'].values():setting['seed']=seed
        assert record['seed']==seed and record['repetition']==si and record['config_snapshot']==expected
        assert record['data_sha256']==original['data_sha256']
        assert record['input_sha256']==digest(dict(network=network,rows=rows,config=expected))
        fits={(f['fit_group'],f['model'],f['node']):f for f in record['fits']}
        assert set(fits)==set(source_fits) and len(record['fits'])==36
        for key,f in fits.items():
            group,mode,node=key;setting=deepcopy(expected['models'][mode])
            if 'delta_bounds' in setting:setting['delta_bounds']={node:setting['delta_bounds'][node]}
            assert f['settings_sha256']==digest(setting) and f['protocol_sha256']==digest(protocol)
            assert f['source_input_sha256']==original['input_sha256']
            assert [p['feeder'] for p in f['parameters']]==parents[node]
            attempts=f['attempts'];dim=2+int(mode=='free_delta');assert len(attempts)==6
            starts=[[.5]*dim]+np.random.Generator(np.random.PCG64(seed)).uniform(.01,.99,(5,dim)).tolist()
            assert sorted(tuple(v) for v in starts)==sorted(tuple(a['initial']) for a in attempts)
            assert Counter(a['initial_source'] for a in attempts)=={'neutral':1,'seeded_random':5}
            assert [a['rmse'] for a in attempts]==sorted(a['rmse'] for a in attempts)
            for a in attempts:
                vector=a['parameters'];assert len(vector)==dim and 0<=vector[0]<=1 and 0<=vector[1]<=100
                candidate=dict(parameters=[dict(feeder=parents[node][0],alpha=vector[0],beta=vector[1])],delta=vector[2] if dim==3 else None)
                if dim==3:assert setting['delta_bounds'][node][0]<=vector[2]<=setting['delta_bounds'][node][1]
                training=[r for r in rows if r['fit_group']==group and r['split']=='train'];assert len(training)==90
                rmse=math.sqrt(math.fsum((forward(r['node_values'],candidate)-r['node_values'][node])**2 for r in training)/90)
                close(a['rmse'],rmse);counts['attempt_training_losses']+=1
            tolerance=8*math.ulp(max(1.,abs(attempts[0]['rmse'])))
            selected=next((i for i,a in enumerate(attempts) if a['success'] and a['rmse']-attempts[0]['rmse']<=tolerance),0)
            assert f['attempt_selection']['index']==selected
            actual=[f['parameters'][0]['alpha'],f['parameters'][0]['beta']]+([f['delta']] if dim==3 else [])
            assert actual==attempts[selected]['parameters'];assert (f['status']=='fitted')==attempts[selected]['success']
            counts['selected_parameter_values']+=len(actual)
            if seed==seeds[0]:
                old=source_fits[key]['fit']['receivers'][0]
                assert f['parameters']==old['parameters'] and f['delta']==old['delta']
            allfits[key].append(f);flags.update(f['flags'])
        predictions={}
        for group,mode in {(g,m) for g,m,n in fits}:
            for (g,sample),raw in lookup.items():
                if g!=group:continue
                values={}
                for node in order:
                    f=fits.get((group,mode,node))
                    values[node]=raw['node_values'][node] if not parents[node] else forward(values,f) if f['status']=='fitted' and all(values[x] is not None for x in parents[node]) else None
                for node in (n for n in order if parents[n]):
                    f=fits[group,mode,node]
                    predictions[group,mode,node,sample]=dict(local=forward(raw['node_values'],f) if f['status']=='fitted' else None,recursive=values[node])
        seen=set()
        for p in record['holdout_points']:
            key=(p['fit_group'],p['model'],p['node'],p['sample_id']);assert key not in seen;seen.add(key)
            raw=lookup[p['fit_group'],p['sample_id']];assert raw['split']==p['split']=='holdout' and raw['base_case_id'] in held
            close(p['target'],raw['node_values'][p['node']])
            for kind in ('local','recursive'):
                close(p[kind+'_prediction'],predictions[key][kind]);counts['holdout_prediction_values']+=1
                allpoints[(*key,kind)].append(p[kind+'_prediction'])
                if seed==seeds[0]:close(p[kind+'_prediction'],source_points[key][kind+'_prediction'])
        assert len(seen)==1080
        seen=set()
        for m in record['metrics']:
            key=(m['fit_group'],m['model'],m['node'],m['split'],m['prediction']);assert key not in seen;seen.add(key)
            group,mode,node,split,kind=key;own=[r for r in rows if r['fit_group']==group and r['split']==split]
            ps=[(predictions[group,mode,node,r['sample_id']][kind],r['node_values'][node]) for r in own]
            valid=[(x,y) for x,y in ps if x is not None]
            assert m['available']==len(own) and m['evaluated']==len(valid)
            assert m['outside']==sum(not 0<=x<=100 for x,y in valid)
            close(m['rmse'],math.sqrt(math.fsum((x-y)**2 for x,y in valid)/len(valid)) if valid else None)
            allmetrics[key].append(m);counts['training_holdout_rmse']+=1
        assert len(seen)==144
    def check_range(stored,values):
        expected=dict(minimum=min(values),median=statistics.median(values),maximum=max(values),span=max(values)-min(values)) if values else dict.fromkeys(('minimum','median','maximum','span'))
        for name,value in expected.items():close(stored[name],value);counts['summary_values']+=1
    summary=report['summary']
    def keys_of(name,fields):
        rows=summary[name];keys={tuple(r[k] for k in fields) for r in rows}
        assert len(keys)==len(rows);return keys
    assert keys_of('diagnostics',('fit_group','model','node'))==set(allfits)
    assert keys_of('metrics',('fit_group','model','node','split','prediction'))==set(allmetrics)
    assert keys_of('holdout_points',('fit_group','model','node','sample_id','prediction'))==set(allpoints)
    parameter_keys={(g,m,n,kind+':'+p['feeder']) for (g,m,n),fs in allfits.items() for p in fs[0]['parameters'] for kind in ('alpha','beta')}
    parameter_keys|={(g,m,n,'delta') for (g,m,n),fs in allfits.items() if fs[0]['delta'] is not None}
    assert keys_of('parameters',('fit_group','model','node','parameter'))==parameter_keys
    for r in report['summary']['parameters']:
        fits=allfits[r['fit_group'],r['model'],r['node']];valid=[f for f in fits if f['status']=='fitted']
        name=r['parameter'].split(':')[0];vs=[f['delta'] if name=='delta' else f['parameters'][0][name] for f in valid]
        assert r['planned']==5 and r['evaluated']==len(vs);check_range(r,vs)
    for r in report['summary']['diagnostics']:
        fits=allfits[r['fit_group'],r['model'],r['node']];valid=[f for f in fits if f['status']=='fitted']
        assert r['planned']==5 and r['fitted']==len(valid) and r['failed']==5-len(valid)
        assert r['flags']==dict(Counter(flag for f in fits for flag in f['flags']))
        check_range(r['selected_training_rmse'],[f['attempts'][f['attempt_selection']['index']]['rmse'] for f in valid])
    for r in report['summary']['metrics']:
        ms=allmetrics[r['fit_group'],r['model'],r['node'],r['split'],r['prediction']];valid=[m for m in ms if m['rmse'] is not None and m['evaluated']>0]
        assert r['planned']==5 and r['evaluated']==len(valid) and r['unavailable']==5-len(valid)
        assert r['outside']==sum(m['outside'] for m in ms);check_range(r,[m['rmse'] for m in valid])
    for r in report['summary']['holdout_points']:
        ps=allpoints[r['fit_group'],r['model'],r['node'],r['sample_id'],r['prediction']];vs=[v for v in ps if v is not None]
        assert r['planned']==5 and r['evaluated']==len(vs);check_range(r,vs)
    for name,key in [('参数范围.csv','parameters'),('训练损失与诊断.csv','diagnostics'),('训练与留出误差.csv','metrics'),('逐留出预测范围.csv','holdout_points')]:
        with (out/name).open(encoding='utf-8-sig',newline='') as stream:csvrows=list(csv.DictReader(stream))
        assert len(csvrows)==len(report['summary'][key])
        for stored,expected in zip(csvrows,report['summary'][key]):
            assert set(stored)==set(expected)
            for field,value in expected.items():
                if isinstance(value,(dict,list)):assert json.loads(stored[field])==value
                elif value is None:assert stored[field]==''
                elif isinstance(value,(int,float)):close(float(stored[field]),value)
                else:assert stored[field]==value
                counts['csv_fields']+=1
    assert report['audit']['local_fits']==180 and report['audit']['flags']==dict(flags)
    assert report['audit']['failed_local_fits']==sum(f['status']!='fitted' for fs in allfits.values() for f in fs)
    result=dict(status='passed',seeds_verified=5,local_fits_verified=180,source_files_verified=94,
        checks=dict(counts),maximum_absolute_difference=maximum,
        interpretation='scalar_numerical_recheck_not_new_fitting_or_real_validation')
    print(json.dumps(result,ensure_ascii=False,indent=2));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report-dir',required=True);args=parser.parse_args()
    result=verify(args.report_dir)
    (Path(args.report_dir)/'独立标量核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
