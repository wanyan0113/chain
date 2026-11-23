"""Independent checks of training-draw weights, all-start losses, predictions and ranges."""
import argparse
from collections import Counter
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf-8')).hexdigest()


def verify(directory):
    out=Path(directory);report=read(out/'refit_report.json');source=Path(report['source']['directory'])
    for name,expected in read(out/'manifest.json').items():assert sha(out/name)==expected,name
    package=read(source/'完整包校验.json')
    for name,expected in package.items():assert sha(source/name)==expected,name
    assert sha(source/'04_FDNA拟合/workflow_report.json')==report['source']['fit_sha256']
    original=read(source/'04_FDNA拟合/workflow_report.json');rows=read(source/'04_FDNA拟合/输入_SE记录.json')
    draws=read(out/'训练成组抽样索引.json');protocol=report['protocol']
    training=draws['training_base_cases'];held=draws['holdout_base_cases'];indices=np.asarray(draws['indices'])
    assert len(training)==90 and len(held)==30 and not set(training)&set(held) and indices.shape==(50,90)
    generator=np.random.Generator(np.random.PCG64(protocol['bootstrap_seed']))
    np.testing.assert_array_equal(indices,generator.integers(0,90,size=(50,90)))
    lookup={(r['fit_group'],r['base_case_id']):r for r in rows}
    sample_lookup={(r['fit_group'],r['sample_id']):r for r in rows}
    jobs={(j['fit_group'],j['model']):j for j in original['jobs']}
    originals={(j['fit_group'],j['model'],l['node']):l for j in original['jobs'] for l in j['local_fits']}
    control=read(out/'控制复跑.json')['record']
    assert control['input_sha256']==report['source']['input_sha256']
    for f in control['fits']:
        old=originals[f['fit_group'],f['model'],f['node']]['fit']['receivers'][0]
        assert f['parameters']==old['parameters'] and f['delta']==old['delta']
    assert original['config_snapshot']['input_role']=='observed'
    parents={n:sorted(e['feeder'] for e in original['network_snapshot']['edges'] if e['receiver']==n) for n in original['network_snapshot']['nodes']}
    order=[]
    while len(order)<len(parents):
        ready=[n for n in sorted(parents) if n not in order and all(p in order for p in parents[n])]
        assert ready;order.extend(ready)
    maximum=0.;loss_checks=0;prediction_checks=0;selection_checks=0;all_fits=[];all_metrics=[]

    def close(a,b):
        nonlocal maximum
        if a is None or b is None:
            assert a is None and b is None;return
        assert math.isfinite(a) and math.isfinite(b)
        error=abs(a-b);maximum=max(maximum,error);assert error<=1e-8,(a,b,error)

    def forward(values,parameters,delta):
        sod=math.fsum(100-p['alpha']*(100-values[p['feeder']]) for p in parameters)/len(parameters)
        cod=min(values[p['feeder']]+p['beta'] for p in parameters)
        return min(sod,cod)+(delta or 0.)

    paths=sorted((out/'逐次记录').glob('repetition_*.json'));assert len(paths)==50
    for ri,path in enumerate(paths,1):
        record=read(path);assert record['repetition']==ri
        assert record['distinct_training_base_cases']==len(set(indices[ri-1].tolist()))
        assert len(record['fits'])==36 and len(record['holdout_points'])==1080
        fit_lookup={(f['fit_group'],f['model'],f['node']):f for f in record['fits']}
        assert len(fit_lookup)==36
        for key,f in fit_lookup.items():
            group,mode,node=key;source_local=originals[key]['fit']
            assert f['settings_sha256']==digest(source_local['settings_snapshot'])
            assert sorted(tuple(a['initial']) for a in f['attempts'])==sorted(tuple(a['initial']) for a in source_local['receivers'][0]['attempts'])
            assert f['protocol_sha256']==report['protocol_sha256']
            assert f['source_input_sha256']==report['source']['input_sha256']
            if not f['parameters']:
                assert f['status']!='fitted';continue
            feeder_order=[p['feeder'] for p in f['parameters']];n=len(feeder_order)
            assert set(feeder_order)==set(parents[node])
            train_rows=[lookup[group,training[int(index)]] for index in indices[ri-1]]
            assert all(r['split']=='train' and r['base_case_id'] not in held for r in train_rows)
            for attempt in f['attempts']:
                vector=attempt['parameters'];delta=vector[-1] if mode=='free_delta' else None
                parameters=[dict(feeder=p,alpha=vector[i],beta=vector[n+i]) for i,p in enumerate(feeder_order)]
                rmse=math.sqrt(math.fsum((forward(r['node_values'],parameters,delta)-r['node_values'][node])**2 for r in train_rows)/90)
                close(attempt['rmse'],rmse);loss_checks+=1
            assert len(f['attempts'])==6
            chosen=f['attempts'][f['attempt_selection']['index']]
            actual=[p['alpha'] for p in f['parameters']]+[p['beta'] for p in f['parameters']]+([f['delta']] if mode=='free_delta' else [])
            for a,b in zip(actual,chosen['parameters']):close(a,b);selection_checks+=1
            assert len(actual)==len(chosen['parameters'])
            sensitivity=f['sensitivity']
            if f['status']=='fitted':
                assert ('LOCAL_RANK_DEFICIENT' in f['flags'])==(sensitivity['local_jacobian_rank']<sensitivity['parameter_count'])
                assert ('INACTIVE_PARAMETERS' in f['flags'])==bool(sensitivity['inactive_parameters'])
                if f['delta_bounds']:
                    assert ('DELTA_AT_BOUNDARY' in f['flags'])==(min(abs(f['delta']-b) for b in f['delta_bounds'])<=1e-6)
        cached={}
        for p in record['holdout_points']:
            group,mode=p['fit_group'],p['model'];raw=sample_lookup[group,p['sample_id']]
            assert raw['split']=='holdout' and raw['base_case_id'] in held
            assert p['target']==raw['node_values'].get(p['node'])
            cache_key=group,mode,p['sample_id']
            if cache_key not in cached:
                values={}
                for node in order:
                    if not parents[node]:values[node]=raw['node_values'].get(node)
                    else:
                        f=fit_lookup[group,mode,node]
                        values[node]=forward(values,f['parameters'],f['delta']) if f['status']=='fitted' and all(values[x] is not None for x in parents[node]) else None
                cached[cache_key]=values
            fit=fit_lookup[group,mode,p['node']]
            local=forward(raw['node_values'],fit['parameters'],fit['delta']) if fit['status']=='fitted' else None
            close(p['local_prediction'],local);close(p['recursive_prediction'],cached[cache_key][p['node']]);prediction_checks+=2
        for m in record['metrics']:
            if m['split']!='holdout':continue
            own=[p for p in record['holdout_points'] if (p['fit_group'],p['model'],p['node'])==(m['fit_group'],m['model'],m['node'])]
            valid=[p for p in own if p['target'] is not None and p[m['prediction']+'_prediction'] is not None]
            assert m['available']==len(own)==30 and m['evaluated']==len(valid)
            rmse=math.sqrt(math.fsum((p[m['prediction']+'_prediction']-p['target'])**2 for p in valid)/len(valid)) if valid else None
            close(m['rmse'],rmse)
        all_fits.extend(record['fits']);all_metrics.extend(record['metrics'])
    grouped=defaultdict(list)
    for f in all_fits:grouped[f['fit_group'],f['model'],f['node']].append(f)
    summary_checks=0
    for summary in report['summary']['parameters']:
        records=grouped[summary['fit_group'],summary['model'],summary['node']];values=[]
        for f in records:
            if f['status']!='fitted':continue
            if summary['parameter']=='delta':values.append(f['delta'])
            else:
                kind,feeder=summary['parameter'].split(':',1);values.append(next(p[kind] for p in f['parameters'] if p['feeder']==feeder))
        assert summary['planned']==len(records)==50 and summary['evaluated']==len(values)
        for field,value in [('minimum',min(values)),('median',float(np.median(values))),('maximum',max(values))]:close(summary[field],value);summary_checks+=1
    for summary in report['summary']['prediction']:
        records=[m for m in all_metrics if m['split']=='holdout' and all(m[k]==summary[k] for k in ('fit_group','model','node','prediction'))]
        valid=[m['rmse'] for m in records if m['evaluated']>0 and m['rmse'] is not None]
        assert len(records)==50 and summary['evaluated']==len(valid)
        for field,value in [('minimum',min(valid) if valid else None),('median',float(np.median(valid)) if valid else None),('maximum',max(valid) if valid else None)]:
            close(summary[field],value);summary_checks+=1
    for summary in report['summary']['diagnostics']:
        records=grouped[summary['fit_group'],summary['model'],summary['node']]
        assert len(records)==summary['planned']==50
        assert summary['failed']==sum(f['status']!='fitted' for f in records)
        assert summary['flag_counts']==dict(Counter(flag for f in records for flag in f['flags']))
    assert report['audit']['flag_counts']==dict(Counter(flag for f in all_fits for flag in f['flags']))
    assert len(all_fits)==1800
    return dict(status='passed',source_files_verified=len(package),repetitions=50,local_fits=1800,
        all_start_training_losses_checked=loss_checks,selected_parameter_values_checked=selection_checks,
        holdout_prediction_values_checked=prediction_checks,summary_values_checked=summary_checks,
        maximum_absolute_difference=maximum,parameter_confidence_interval=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='独立标量核验训练成组重拟合、起点损失与固定留出预测')
    parser.add_argument('--report-dir',type=Path,required=True);parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=verify(args.report_dir);text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        if args.output.exists():raise ValueError('复核记录已存在')
        args.output.write_text(text,encoding='utf-8')
    print(text)
