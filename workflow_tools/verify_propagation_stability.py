"""Independent scalar FDNA and grouped-mean verification, without the replay engine."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(directory):
    out=Path(directory);report=read(out/'propagation_report.json');source=Path(report['source']['directory'])
    for name,expected in read(out/'manifest.json').items():assert sha(out/name)==expected,name
    package=read(source/'完整包校验.json')
    for name,expected in package.items():assert sha(source/name)==expected,name
    fit_path=source/'04_FDNA拟合/workflow_report.json'
    assert sha(fit_path)==report['source']['fit_sha256']
    fit=read(fit_path);rows=read(source/'04_FDNA拟合/输入_SE记录.json')
    original_draws=Path(report['source']['draw_directory'])/'成组抽样索引.json'
    assert sha(original_draws)==report['source']['draw_sha256']
    draws=read(out/'原成组抽样索引.json');assert draws==read(original_draws)
    samples={(r['fit_group'],r['sample_id']):r for r in rows if r['split']=='holdout'}
    jobs={(j['fit_group'],j['model']):j for j in fit['jobs']};network=fit['network_snapshot']
    parents={n:sorted(e['feeder'] for e in network['edges'] if e['receiver']==n) for n in network['nodes']}
    order=[]
    while len(order)<len(parents):
        ready=[n for n in sorted(parents) if n not in order and all(p in order for p in parents[n])]
        assert ready;order.extend(ready)
    reach={(a,b):False for a in parents for b in parents}
    for e in network['edges']:reach[e['feeder'],e['receiver']]=True
    for middle in parents:
        for a in parents:
            for b in parents:reach[a,b]=reach[a,b] or (reach[a,middle] and reach[middle,b])
    maximum=0.;prediction_checks=0;statistics_checks=0

    def close(a,b):
        nonlocal maximum
        assert math.isfinite(a) and math.isfinite(b)
        difference=abs(a-b);maximum=max(maximum,difference);assert difference<=1e-9,(a,b,difference)

    def calculate(group,mode,sample_id,forced=None):
        row=samples[group,sample_id]
        inputs=row['node_values'] if fit['config_snapshot']['input_role']=='observed' else row['predictor_values']
        params={f['node']:f['fit']['receivers'][0] for f in jobs[group,mode]['local_fits']}
        values={}
        for node in order:
            if forced and node==forced[0]:value=forced[1]
            elif not parents[node]:value=inputs[node]
            else:
                p=params[node];terms=p['parameters']
                sod=math.fsum(100-t['alpha']*(100-values[t['feeder']]) for t in terms)/len(terms)
                cod=min(values[t['feeder']]+t['beta'] for t in terms)
                value=min(sod,cod)+(p.get('delta') or 0.)
            values[node]=value
        return values

    baselines={};changed={};grouped=defaultdict(dict);effect_keys=set()
    effects=report['analysis']['effects']
    assert len(effects)==report['audit']['responses']==8100
    for e in effects:
        key=e['group'],e['model'],e['baseline_sample']
        if key not in baselines:baselines[key]=calculate(*key)
        baseline=baselines[key];change_key=(*key,e['intervened_node'],e['retained_fraction'])
        forced=baseline[e['intervened_node']]*e['retained_fraction']
        if change_key not in changed:changed[change_key]=calculate(*key,forced=(e['intervened_node'],forced))
        value=changed[change_key][e['target']]
        for actual,expected in [(e['baseline'],baseline[e['target']]),(e['forced_value'],forced),
                                (e['intervened'],value),(e['effect'],value-baseline[e['target']])]:
            close(actual,expected);prediction_checks+=1
        assert e['outside']==(not 0<=value<=100) and not e['blocked']
        assert reach[e['intervened_node'],e['target']] and e['intervened_node']!=e['target']
        identity=(*change_key,e['target']);assert identity not in effect_keys;effect_keys.add(identity)
        summary_key=tuple(e[k] for k in ('group','model','intervened_node','target','retained_fraction'))
        base=samples[e['group'],e['baseline_sample']]['base_case_id']
        assert base not in grouped[summary_key];grouped[summary_key][base]=-e['effect']
    invariant_checks=0
    for check in report['analysis']['checks']:
        assert check['status']=='checked'
        key=check['group'],check['model'],check['baseline_sample']
        if key not in baselines:baselines[key]=calculate(*key)
        baseline=baselines[key];node=check['node'];fraction=check['retained_fraction']
        change_key=(*key,node,fraction)
        if change_key not in changed:changed[change_key]=calculate(*key,forced=(node,baseline[node]*fraction))
        for n in order:
            if n!=node and not reach[node,n]:
                close(changed[change_key][n],baseline[n]);invariant_checks+=1
    with np.load(out/'均值重采样结果.npz',allow_pickle=False) as archive:means=archive['mean_decreases']
    assert means.shape==(2000,270) and len(report['summary'])==270
    cases=report['base_cases'];indices=draws['indices'];mean_checks=0

    def quantile(values,q):
        ordered=sorted(values);position=(len(ordered)-1)*q;low=math.floor(position);high=math.ceil(position)
        return ordered[low]+(position-low)*(ordered[high]-ordered[low])

    for ci,row in enumerate(report['summary']):
        key=tuple(row[k] for k in ('group','model','intervened_node','target','retained_fraction'))
        assert set(grouped[key])==set(cases) and len(cases)==row['baseline_records']==30
        values=[grouped[key][b] for b in cases]
        expected=dict(mean_decrease=math.fsum(values)/30,min_decrease=min(values),max_decrease=max(values),
            p10_decrease=quantile(values,.1),p90_decrease=quantile(values,.9))
        resampled=[]
        for di,draw in enumerate(indices):
            value=math.fsum(values[i] for i in draw)/30
            close(means[di,ci],value);mean_checks+=1;resampled.append(value)
        expected.update(mean_resample_q025=quantile(resampled,.025),mean_resample_median=quantile(resampled,.5),
                        mean_resample_q975=quantile(resampled,.975))
        for field,value in expected.items():close(row[field],value);statistics_checks+=1
    return dict(status='passed',source_files_verified=len(package),response_values_checked=prediction_checks,
        non_descendant_values_checked=invariant_checks,resampled_means_checked=mean_checks,
        summary_values_checked=statistics_checks,maximum_absolute_difference=maximum,refitted=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='独立标量核验全留出基准传播和均值重采样')
    parser.add_argument('--report-dir',type=Path,required=True);parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=verify(args.report_dir);text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        if args.output.exists():raise ValueError('复核记录已存在')
        args.output.write_text(text,encoding='utf-8')
    print(text)
