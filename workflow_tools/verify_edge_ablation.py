"""Recompute every prediction and propagation response with scalar FDNA equations."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def table(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as stream:return list(csv.DictReader(stream))


def verify(directory):
    out=Path(directory);report=read(out/'ablation_report.json');source=Path(report['source']['directory'])
    for name,value in read(out/'manifest.json').items():assert sha(out/name)==value,name
    assert sha(source/'完整包校验.json')==report['source']['manifest_sha256']
    package=read(source/'完整包校验.json')
    for name,value in package.items():assert sha(source/name)==value,name
    original=read(source/'04_FDNA拟合/workflow_report.json');rows=read(source/'04_FDNA拟合/输入_SE记录.json')
    assert sha(source/'04_FDNA拟合/workflow_report.json')==report['source']['fit_sha256']
    axes=report['axes'];network=original['network_snapshot'];parents={n:[e['feeder'] for e in network['edges'] if e['receiver']==n] for n in axes['nodes']}
    roots=[n for n in axes['nodes'] if not parents[n]];targets=sorted(n for n in parents if parents[n]);assert targets==axes['targets']
    order=[]
    while len(order)<len(parents):
        ready=[n for n in sorted(parents) if n not in order and all(p in order for p in parents[n])];assert ready;order.extend(ready)
    lookup={(r['fit_group'],r['base_case_id']):r for r in rows};jobs={(j['fit_group'],j['model']):j for j in original['jobs']}
    means={};maximum=0.;prediction_checks=0;responses_checked=0;unreachable=0
    def close(a,b):
        nonlocal maximum
        assert math.isfinite(a) and math.isfinite(b)
        error=abs(a-b);maximum=max(maximum,error);assert error<=1e-8,(a,b,error)
    for c in report['training_constants']:
        selected=sorted((r for r in rows if r['fit_group']==c['group'] and r['split']=='train'),key=lambda r:r['sample_id'])
        value=math.fsum(r['node_values'][c['node']] for r in selected)/len(selected)
        close(value,c['value']);assert len(selected)==c['training_rows']==90
        assert c['training_sample_ids']==[r['sample_id'] for r in selected]
        assert c['training_base_cases']==sorted({r['base_case_id'] for r in selected})
        means[c['group'],c['node']]=value
    assert len(means)==18
    with np.load(out/'逐样本预测与传播.npz') as archive:arrays={k:archive[k] for k in archive.files}
    def scalar(fit,values):
        params=fit['parameters'];assert fit['status']=='fitted'
        sod=math.fsum(100-p['alpha']*(100-values[p['feeder']]) for p in params)/len(params)
        cod=min(values[p['feeder']]+p['beta'] for p in params)
        return min(sod,cod)+(fit['delta'] or 0.)
    def compute(fits,row,cut,group,force=None):
        values={n:row['node_values'][n] for n in roots}
        for n in order:
            if force and force[0]==n:values[n]=force[1]
            elif n in roots:continue
            elif n in cut:values[n]=means[group,n]
            else:values[n]=scalar(fits[n],values)
        return values
    assert axes['scenarios']==['original','cut_A1_B1','cut_A2_B2','cut_A3_B3','cut_B1_F1','cut_B2_F2','cut_B3_F3','cut_all']
    for si,variant in enumerate(report['scenarios']):
        cut={pair[1] for pair in variant['removed']};assert cut==set(variant['new_constant_roots'])
        expected_edges=[e for e in network['edges'] if [e['feeder'],e['receiver']] not in variant['removed']]
        assert variant['network']['edges']==sorted(expected_edges,key=lambda e:(e['feeder'],e['receiver'])) or si==0 and variant['network']==network
        if si:
            assert variant['preview']['status']=='review_required' and not variant['preview']['policies']['mapping_approved']
            assert variant['preview']['original_evaluation_targets']==targets
        reach={}
        for start in axes['nodes']:
            pending=[start];found=set()
            while pending:
                a=pending.pop()
                for e in expected_edges:
                    if e['feeder']==a and e['receiver'] not in found:found.add(e['receiver']);pending.append(e['receiver'])
            reach[start]=found
        for k,(start,target) in enumerate(axes['pairs']):assert bool(arrays['structurally_reachable'][si,k])==(target in reach[start])
        for ji,(group,mode) in enumerate(axes['jobs']):
            job=jobs[group,mode];fits={f['node']:f['fit']['receivers'][0] for f in job['local_fits']}
            original_points={(p['base_case_id'],p['node']):p for p in job['points']}
            for bi,case in enumerate(axes['cases']):
                row=lookup[group,case];values=compute(fits,row,cut,group)
                for ni,n in enumerate(targets):
                    local=means[group,n] if n in cut else scalar(fits[n],row['node_values'])
                    close(arrays['predictions'][si,ji,bi,ni,0],local);close(arrays['predictions'][si,ji,bi,ni,1],values[n]);prediction_checks+=2
                    close(arrays['target_values'][axes['groups'].index(group),bi,ni],row['node_values'][n])
                    if si==0:
                        close(local,original_points[case,n]['local_prediction']);close(values[n],original_points[case,n]['recursive_prediction'])
                if case not in axes['holdout_cases']:continue
                b=axes['holdout_cases'].index(case)
                for ni,n in enumerate(axes['nodes']):close(arrays['baseline'][si,ji,b,ni],values[n])
                for k,(start,target) in enumerate(axes['pairs']):
                    for fi,fraction in enumerate(axes['fractions']):
                        forced=values[start]*fraction;post=compute(fits,row,cut,group,(start,forced));key=si,ji,k,fi,b
                        close(arrays['forced'][key],forced);close(arrays['changed'][key],post[target]);close(arrays['decrease'][key],values[target]-post[target]);responses_checked+=1
                        if target not in reach[start]:close(arrays['decrease'][key],0.);unreachable+=1
                        extrap=False
                        if target not in cut:
                            training=[r for r in rows if r['fit_group']==group and r['split']=='train']
                            for feeder in parents[target]:
                                xs=[r['node_values'][feeder] for r in training]
                                extrap|=post[feeder]<min(xs)-1e-8 or post[feeder]>max(xs)+1e-8
                        assert bool(arrays['extrapolated'][key])==extrap
            dimension=next(r for r in report['complexity'] if (r['scenario'],r['group'],r['model'])==(variant['id'],group,mode))
            assert dimension['constant_parameters']==len(cut) and dimension['evaluation_targets']==6
            assert dimension['frozen_fdna_nominal_parameters']==sum(2*len(f['parameters'])+int(f['delta'] is not None) for n,f in fits.items() if n not in cut)
            assert not dimension['new_roots_observed'] and dimension['observed_external_roots']==roots
    for row in report['metrics']:
        si=axes['scenarios'].index(row['scenario']);ji=axes['jobs'].index([row['group'],row['model']]);ni=targets.index(row['node']);pi=axes['prediction'].index(row['prediction'])
        indices=[i for i,c in enumerate(axes['cases']) if lookup[row['group'],c]['split']==row['split']]
        truth=[lookup[row['group'],axes['cases'][i]]['node_values'][row['node']] for i in indices]
        pred=arrays['predictions'][si,ji,indices,ni,pi];old=arrays['predictions'][0,ji,indices,ni,pi]
        rmse=math.sqrt(math.fsum((a-b)**2 for a,b in zip(truth,pred))/len(indices));reference=math.sqrt(math.fsum((a-b)**2 for a,b in zip(truth,old))/len(indices))
        assert row['planned']==row['evaluated']==len(indices);close(row['rmse'],rmse);close(row['original_rmse'],reference);close(row['difference'],rmse-reference)
        assert row['outside']==sum(p<0 or p>100 for p in pred)
    for row in report['responses']:
        si=axes['scenarios'].index(row['scenario']);ji=axes['jobs'].index([row['group'],row['model']]);k=axes['pairs'].index([row['intervened_node'],row['target']]);fi=axes['fractions'].index(row['retained_fraction'])
        key=si,ji,k,fi;panel=arrays['decrease'][key]
        close(row['mean_decrease'],math.fsum(panel)/len(panel));close(row['original_mean_decrease'],math.fsum(arrays['decrease'][0,ji,k,fi])/len(panel))
        assert row['structurally_reachable']==bool(arrays['structurally_reachable'][si,k])
        assert row['extrapolated_records']==int(arrays['extrapolated'][key].sum())
        assert row['outside_records']==int(np.sum((arrays['changed'][key]<0)|(arrays['changed'][key]>100)))
        assert row['baseline_records']==row['evaluated']==30
    for key,name in [('metrics','同目标误差对照.csv'),('responses','同关系传播对照.csv')]:
        stored=table(out/name);assert len(stored)==len(report[key])
        for a,b in zip(stored,report[key]):
            for k,v in b.items():assert a[k]==str(v)
    assert prediction_checks==report['audit']['prediction_values']==69120
    assert responses_checked==report['audit']['propagation_responses']==64800
    assert unreachable==report['audit']['removed_path_responses']==18900
    return dict(status='passed',source_files_verified=len(package),training_constants_checked=len(means),
        prediction_values_checked=prediction_checks,propagation_responses_checked=responses_checked,removed_path_zero_checks=unreachable,
        metric_rows_checked=len(report['metrics']),response_summary_rows_checked=len(report['responses']),maximum_absolute_difference=maximum,
        source_manifest_sha256=sha(out/'manifest.json'),report_sha256=sha(out/'ablation_report.json'))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report-dir',type=Path,required=True);parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=verify(args.report_dir);text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        if args.output.exists():raise ValueError('核验记录已存在')
        args.output.write_text(text,encoding='utf-8',newline='\n')
    print(text)
