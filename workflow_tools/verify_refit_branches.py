"""Independent scalar checks; does not import the production branch classifier."""
import argparse
import csv
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def table(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as stream:return list(csv.DictReader(stream))


def verify(directory):
    out=Path(directory);report=read(out/'branch_report.json');tol=report['protocol']['tie_tolerance']
    for name,value in read(out/'manifest.json').items():assert sha(out/name)==value,name
    source=Path(report['source_propagation_directory'])
    assert sha(source/'manifest.json')==report['source_manifest_sha256']
    assert sha(source/'propagation_report.json')==report['source_report_sha256']
    assert sha(source/'逐模型逐基准数组.npz')==report['source_arrays_sha256']
    saved=read(source/'propagation_report.json');refits=Path(saved['source_refit_directory'])
    assert sha(refits/'manifest.json')==saved['source_manifest_sha256']
    for name,value in read(refits/'manifest.json').items():assert sha(refits/name)==value,name
    refit=read(refits/'refit_report.json');original=Path(refit['source']['directory'])
    for name,value in read(original/'完整包校验.json').items():assert sha(original/name)==value,name
    draws=read(refits/'训练成组抽样索引.json');rows=read(original/'04_FDNA拟合/输入_SE记录.json')
    lookup={(r['fit_group'],r['base_case_id']):r for r in rows};axes=report['axes'];assert axes==saved['axes']
    network=read(original/'04_FDNA拟合/workflow_report.json')['network_snapshot']
    parents={n:[e['feeder'] for e in network['edges'] if e['receiver']==n] for n in axes['nodes']}
    assert all(len(p)<=1 for p in parents.values());order=[]
    while len(order)<len(parents):
        ready=[n for n in sorted(parents) if n not in order and all(p in order for p in parents[n])]
        assert ready;order.extend(ready)
    with np.load(out/'逐响应分支.npz') as archive:codes={k:archive[k] for k in archive.files}
    with np.load(source/'逐模型逐基准数组.npz') as archive:old={k:archive[k] for k in archive.files}
    training=table(out/'训练分支逐拟合.csv');conditions=table(out/'传播分支逐模型条件.csv')
    train_map={(int(r['repetition']),r['group'],r['model'],r['node']):r for r in training}
    keys=('group','model','intervened_node','target','retained_fraction')
    condition_map={(int(r['repetition']),r['group'],r['model'],r['intervened_node'],r['target'],float(r['retained_fraction'])):r for r in conditions}
    assert len(train_map)==len(training)==1800;assert len(condition_map)==len(conditions)==13500
    maximum=0.;checked=0;training_rows=0;grouped=defaultdict(list)
    def formula(f,x):
        p=f['parameters'][0];s=100-p['alpha']*(100-x);c=x+p['beta']
        code=3 if abs(s-c)<=tol else 1 if s<c else 2
        return code,min(s,c)+(f['delta'] or 0.)
    def forward(fits,roots,force=None):
        values=dict(roots);labels={}
        for n in order:
            if force and n==force[0]:values[n]=force[1]
            elif parents[n]:labels[n],values[n]=formula(fits[n],values[parents[n][0]])
        return values,labels
    def close(a,b):
        nonlocal maximum
        error=abs(a-b);maximum=max(maximum,error);assert np.isfinite(error) and error<=1e-8
    for ri,indices in enumerate(draws['indices']):
        selected=[draws['training_base_cases'][i] for i in indices]
        record=read(refits/f'逐次记录/repetition_{ri+1:03d}.json')
        for j,(group,mode) in enumerate(axes['jobs']):
            fits={f['node']:f for f in record['fits'] if (f['fit_group'],f['model'])==(group,mode)}
            inactive={}
            for target,f in fits.items():
                assert f['status']=='fitted';p=parents[target][0]
                labels=[formula(f,lookup[group,c]['node_values'][p])[0] for c in selected];training_rows+=len(labels)
                stored=train_map[ri+1,group,mode,target];inactive[target]=all(c==1 for c in labels)
                assert stored['strict_training_beta_inactive']==str(inactive[target])
                for name,value in [('training_sod',labels.count(1)),('training_cod',labels.count(2)),('training_near_tie',labels.count(3)),
                                   ('available_training_rows',len(labels)),('planned_training_rows',len(selected)),('distinct_training_cases',len(set(selected)))]:assert int(stored[name])==value
            base_states=[]
            for case in axes['base_cases']:
                roots={n:lookup[group,case]['node_values'][n] for n in order if not parents[n]}
                base_states.append((roots,*forward(fits,roots)))
            for k,(start,target) in enumerate(axes['pairs']):
                for fi,fraction in enumerate(axes['fractions']):
                    counts=dict(sod=0,cod=0,near_tie=0,activated_beta_records=0,activation_with_extrapolation=0,
                                activated_from_recursive_sod=0,activated_already_recursive_cod=0,activated_from_recursive_near_tie=0,baseline_sod_to_cod=0)
                    for b,(roots,base,base_labels) in enumerate(base_states):
                        values,labels=forward(fits,roots,(start,base[start]*fraction));label=labels[target];baseline_label=base_labels[target]
                        assert codes['response_branch'][ri,j,k,fi,b]==label
                        assert codes['baseline_branch'][ri,j,k,b]==baseline_label
                        close(values[target],old['changed'][ri,j,k,fi,b]);checked+=1
                        counts[{1:'sod',2:'cod',3:'near_tie'}[label]]+=1
                        counts['baseline_sod_to_cod']+=baseline_label==1 and label==2
                        if inactive[target] and label==2:
                            counts['activated_beta_records']+=1
                            feeder=parents[target][0];xs=[lookup[group,c]['node_values'][feeder] for c in selected]
                            extrap=values[feeder]<min(xs)-tol or values[feeder]>max(xs)+tol
                            counts['activation_with_extrapolation']+=extrap
                            counts[{1:'activated_from_recursive_sod',2:'activated_already_recursive_cod',3:'activated_from_recursive_near_tie'}[baseline_label]]+=1
                    row=condition_map[ri+1,group,mode,start,target,fraction]
                    assert int(row['planned_records'])==int(row['available_records'])==30
                    assert row['strict_training_beta_inactive']==str(inactive[target])
                    for name,value in counts.items():assert int(row[name])==value,(name,row)
                    grouped[group,mode,start,target,fraction].append(row)
    assert checked==405000
    summary_csv=table(out/'传播分支条件摘要.csv');assert len(summary_csv)==len(report['summary'])==270
    fields=list(counts)+['planned_records','available_records']
    for r,csv_row in zip(report['summary'],summary_csv):
        key=tuple(r[k] for k in keys);parts=grouped[key];assert len(parts)==r['models']==50
        assert r['strict_training_beta_inactive_models']==sum(p['strict_training_beta_inactive']=='True' for p in parts)
        for name in fields:assert r[name]==sum(int(p[name]) for p in parts)
        for name,value in r.items():assert csv_row[name]==str(value)
    audit=report['audit']
    assert audit['training_fits']==len(training) and audit['response_records']==audit['available_responses']==checked
    assert audit['strict_training_beta_inactive_fits']==sum(r['strict_training_beta_inactive']=='True' for r in training)
    active_fits={(int(r['repetition']),r['group'],r['model'],r['target']) for r in conditions if int(r['activated_beta_records'])}
    assert audit['activated_in_propagation_fits']==len(active_fits)
    for output,field in [('activated_beta_responses','activated_beta_records'),('activation_with_extrapolation','activation_with_extrapolation'),
                         ('activated_from_recursive_sod','activated_from_recursive_sod'),('activated_already_recursive_cod','activated_already_recursive_cod'),
                         ('activated_from_recursive_near_tie','activated_from_recursive_near_tie'),('baseline_sod_to_cod','baseline_sod_to_cod'),('near_tie_responses','near_tie')]:
        assert audit[output]==sum(int(r[field]) for r in conditions)
    assert audit['activation_without_extrapolation']==audit['activated_beta_responses']-audit['activation_with_extrapolation']
    assert audit['activated_beta_responses']==sum(audit[k] for k in ('activated_from_recursive_sod','activated_already_recursive_cod','activated_from_recursive_near_tie'))
    assert sum(int(r['activated_from_recursive_sod']) for r in conditions if float(r['retained_fraction'])==1)==0
    return dict(status='passed',training_fit_rows_checked=len(training),scalar_training_inputs_checked=training_rows,
        scalar_responses_checked=checked,model_conditions_checked=len(conditions),summary_rows_checked=len(summary_csv),
        maximum_response_difference=maximum,source_manifest_sha256=sha(out/'manifest.json'),report_sha256=sha(out/'branch_report.json'))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report-dir',type=Path,required=True);parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=verify(args.report_dir);text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        if args.output.exists():raise ValueError('拒绝覆盖已有核验记录')
        args.output.write_text(text,encoding='utf-8',newline='\n')
    print(text)
