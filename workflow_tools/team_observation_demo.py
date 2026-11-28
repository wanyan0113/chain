"""Synthetic six-table collection fixture bound to real task_flow/OWL chains."""
import argparse
from copy import deepcopy
import csv
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import zipfile

from task_flow.joint_identity import sha
from .audit_team_statistics import protocol_draft
from .prepare_team_observation_contract import template
from .team_study_lock import create_lock
from .team_observation_intake import save, run as intake

ROOT=Path(__file__).resolve().parents[1]
BASE=datetime(2026,11,28,tzinfo=timezone.utc)
def stamp(seconds):return (BASE+timedelta(seconds=seconds)).isoformat()


def inputs(feasibility_only=False):
    with zipfile.ZipFile(ROOT/'docs/task-flow/team-quality-semantics-evidence/full-run.zip') as archive:
        source=json.loads(archive.read('delivery/team-optimization.json').decode('utf-8'))
    candidates=source['source_report']['candidates']
    ids=[]
    for task in ('T1','T2'):
        for group in (['O-Phase-Tuner','O-Safety-Auditor'],['O-Combined']):
            ids.append(next(cid for cid,c in candidates.items() if c['task']==task and c['plan']['steps'][1]['members']==group))
    # A study may compare locally verified alternatives, not just the chosen
    # optimizer network. This view is only for descriptor extraction, never a
    # forged source report: the original report is hashed and verified intact.
    view=deepcopy(source);view['selected_candidate_ids']=ids
    p=template(protocol_draft(view));p['source_binding']=protocol_draft(source)['source_binding']
    p.update(study_id='synthetic-team-observation-study',study_protocol_status='frozen',actual_study_frozen=True,
        data_origin='synthetic_simulation',maximum_attempts=3,planned_execution_repeats=1,
        endpoint_window='Synthetic endpoint collected after the final actual attempt within 60 seconds.',
        retry_task_effect='Independent task endpoint; retries do not overwrite first-attempt classification records.',sampling_design='paired_same_sample')
    p['source_reports']=[dict(source_report_id='source-1',report_sha256=sha(source))]
    for key in p['pairing_controls']:
        if type(p['pairing_controls'][key]) is str:p['pairing_controls'][key]='synthetic fixed-order paired design with reset; no real causal claim'
    p['acceptance'].update(alpha=.05,minimum_calibration=1,minimum_evaluation=1,minimum_prefix_support=1,
        acceptable_absolute_error=.05,subgroup_plan='synthetic all stratum',systematic_bias_rule='Descriptive-only software fixture, not domain acceptance.')
    for i,a in enumerate(p['arms']):
        a.update(arm_id='arm-'+str(i+1),source_report_id='source-1',endpoint_definition='independent-stability-event',
            endpoint_method='categorical_equality_v1',endpoint_reference_method='synthetic-independent-task-reference',
            endpoint_equals_all_stage_success='declared_not_equivalent')
        for s in a['stages']:
            s.update(measurement_object='Synthetic independent group output',direction='target_match',scale_meaning='Categorical equality',
                evaluation_method='categorical_equality_v1',reference_method='synthetic-independent-group-reference',
                population=a['task_id']+'-synthetic-population',group_success_event='group-output-matches-reference')
            for m in s['members']:
                m['artifact_version']='synthetic-firmware@1'
                for e in m['measurements']:
                    e.update(measurement_object='Synthetic capability contribution '+e['capability_id'],direction='target_match',
                        scale_meaning='Categorical equality',evaluation_method='categorical_equality_v1',
                        reference_method='synthetic-independent-capability-reference',population=s['population'],success_event='capability-output-matches-reference')
    planned=dict(contract_version='team-observation-contract-1.0',study_id=p['study_id'],protocol_revision_hash=sha(p),
        data_origin='synthetic_simulation',samples=[],executions=[],stage_attempts=[],member_observations=[],group_outputs=[],task_endpoints=[])
    n=0
    for task in ('T1','T2'):
        arms=[a for a in p['arms'] if a['task_id']==task]
        for j in range(3):
            sid=f'{task}-sample-{j}';baseline='synthetic://baseline/'+sid
            planned['samples'].append(dict(sample_id=sid,pair_id='pair-'+sid,task_id=task,population=task+'-synthetic-population',
                cluster_id='cluster-'+sid,split='calibration' if j<2 else 'evaluation',stratum='all',eligible=True,exclusion_reason=None,
                baseline_input_reference=baseline,registered_at=stamp(0)))
            for order,a in enumerate(arms,1):
                eid=f'{sid}-{a["arm_id"]}';n+=1
                planned['executions'].append(dict(execution_id=eid,sample_id=sid,arm_id=a['arm_id'],task_id=task,source_report_id='source-1',
                    candidate_id=a['candidate_id'],environment_reference='synthetic://environment/'+eid,environment_stratum='controlled',
                    assigned_order=order,execution_repeat_id=1,actual_order=None,order_deviation_reason=None,execution_input_reference=baseline,
                    reset_reference=None,expected_stage_instance_ids=[s['stage_instance_id'] for s in a['stages']],
                    registered_at=stamp(1),started_at=None,ended_at=None))
                for s in a['stages']:
                    planned['stage_attempts'].append(dict(execution_id=eid,candidate_id=a['candidate_id'],stage_instance_id=s['stage_instance_id'],attempt_id=1,
                        registered_at=stamp(2),started_at=None,ended_at=None,execution_status='planned',reason=None,reason_detail=None))
    data=deepcopy(planned)
    for i,e in enumerate(data['executions']):
        a=next(a for a in p['arms'] if a['arm_id']==e['arm_id']);start=3600+i*120
        e.update(actual_order=e['assigned_order'],started_at=stamp(start),ended_at=stamp(start+40),
                 reset_reference='synthetic://reset/'+e['execution_id'] if e['assigned_order']>1 else None)
        previous=None
        for j,s in enumerate(a['stages']):
            t=next(t for t in data['stage_attempts'] if t['execution_id']==e['execution_id'] and t['stage_instance_id']==s['stage_instance_id'])
            t.update(started_at=stamp(start+j*10),ended_at=stamp(start+j*10+5),execution_status='completed')
            groupid=e['execution_id']+':'+s['stage_instance_id']+':g1'
            mids=[]
            for m in s['members']:
                for cap in m['capabilities']:
                    mid=e['execution_id']+':'+s['stage_instance_id']+':'+m['artifact_id']+':'+cap+':m1';mids.append(mid)
                    data['member_observations'].append(dict(observation_id=mid,sample_id=e['sample_id'],execution_id=e['execution_id'],candidate_id=a['candidate_id'],
                        stage_instance_id=s['stage_instance_id'],attempt_id=1,artifact_id=m['artifact_id'],artifact_version=m['artifact_version'],capability_id=cap,
                        actual_label='correct',reference_label='correct',output_reference='synthetic://member-output/'+mid,
                        independent_reference='synthetic://independent-capability-truth/'+mid,evaluation_method='categorical_equality_v1',
                        observation_status='recorded',reference_status='known',missing_reason=None,missing_reason_detail=None,recorded_at=stamp(start+j*10+6),
                        input_reference=previous['output_reference'] if previous else e['execution_input_reference'],
                        input_group_observation_ids=[previous['observation_id']] if previous else []))
            group=dict(observation_id=groupid,sample_id=e['sample_id'],execution_id=e['execution_id'],candidate_id=a['candidate_id'],stage_instance_id=s['stage_instance_id'],
                attempt_id=1,hub=s['hub'],member_observation_ids=mids,input_group_observation_ids=[previous['observation_id']] if previous else [],
                actual_label='correct',reference_label='correct',output_reference='synthetic://group-output/'+groupid,
                independent_reference='synthetic://independent-group-truth/'+groupid,evaluation_method='categorical_equality_v1',observation_status='recorded',reference_status='known',
                missing_reason=None,missing_reason_detail=None,recorded_at=stamp(start+j*10+7))
            data['group_outputs'].append(group);previous=group
        endpointid=e['execution_id']+':endpoint'
        data['task_endpoints'].append(dict(observation_id=endpointid,sample_id=e['sample_id'],execution_id=e['execution_id'],candidate_id=a['candidate_id'],
            endpoint_event_id=a['endpoint_definition'],contributing_group_observation_ids=[previous['observation_id']],
            actual_label='unstable' if i%5==0 else 'stable',reference_label='stable',output_reference='synthetic://task-state/'+endpointid,
            independent_reference='synthetic://independent-required-state/'+endpointid,evaluation_method='categorical_equality_v1',
            observation_status='recorded',reference_status='known',missing_reason=None,missing_reason_detail=None,recorded_at=stamp(start+45)))
    if feasibility_only:
        source=source['source_report']
        p['source_reports'][0]['report_sha256']=sha(source)
        p['source_binding']=dict(source_input_hash=source['input_hash'],execution_context_hash=sha(source['context']),
            selected_candidate_ids=[a['candidate_id'] for a in p['arms']],evaluation_context_hash='not_evaluated:feasibility_only')
        planned['protocol_revision_hash']=data['protocol_revision_hash']=sha(p)
    return planned,data,p,{'source-1':source}


def write_packet(output,data,p,lock,sources,*,csv_mode=False):
    output.mkdir(parents=True,exist_ok=False)
    save(output/'protocol.json',p);save(output/'lock.json',lock)
    entries=[]
    for i,(sid,r) in enumerate(sources.items()):
        file=f'source-{i}.json';save(output/file,r);entries.append(dict(source_report_id=sid,report_file=file))
    save(output/'sources.json',dict(version='team-observation-sources-1.0',sources=entries))
    if csv_mode:
        from .team_observation_io import TABLES
        meta={k:v for k,v in data.items() if k not in TABLES};meta['table_files']={t:t+'.csv' for t in TABLES}
        for t in TABLES:
            from .prepare_team_observation_contract import fields
            with (output/(t+'.csv')).open('w',encoding='utf-8',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(fields()[t]));w.writeheader()
                for r in data[t]:w.writerow({k:'' if v is None else json.dumps(v,ensure_ascii=False) if isinstance(v,(list,bool)) else v for k,v in r.items()})
        save(output/'dataset.json',meta)
    else:save(output/'dataset.json',data)


def run(output,feasibility_only=False):
    if output.exists():raise ValueError('output exists')
    planned,data,p,sources=inputs(feasibility_only)
    lock=create_lock(planned,p,sources,['synthetic-software-study-review-only'],frozen_at=stamp(10))
    output.mkdir(parents=True)
    write_packet(output/'packet',data,p,lock,sources,csv_mode=True)
    r=intake(output/'packet/dataset.json',output/'packet/protocol.json',output/'packet/lock.json',output/'packet/sources.json',output/'intake')
    check=json.loads((output/'intake/verification.json').read_text(encoding='utf-8'))
    if check['status']!='passed':raise AssertionError(check['issues'])
    save(output/'registration-plan.json',planned)
    summary=dict(status='passed',data_origin='synthetic_simulation',counts=check['counts'],ontology_status=check['ontology']['status'],
        classification_counts=check['classification_counts'],confidence_intervals=None,domain_validity='pending',production_writeback=False)
    save(output/'verification.json',summary);print(json.dumps(summary,ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--feasibility-source',action='store_true')
    a=parser.parse_args();run(a.output_dir,a.feasibility_source)


if __name__=='__main__':main()
