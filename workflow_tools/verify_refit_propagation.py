"""Independent scalar replay of every saved refitted propagation response."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(directory):
    out=Path(directory);report=read(out/'propagation_report.json')
    for name,value in read(out/'manifest.json').items():assert sha(out/name)==value,name
    source=Path(report['source_refit_directory']);assert sha(source/'manifest.json')==report['source_manifest_sha256']
    refit=read(source/'refit_report.json');original=Path(refit['source']['directory'])
    # Check upstream immutable data bytes as well as the refit inputs.
    for name,value in read(original/'完整包校验.json').items():assert sha(original/name)==value,name
    for name,value in read(source/'manifest.json').items():assert sha(source/name)==value,name
    rows=read(original/'04_FDNA拟合/输入_SE记录.json');draws=read(source/'训练成组抽样索引.json')
    network=read(original/'04_FDNA拟合/workflow_report.json')['network_snapshot'];nodes=report['axes']['nodes']
    parents={n:sorted(e['feeder'] for e in network['edges'] if e['receiver']==n) for n in nodes}
    order=[]
    while len(order)<len(nodes):
        ready=[n for n in nodes if n not in order and all(p in order for p in parents[n])]
        assert ready;order.extend(ready)
    roots=[n for n in nodes if not parents[n]];lookup={(r['fit_group'],r['base_case_id']):r for r in rows}
    with np.load(out/'逐模型逐基准数组.npz') as archive:
        arrays={name:archive[name] for name in archive.files}
    maximum=0.;checked=0;outside=0;extra=0
    def close(a,b):
        nonlocal maximum
        assert math.isfinite(a) and math.isfinite(b)
        error=abs(a-b);maximum=max(maximum,error);assert error<1e-8,(a,b)
    def forward(fits,values,forced=None):
        values=dict(values)
        for n in order:
            if forced and n==forced[0]:values[n]=forced[1]
            elif parents[n]:
                f=fits[n];p=f['parameters']
                sod=math.fsum(100-x['alpha']*(100-values[x['feeder']]) for x in p)/len(p)
                cod=min(values[x['feeder']]+x['beta'] for x in p)
                values[n]=min(sod,cod)+(f['delta'] or 0.)
        return values
    for ri in range(50):
        path=source/f'逐次记录/repetition_{ri+1:03d}.json'
        assert sha(path)==report['source_record_sha256'][path.name]
        record=read(path);selected=[draws['training_base_cases'][i] for i in draws['indices'][ri]]
        for j,(group,mode) in enumerate(report['axes']['jobs']):
            fits={f['node']:f for f in record['fits'] if (f['fit_group'],f['model'])==(group,mode)}
            ranges={(n,p):(min(lookup[group,c]['node_values'][p] for c in selected),
                           max(lookup[group,c]['node_values'][p] for c in selected)) for n in nodes for p in parents[n]}
            for b,case in enumerate(report['axes']['base_cases']):
                values={n:lookup[group,case]['node_values'][n] for n in roots};base=forward(fits,values)
                for ni,n in enumerate(nodes):close(arrays['baseline'][ri,j,b,ni],base[n])
                for k,(start,target) in enumerate(report['axes']['pairs']):
                    for fi,fraction in enumerate(report['axes']['fractions']):
                        key=ri,j,k,fi,b;forced=base[start]*fraction;changed=forward(fits,values,(start,forced))
                        assert arrays['valid'][key] and 0<=base[start]<=100
                        close(arrays['forced'][key],forced);close(arrays['changed'][key],changed[target])
                        close(arrays['decrease'][key],base[target]-changed[target]);checked+=1
                        is_out=not 0<=changed[target]<=100
                        is_extra=any(changed[p]<ranges[target,p][0]-1e-8 or changed[p]>ranges[target,p][1]+1e-8 for p in parents[target])
                        assert bool(arrays['outside'][key])==is_out;assert bool(arrays['extrapolated'][key])==is_extra
                        outside+=is_out;extra+=is_extra
                        descendants=set();pending=[start]
                        while pending:
                            current=pending.pop()
                            for n in nodes:
                                if current in parents[n] and n not in descendants:descendants.add(n);pending.append(n)
                        for n in nodes:
                            if n!=start and n not in descendants:close(changed[n],base[n])
    assert checked==405000==report['audit']['available_responses']
    assert outside==report['audit']['outside_responses'] and extra==report['audit']['target_extrapolated_responses']
    for row in report['summary']:
        j=report['axes']['jobs'].index([row['group'],row['model']]);k=report['axes']['pairs'].index([row['intervened_node'],row['target']]);fi=report['axes']['fractions'].index(row['retained_fraction'])
        means=arrays['decrease'][:,j,k,fi].mean(axis=1)
        assert row['complete_models']==50 and row['baseline_records']==30
        for name,value in [('minimum',min(means)),('median',np.median(means)),('maximum',max(means))]:close(row[name],value)
    with (out/'传播响应摘要.csv').open(encoding='utf-8-sig',newline='') as stream:
        saved=list(csv.DictReader(stream))
    assert len(saved)==len(report['summary'])
    for a,b in zip(saved,report['summary']):
        for name,value in b.items():
            if isinstance(value,(float,int)):close(float(a[name]),value)
            else:assert a[name]==value
    with (out/'固定示例.csv').open(encoding='utf-8-sig',newline='') as stream:
        focus=list(csv.DictReader(stream))
    assert len(focus)==50
    fixed=report['protocol']['focus_plot']
    j=report['axes']['jobs'].index([fixed['group'],fixed['model']])
    k=report['axes']['pairs'].index([fixed['intervened_node'],fixed['target']])
    fi=report['axes']['fractions'].index(fixed['retained_fraction'])
    for ri,row in enumerate(focus):
        record=read(source/f'逐次记录/repetition_{ri+1:03d}.json');assert int(row['repetition'])==ri+1
        target=next(f for f in record['fits'] if (f['fit_group'],f['model'],f['node'])==(fixed['group'],fixed['model'],fixed['target']))
        close(float(row['target_beta']),next(p['beta'] for p in target['parameters'] if p['feeder']==fixed['intervened_node']))
        close(float(row['mean_decrease']),arrays['decrease'][ri,j,k,fi].mean())
        metric=next(m for m in record['metrics'] if (m['fit_group'],m['model'],m['node'],m['split'],m['prediction'])==(fixed['group'],fixed['model'],fixed['target'],'holdout','recursive'))
        close(float(row['recursive_rmse']),metric['rmse'])
    return dict(status='passed',responses_checked=checked,summary_rows_checked=len(report['summary']),
                summary_csv_rows_checked=len(saved),focus_csv_rows_checked=len(focus),
                source_manifest_sha256=sha(out/'manifest.json'),report_sha256=sha(out/'propagation_report.json'),
                arrays_sha256=sha(out/'逐模型逐基准数组.npz'),
                outside_responses=outside,target_extrapolated_responses=extra,max_difference=maximum)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report-dir',type=Path,required=True);parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=verify(args.report_dir);text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        if args.output.exists():raise ValueError('复核记录已存在')
        args.output.write_text(text,encoding='utf-8')
    print(text)
