"""Audit reference counts only; does not import production intake or estimators."""
from collections import Counter,defaultdict


def grade(protocol,rows,registry,*,input_sources=None,final_outcomes=None):
    flows={f['flow_id']:f for f in protocol['flows']}
    keys={};by_id={}
    for row in rows:
        key=(row['split'],row['flow_id'],row['sample_id'],row['stage_instance_id'])
        if key in keys or row['observation_id'] in by_id:raise ValueError('duplicate reference observation')
        keys[key]=row;by_id[row['observation_id']]=row
    registered={(r['split'],r['flow_id'],r['sample_id']):r for r in registry}
    if len(registered)!=len(registry):raise ValueError('duplicate enrolled subject')
    labels=set(protocol['label_space']);result=[]
    def evaluated(row):
        return row is not None and row['gold_label'] in labels and (row['status']=='abstained' or
            (row['status']=='observed' and row['output_label'] in labels))
    def correct(row):return row['status']=='observed' and row['output_label']==row['gold_label']
    for fid,flow in flows.items():
        stage_results=[]
        for split in ('calibration','evaluation'):
            for stage in flow['stages']:
                sid=stage['stage_instance_id'];statuses=Counter();success=0
                subjects=[r for r in registry if r['split']==split and r['flow_id']==fid and sid in r['expected_stages']]
                for subject in subjects:
                    row=keys.get((split,fid,subject['sample_id'],sid))
                    statuses['expected']+=1
                    if row is None:statuses['unrecorded']+=1;continue
                    statuses['recorded']+=1;statuses['status:'+row['status']]+=1
                    if evaluated(row):statuses['evaluable']+=1;success+=correct(row)
                    elif row['status'] in ('observed','abstained'):statuses['unevaluable_label']+=1
                n=statuses['evaluable']
                stage_results.append(dict(split=split,stage_instance_id=sid,counts=dict(statuses),correct=success,
                    rate=success/n if n else None,denominator='actually_evaluable_stage_observations'))
        cohort=[r for r in registry if r['split']=='evaluation' and r['flow_id']==fid and r['endpoint_eligible']]
        all_steps=[s['stage_instance_id'] for s in flow['stages']];complete=[];last=[];absent=[]
        for subject in cohort:
            sample=subject['sample_id'];sequence=[keys.get(('evaluation',fid,sample,sid)) for sid in all_steps]
            if not any(sequence):absent.append(sample)
            if all(evaluated(row) for row in sequence):complete.append(sequence)
            if evaluated(sequence[-1]):last.append(sequence[-1])
        endpoints=[];final_missing=[]
        for subject in cohort:
            key=(fid,subject['sample_id']);endpoint=(final_outcomes or {}).get(key)
            if endpoint is None or type(endpoint.get('success')) is not bool or not endpoint.get('evaluation_method'):
                final_missing.append(subject['sample_id'])
            else:endpoints.append(endpoint)
        n=len(endpoints)
        result.append(dict(flow_id=fid,stage_statistics=stage_results,registered_endpoint_eligible=len(cohort),
            wholly_unrecorded_subjects=absent,complete_evaluation=len(complete),
            complete_case_last_output_rate=sum(correct(s[-1]) for s in complete)/len(complete) if complete else None,
            last_stage_evaluable=len(last),last_stage_descriptive_rate=sum(correct(row) for row in last)/len(last) if last else None,
            independent_final_evaluable=n,independent_final_missing=final_missing,
            independent_final_rate=sum(e['success'] for e in endpoints)/n if n else None,
            independent_final_coverage_complete=not final_missing and bool(cohort)))
    lineage=[]
    for target_id,source_id in (input_sources or {}).items():
        target=by_id.get(target_id);source=by_id.get(source_id);reasons=[]
        if target is None or source is None:reasons.append('unknown_observation_reference')
        else:
            if (target['flow_id'],target['sample_id'],target['split'])!=(source['flow_id'],source['sample_id'],source['split']):
                reasons.append('input_source_belongs_to_other_subject_or_flow')
            order=[s['stage_instance_id'] for s in flows[target['flow_id']]['stages']]
            index=order.index(target['stage_instance_id'])
            if index==0 or source['stage_instance_id']!=order[index-1]:reasons.append('not_declared_preceding_stage')
        lineage.append(dict(target_observation_id=target_id,source_observation_id=source_id,valid=not reasons,reasons=reasons))
    return dict(flows=result,lineage_checks=lineage,reference_scope='audit-only declared registry/labels; no domain validation')
