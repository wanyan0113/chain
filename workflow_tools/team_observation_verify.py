"""Independent packet replay and six-table checks, with a separate OWL audit.

Never calls the intake parser. No confidence intervals, quality estimation or
automatic writeback into production evaluation is performed.
"""
from collections import Counter,defaultdict
from datetime import datetime
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import unicodedata

from jsonschema import Draft202012Validator
from task_flow.joint_identity import sha
from task_flow.contracts import read_json
from task_flow.team_optimization_verify import verify_team_optimization
from .team_observation_io import CONTRACT, TABLES, VERSION, FROZEN_CONTRACT_HASH
from .team_observation_sources import source_payload


def moment(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('timestamp has no UTC offset')
    return d


def check_package(data,p,lock,sources,*,phase='observations'):
    issues=[];analysis_blockers=[];issue_total=0
    def issue(code,table='',row=None,column='',message='',**context):
        nonlocal issue_total
        issue_total+=1
        if len(issues)<1000:issues.append(dict(code=code,table=table,row=row,column=column,message=message,context=context))
    manifest=read_json(CONTRACT/'freeze-manifest.json',allow_finite_floats=True)
    fingerprint=hashlib.sha256(json.dumps(manifest['files'],sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
    if fingerprint!=FROZEN_CONTRACT_HASH or manifest['content_hash']!=fingerprint:issue('CONTRACT_CHANGED','contract')
    for name,digest in manifest['files'].items():
        f=(CONTRACT/name).resolve()
        if not f.is_relative_to(CONTRACT.resolve()) or hashlib.sha256(f.read_bytes()).hexdigest()!=digest:issue('CONTRACT_CHANGED','contract',message=name)
    if issues:return dict(status='blocked',issues=issues,ontology=dict(status='not_run'),domain_validity='pending')
    for obj,name in [(data,'schema.json'),(p,'protocol.schema.json')]:
        for e in Draft202012Validator(read_json(CONTRACT/name,allow_finite_floats=True)).iter_errors(obj):
            issue('SCHEMA',name,message=e.message,path=list(e.absolute_path))
    if issues:return dict(status='blocked',issues=issues,ontology=dict(status='not_run'),domain_validity='pending')
    if phase=='observations':
        required_lock={'version','study_id','protocol_hash','source_hashes','plan','plan_hash','frozen_at','review_evidence_refs','lock_hash','data_origin','domain_validity','external_signature_verified'}
        try:
            if not isinstance(lock,dict) or set(lock)!=required_lock or lock['version']!='team-study-lock-1.0':raise ValueError('invalid lock fields/version')
            moment(lock['frozen_at'])
            if type(lock['plan']) is not dict or set(lock['plan'])!={'samples','executions','first_stage_attempts'}:raise ValueError('invalid frozen plan sections')
            if not lock['review_evidence_refs'] or not all(type(v) is str and v.strip() for v in lock['review_evidence_refs']):raise ValueError('lock review references missing')
            if lock['external_signature_verified'] is not False or lock['domain_validity']!='pending' or lock['data_origin']!=p['data_origin']:raise ValueError('unsupported lock evidence claim')
            for rows in lock['plan'].values():
                if type(rows) is not list or any(type(r) is not dict or 'registered_at' not in r for r in rows):raise ValueError('invalid frozen plan rows')
                if any(moment(r['registered_at'])>moment(lock['frozen_at']) for r in rows):raise ValueError('plan registration is later than freeze')
        except (ValueError,KeyError,TypeError) as exc:
            issue('STUDY_LOCK_INVALID','lock',message=str(exc))
            return dict(status='blocked',issues=issues,ontology=dict(status='not_run'),domain_validity='pending')
    if p['study_protocol_status']!='frozen' or not p['actual_study_frozen']:issue('PROTOCOL_NOT_FROZEN','protocol')
    if data['study_id']!=p['study_id'] or data['data_origin']!=p['data_origin'] or data['protocol_revision_hash']!=sha(p):
        issue('PROTOCOL_BINDING','dataset')
    if issues:return dict(status='blocked',issues=issues,ontology=dict(status='not_run'),domain_validity='pending')
    all_times={}
    for table in TABLES:
        for i,r in enumerate(data[table],1):
            for k,v in r.items():
                if k.endswith('_at') and v is not None:
                    try:all_times[table,i,k]=moment(v)
                    except (ValueError,TypeError):issue('INVALID_TIMESTAMP',table,i,k,'invalid calendar/timezone')
    if issues:return dict(status='blocked',issues=issues,ontology=dict(status='not_run'),domain_validity='pending')
    def index(table,keys,ignore_null=False):
        result={}
        for i,r in enumerate(data[table],1):
            key=tuple(r[k] for k in keys)
            if ignore_null and any(x is None for x in key):continue
            if key in result:issue('DUPLICATE_KEY',table,i,','.join(keys),key=list(key))
            else:result[key]=(i,r)
        return result
    samples=index('samples',['sample_id']);executions=index('executions',['execution_id'])
    attempts=index('stage_attempts',['execution_id','stage_instance_id','attempt_id'])
    members=index('member_observations',['execution_id','stage_instance_id','attempt_id','artifact_id','capability_id'])
    groups=index('group_outputs',['execution_id','stage_instance_id','attempt_id'])
    index('samples',['pair_id'],True);index('executions',['sample_id','arm_id','execution_repeat_id']);index('executions',['sample_id','assigned_order'])
    index('task_endpoints',['execution_id','endpoint_event_id'])
    obs={}
    for table in ('member_observations','group_outputs','task_endpoints'):
        for i,r in enumerate(data[table],1):
            oid=r['observation_id']
            if oid in obs:issue('GLOBAL_OBSERVATION_ID_DUPLICATE',table,i,'observation_id',observation_id=oid,previous_table=obs[oid][0])
            else:obs[oid]=(table,i,r)
    arms={};source_defs={}
    for i,a in enumerate(p['arms'],1):
        if a['arm_id'] in arms:issue('DUPLICATE_ARM','protocol',i,'arm_id')
        arms[a['arm_id']]=a
    for i,s in enumerate(p['source_reports'],1):
        if s['source_report_id'] in source_defs:issue('DUPLICATE_SOURCE','protocol',i)
        source_defs[s['source_report_id']]=s
    if set(sources)!=set(source_defs):issue('SOURCE_SET_MISMATCH','sources')
    source_checks={};verified={}
    for sid,r in sources.items():
        if sid not in source_defs or sha(r)!=source_defs[sid]['report_sha256']:
            issue('SOURCE_REPORT_HASH','sources',message=sid);continue
        h=sha(r)
        if h not in verified:
            if r.get('format_version')=='task-flow-team-feasibility-1.0':
                from task_flow.team_verify import verify_teams
                verified[h]=verify_teams(r)
            else:verified[h]=verify_team_optimization(r)
        source_checks[sid]=verified[h]
        if verified[h]['status']!='passed':issue('SOURCE_INDEPENDENT_VERIFICATION','sources',message=sid,verification=verified[h])
    chain_by_arm={}
    for a in p['arms']:
        report=sources.get(a['source_report_id'])
        if report is None or source_checks.get(a['source_report_id'],{}).get('status')!='passed':continue
        source=source_payload(report);chain=source['candidates'].get(a['candidate_id'])
        if chain is None:issue('ARM_CANDIDATE_NOT_IN_SOURCE','protocol',message=a['arm_id']);continue
        chain_by_arm[a['arm_id']]=chain
        if chain['task']!=a['task_id'] or chain['flow_id']!=a['flow_id']:issue('ARM_TASK_FLOW_MISMATCH','protocol',message=a['arm_id'])
        if len(a['stages'])!=len(chain['plan']['steps']):issue('SOURCE_STAGE_COUNT','protocol',message=a['arm_id']);continue
        analysis=next(x for x in source['analyses'] if x['task_id']==chain['task'] and x['flow_id']==chain['flow_id'])
        instances=analysis['flow_candidate']['sequence']['steps']
        for j,(stage,expected,instance) in enumerate(zip(a['stages'],chain['plan']['steps'],instances)):
            expected_prefix=[s['step_instance_id'] for s in chain['plan']['steps'][:j]]
            if (stage['stage_instance_id']!=expected['step_instance_id'] or stage['activity_type']!=expected['activity'] or
                stage['activity_id']!=instance['activity_id'] or stage['activity_version']!=instance['activity_version'] or
                stage['full_prefix_stage_instance_ids']!=expected_prefix):issue('SOURCE_STAGE_INSTANCE_MISMATCH','protocol',j+1,message=a['arm_id'])
            if stage['capability_assignments']!=expected['capability_assignments'] or stage['hub']!=expected['hub']:
                issue('SOURCE_CAPABILITY_ASSIGNMENT_MISMATCH','protocol',j+1,message=a['arm_id'])
            if sorted(m['artifact_id'] for m in stage['members'])!=expected['members']:issue('SOURCE_MEMBER_SET','protocol',j+1,message=a['arm_id'])
            for m in stage['members']:
                caps=sorted(c for c,v in expected['capability_assignments'].items() if v==m['artifact_id'])
                if m['capabilities']!=caps or sorted(e['capability_id'] for e in m['measurements'])!=caps:
                    issue('MEMBER_MEASUREMENT_CAPABILITY_SET','protocol',j+1,message=m['artifact_id'])
    cluster_splits=defaultdict(set);pair_samples={}
    for i,r in enumerate(data['samples'],1):
        cluster_splits[r['cluster_id']].add(r['split'])
        if r['pair_id'] is not None:pair_samples[r['sample_id']]=r['pair_id']
        if phase=='observations' and lock and moment(r['registered_at'])>moment(lock['frozen_at']):issue('ENROLLMENT_AFTER_FREEZE','samples',i)
    for cluster,splits in cluster_splits.items():
        if len(splits)>1:issue('CLUSTER_SPLIT_LEAKAGE','samples',message=cluster)
    counts_per_sample=defaultdict(list)
    for i,e in enumerate(data['executions'],1):
        sr=samples.get((e['sample_id'],));a=arms.get(e['arm_id'])
        if sr is None:issue('EXECUTION_SAMPLE_FK','executions',i);continue
        sample=sr[1];counts_per_sample[e['sample_id']].append((i,e))
        if not sample['eligible']:issue('EXECUTION_OF_EXCLUDED_SAMPLE','executions',i)
        if a is None:issue('EXECUTION_ARM_FK','executions',i);continue
        if any(e[k]!=a[k] for k in ('candidate_id','task_id','source_report_id')) or sample['task_id']!=a['task_id']:
            issue('EXECUTION_SOURCE_ASSIGNMENT','executions',i)
        if any(s['population']!=sample['population'] or any(m['population']!=sample['population'] for member in s['members'] for m in member['measurements']) for s in a['stages']):
            issue('MEASUREMENT_POPULATION_MISMATCH','executions',i)
        if e['expected_stage_instance_ids']!=[s['stage_instance_id'] for s in a['stages']]:issue('EXPECTED_STAGE_SEQUENCE','executions',i)
        if e['execution_repeat_id']>p['planned_execution_repeats']:issue('UNPLANNED_EXECUTION_REPEAT','executions',i)
        if e['execution_input_reference']!=sample['baseline_input_reference']:issue('BASELINE_INPUT_MISMATCH','executions',i)
        if e['actual_order'] is not None and e['actual_order']!=e['assigned_order'] and not e['order_deviation_reason']:
            issue('UNEXPLAINED_EXECUTION_ORDER','executions',i)
        if e['started_at']:
            if moment(e['registered_at'])>moment(e['started_at']):issue('EXECUTION_REGISTERED_LATE','executions',i)
            if e['ended_at'] and moment(e['ended_at'])<moment(e['started_at']):issue('EXECUTION_TIME_ORDER','executions',i)
            if phase=='observations' and lock and moment(e['started_at'])<moment(lock['frozen_at']):issue('EXECUTION_BEFORE_FREEZE','executions',i)
        for s in a['stages']:
            if (e['execution_id'],s['stage_instance_id'],1) not in attempts:
                issue('MISSING_ACTUAL_FIRST_ATTEMPT_RECORD','stage_attempts',message='planned slot is retained in the study lock',execution_id=e['execution_id'],stage=s['stage_instance_id'])
    if p['sampling_design']=='paired_same_sample':
        for sample_id,record in samples.items():
            sample=record[1]
            if not sample['eligible']:continue
            if sample['pair_id'] is None:issue('PAIR_ID_MISSING','samples',record[0])
            expected={a['arm_id'] for a in p['arms'] if a['task_id']==sample['task_id']}
            if len(expected)<2:issue('PAIRED_DESIGN_NEEDS_TWO_ARMS','protocol',message=sample['task_id'])
            for repeat in range(1,p['planned_execution_repeats']+1):
                runs=[e for _,e in counts_per_sample[sample['sample_id']] if e['execution_repeat_id']==repeat]
                if {e['arm_id'] for e in runs}!=expected:issue('INCOMPLETE_REGISTERED_PAIR','executions',message=sample['sample_id'],repeat=repeat)
                if len({e['environment_stratum'] for e in runs})>1:issue('PAIRED_ENVIRONMENT_MISMATCH','executions',message=sample['sample_id'])
                if phase=='observations' and (p['planned_execution_repeats']>1 or len(runs)>1):
                    for e in runs:
                        if e['assigned_order']>1 and not e['reset_reference']:issue('PAIR_RESET_EVIDENCE_MISSING','executions',message=e['execution_id'])
    for _,(sample_row,sample) in samples.items():
        if not sample['eligible']:continue
        runs=counts_per_sample[sample['sample_id']]
        if not runs:issue('ELIGIBLE_SAMPLE_WITHOUT_EXECUTION','samples',sample_row)
        if p['sampling_design'] in ('single_arm','independent_arms'):
            for repeat in range(1,p['planned_execution_repeats']+1):
                if sum(e['execution_repeat_id']==repeat for _,e in runs)!=1:issue('ARM_ASSIGNMENT_COUNT','executions',message=sample['sample_id'],repeat=repeat)
        timed=sorted((e for _,e in runs if e['started_at']),key=lambda e:(moment(e['started_at']),e['execution_id']))
        for rank,e in enumerate(timed,1):
            if e['actual_order']!=rank:issue('ACTUAL_ORDER_TIME_MISMATCH','executions',message=e['execution_id'])
    def context(table,i,r):
        record=executions.get((r['execution_id'],))
        if not record:issue('OBSERVATION_EXECUTION_FK',table,i);return None,None,None
        e=record[1];a=arms.get(e['arm_id'])
        if r.get('sample_id',e['sample_id'])!=e['sample_id'] or r['candidate_id']!=e['candidate_id']:
            issue('OBSERVATION_SAMPLE_CANDIDATE_MISMATCH',table,i)
        stage=next((s for s in a['stages'] if s['stage_instance_id']==r.get('stage_instance_id')),None) if a else None
        return e,a,stage
    stage_runs=defaultdict(list)
    for i,r in enumerate(data['stage_attempts'],1):
        e,a,s=context('stage_attempts',i,r)
        if s is None:issue('ATTEMPT_STAGE_NOT_IN_SOURCE','stage_attempts',i);continue
        stage_runs[r['execution_id'],r['stage_instance_id']].append(r)
        if r['attempt_id']>p['maximum_attempts']:issue('RETRY_LIMIT','stage_attempts',i)
        if r['started_at']:
            if moment(r['registered_at'])>moment(r['started_at']):issue('ATTEMPT_REGISTERED_LATE','stage_attempts',i)
            if e and e['started_at'] and moment(r['started_at'])<moment(e['started_at']):issue('ATTEMPT_BEFORE_EXECUTION','stage_attempts',i)
        if r['started_at'] and r['ended_at'] and moment(r['ended_at'])<moment(r['started_at']):issue('ATTEMPT_TIME_ORDER','stage_attempts',i)
        if r['execution_status'] in ('planned','not_reached','not_executed') and (r['started_at'] or r['ended_at']):issue('NONEXECUTED_ATTEMPT_HAS_TIMES','stage_attempts',i)
        if phase=='observations' and r['execution_status']=='planned':issue('ATTEMPT_STILL_PLANNED','stage_attempts',i)
    for key,runs in stage_runs.items():
        runs=sorted(runs,key=lambda r:r['attempt_id'])
        if [r['attempt_id'] for r in runs]!=list(range(1,len(runs)+1)):issue('RETRY_NUMBER_GAP','stage_attempts',execution_id=key[0],stage=key[1])
        for before,after in zip(runs,runs[1:]):
            if before['ended_at'] and moment(after['registered_at'])<moment(before['ended_at']):
                issue('RETRY_REGISTERED_BEFORE_PREVIOUS_END','stage_attempts',execution_id=key[0],stage=key[1])
            if not before['ended_at'] or not after['started_at'] or moment(after['started_at'])<moment(before['ended_at']):
                issue('RETRY_TIME_OVERLAP','stage_attempts',execution_id=key[0],stage=key[1])
    for table in ('member_observations','group_outputs','task_endpoints'):
        for i,r in enumerate(data[table],1):
            e,a,s=context(table,i,r)
            if not e or not a:continue
            if r['output_reference'] and r['output_reference']==r['independent_reference']:issue('REFERENCE_EQUALS_OUTPUT',table,i)
            if table=='task_endpoints':
                if r['endpoint_event_id']!=a['endpoint_definition'] or r['evaluation_method']!=a['endpoint_method']:issue('ENDPOINT_EVENT_METHOD',table,i)
                refs=r['contributing_group_observation_ids']
            else:
                if s is None:issue('OBSERVATION_STAGE_NOT_IN_SOURCE',table,i);continue
                attempt=attempts.get((r['execution_id'],r['stage_instance_id'],r['attempt_id']))
                if not attempt:issue('OBSERVATION_ATTEMPT_FK',table,i)
                else:
                    if r['observation_status']=='recorded' and attempt[1]['execution_status'] in ('planned','not_reached','not_executed'):
                        issue('RECORDED_OUTPUT_WITHOUT_EXECUTION',table,i)
                    if attempt[1]['ended_at'] and moment(r['recorded_at'])<moment(attempt[1]['ended_at']):issue('OBSERVATION_BEFORE_ATTEMPT_END',table,i)
                if table=='member_observations':
                    member=next((m for m in s['members'] if m['artifact_id']==r['artifact_id']),None)
                    if member is None:issue('MEMBER_NOT_ASSIGNED',table,i)
                    elif r['capability_id'] not in member['capabilities']:issue('CAPABILITY_NOT_ASSIGNED',table,i)
                    else:
                        method=next((m['evaluation_method'] for m in member['measurements'] if m['capability_id']==r['capability_id']),None)
                        if method is None:issue('MEMBER_MEASUREMENT_NOT_DEFINED',table,i)
                        if r['artifact_version']!=member['artifact_version']:issue('ARTIFACT_VERSION_MISMATCH',table,i)
                        if r['evaluation_method']!=method:issue('MEMBER_METHOD_MISMATCH',table,i)
                    refs=r['input_group_observation_ids']
                else:
                    if r['hub']!=s['hub'] or r['evaluation_method']!=s['evaluation_method']:issue('GROUP_HUB_METHOD_MISMATCH',table,i)
                    expected_ids={mr[1]['observation_id'] for key,mr in members.items() if key[:3]==(r['execution_id'],r['stage_instance_id'],r['attempt_id'])}
                    if set(r['member_observation_ids'])!=expected_ids:issue('GROUP_MEMBER_REFERENCE_SET',table,i)
                    member_inputs={ref for key,mr in members.items() if key[:3]==(r['execution_id'],r['stage_instance_id'],r['attempt_id']) for ref in mr[1]['input_group_observation_ids']}
                    if set(r['input_group_observation_ids'])!=member_inputs:issue('GROUP_INPUT_UNION_MISMATCH',table,i)
                    for oid in r['member_observation_ids']:
                        other=obs.get(oid)
                        if not other or other[0]!='member_observations':issue('GROUP_MEMBER_REFERENCE_FK',table,i)
                        elif any(other[2][k]!=r[k] for k in ('execution_id','sample_id','stage_instance_id','attempt_id')):
                            issue('CROSS_EXECUTION_MEMBER_REFERENCE',table,i,observation_id=oid)
                    refs=r['input_group_observation_ids']
            for oid in refs:
                other=obs.get(oid)
                if not other or other[0]!='group_outputs':issue('GROUP_INPUT_REFERENCE_FK',table,i,observation_id=oid);continue
                upstream=other[2]
                if upstream['execution_id']!=r['execution_id'] or upstream['sample_id']!=r['sample_id']:
                    issue('CROSS_SAMPLE_GROUP_REFERENCE',table,i,observation_id=oid)
                if s and upstream['stage_instance_id'] not in s['full_prefix_stage_instance_ids']:issue('INPUT_NOT_EARLIER_STAGE',table,i)
                if s and upstream['attempt_id']!=1:
                    analysis_blockers.append(dict(code='RETRY_LINEAGE_OUTSIDE_FIRST_ATTEMPT_MODEL',table=table,row=i,observation_id=oid))
                if table=='member_observations' and len(refs)==1 and r['input_reference']!=upstream['output_reference']:issue('INPUT_OUTPUT_REFERENCE_MISMATCH',table,i)
                if s:
                    up_attempt=attempts.get((upstream['execution_id'],upstream['stage_instance_id'],upstream['attempt_id']))
                    down_attempt=attempts.get((r['execution_id'],r['stage_instance_id'],r['attempt_id']))
                    if up_attempt and down_attempt and up_attempt[1]['ended_at'] and down_attempt[1]['started_at'] and moment(up_attempt[1]['ended_at'])>moment(down_attempt[1]['started_at']):
                        issue('INPUT_NOT_AVAILABLE_AT_ATTEMPT_START',table,i,observation_id=oid)
            if table=='member_observations' and r['observation_status']=='recorded':
                if not r['input_reference']:issue('MISSING_INPUT_REFERENCE',table,i)
                elif not refs and r['input_reference']!=e['execution_input_reference']:issue('BASELINE_MEMBER_INPUT_MISMATCH',table,i)
                if len(refs)>1:analysis_blockers.append(dict(code='INPUT_BUNDLE_CONTENT_UNVERIFIED',table=table,row=i))
            if s and s['full_prefix_stage_instance_ids'] and not refs:
                analysis_blockers.append(dict(code='SERIAL_DEPENDENCY_NOT_ESTABLISHED',table=table,row=i,
                    message='Earlier planned stages do not establish actual data dependence.'))
    if phase=='observations':
        for (eid,stage_id,attempt),(_,r) in attempts.items():
            e=executions.get((eid,));a=arms.get(e[1]['arm_id']) if e else None
            stage=next((s for s in a['stages'] if s['stage_instance_id']==stage_id),None) if a else None
            if not stage:continue
            for m in stage['members']:
                for cap in m['capabilities']:
                    if (eid,stage_id,attempt,m['artifact_id'],cap) not in members:
                        issue('MISSING_MEMBER_OBSERVATION','member_observations',execution_id=eid,stage=stage_id,attempt_id=attempt,artifact_id=m['artifact_id'],capability_id=cap)
            if (eid,stage_id,attempt) not in groups:issue('MISSING_GROUP_OUTPUT','group_outputs',execution_id=eid,stage=stage_id,attempt_id=attempt)
        endpoint_execs={r['execution_id'] for r in data['task_endpoints']}
        for (eid,),(_,e) in executions.items():
            if eid not in endpoint_execs:issue('MISSING_TASK_ENDPOINT','task_endpoints',execution_id=eid)
        for table in ('member_observations','group_outputs','task_endpoints'):
            for i,r in enumerate(data[table],1):
                if r['observation_status']!='recorded' or r['reference_status']!='known':issue('OBSERVATION_UNASSESSABLE',table,i,message=r['missing_reason'])
    if phase=='observations':
        from .team_study_lock import projections
        if not lock or lock.get('version')!='team-study-lock-1.0':issue('STUDY_LOCK_MISSING','lock')
        else:
            if lock.get('lock_hash')!=sha({k:v for k,v in lock.items() if k!='lock_hash'}):issue('STUDY_LOCK_HASH','lock')
            if lock.get('protocol_hash')!=sha(p) or lock.get('study_id')!=p['study_id']:issue('LOCK_PROTOCOL_BINDING','lock')
            if lock.get('source_hashes')!={k:sha(v) for k,v in sources.items()}:issue('LOCK_SOURCE_BINDING','lock')
            if lock.get('plan_hash')!=sha(lock.get('plan')):issue('LOCK_PLAN_HASH','lock')
            actual=projections(data)
            for table in actual:
                if actual[table]!=lock.get('plan',{}).get(table):
                    issue('FROZEN_PLAN_CHANGED',table,message='planning history is immutable even if execution did not occur')
                    keys={'samples':['sample_id'],'executions':['execution_id'],'first_stage_attempts':['execution_id','stage_instance_id','attempt_id']}[table]
                    before={tuple(r[k] for k in keys):r for r in lock.get('plan',{}).get(table,[])}
                    after={tuple(r[k] for k in keys):r for r in actual[table]}
                    for key in sorted(set(before)-set(after)):issue('MISSING_FROZEN_PLAN_SLOT',table,key=list(key))
                    for key in sorted(set(after)-set(before)):issue('UNREGISTERED_PLAN_SLOT',table,key=list(key))
                    for key in sorted(set(before)&set(after)):
                        for column in before[key]:
                            if before[key][column]!=after[key][column]:
                                code='PAIR_ID_CHANGED' if column=='pair_id' else 'FROZEN_SPLIT_CHANGED' if column=='split' else 'FROZEN_PLAN_FIELD_CHANGED'
                                issue(code,table,column=column,key=list(key))
            if phase=='observations':
                for table in ('member_observations','group_outputs','task_endpoints'):
                    for i,r in enumerate(data[table],1):
                        if moment(r['recorded_at'])<moment(lock['frozen_at']):issue('OBSERVATION_BEFORE_FREEZE',table,i)
    ontology=dict(status='not_run',reason='source verification did not pass')
    if all(s.get('status')=='passed' for s in source_checks.values()) and set(source_checks)==set(source_defs):
        if phase=='observations':
            from .team_observation_owl import audit_owl
            try:ontology=audit_owl(data,p,sources)
            except (ValueError,TypeError,OSError,KeyError) as exc:ontology=dict(status='not_run',reason=str(exc))
            if ontology['status']!='passed':issue('OWL_OBSERVATION_CONSISTENCY','ontology',message=ontology.get('reason','OWL not completed'))
            for c in ontology.get('capability_support',[]):
                if c['status']!='entailed':issue('OWL_CAPABILITY_SUPPORT_UNKNOWN','member_observations',observation_id=c['observation_id'],artifact_id=c['artifact_id'],capability_id=c['capability_id'])
        else:ontology=dict(status='passed',scope='source chain fresh OWL verification; runtime observations not yet present')
    result=dict(version='team-observation-verification-1.0',status='passed' if not issues else 'blocked',issues=issues,
        phase=phase,source_verification=source_checks,ontology=ontology,
        verification_scope='declared packet identities, source bindings, temporal relationships and ontology constraints',
        external_reference_content_verified=False,live_equipment_version_authenticated=False,
        registered_plan_preserved=bool(lock and lock.get('plan')),confidence_intervals=None,
        independence_assumed=False,domain_validity='pending',probability_calibrated=False,production_writeback=False,
        counts=dict(samples=len(samples),clusters=len(cluster_splits),executions=len(executions),registered_attempts=len(attempts)),
        first_attempt_plan=lock.get('plan',{}).get('first_stage_attempts',[]) if lock else [])
    result.update(analysis_blockers=analysis_blockers,conditional_event_data_ready=not issues and not analysis_blockers,
                  conditional_model_validated=False,
                  counts_are_descriptive_only=True,aggregate_counts_unit='observation_rows_not_independent_samples',
                  issues_truncated=issue_total>len(issues),total_issue_count=issue_total)
    if phase=='observations':
        result['classification_counts']={table:dict(n_rows=len(data[table]),
            n_evaluable=sum(r.get('attempt_id',1)==1 and r['observation_status']=='recorded' and r['reference_status']=='known' for r in data[table]),
            n_correct=sum(r['actual_label']==r['reference_label'] for r in data[table] if r.get('attempt_id',1)==1 and r['observation_status']=='recorded' and r['reference_status']=='known'),
            n_missing=sum(r['observation_status']!='recorded' or r['reference_status']!='known' for r in data[table]))
            for table in ('member_observations','group_outputs','task_endpoints')}
        stratified={}
        for table in ('member_observations','group_outputs','task_endpoints'):
            bins=defaultdict(list)
            for r in data[table]:
                sample=samples.get((r['sample_id'],),(None,{}))[1]
                dimensions=('candidate_id','endpoint_event_id') if table=='task_endpoints' else (('candidate_id','stage_instance_id','artifact_id','artifact_version','capability_id') if table=='member_observations' else ('candidate_id','stage_instance_id'))
                key=tuple(r[k] for k in dimensions)+(sample.get('split'),sample.get('population'),sample.get('stratum'))
                bins[key].append(r)
            stratified[table]=[dict(stratum=dict(zip([*dimensions,'split','population','stratum'],key)),
                n_rows=len(rows),n_unique_samples=len({r['sample_id'] for r in rows}),
                n_evaluable_first_attempt=sum(r.get('attempt_id',1)==1 and r['observation_status']=='recorded' and r['reference_status']=='known' for r in rows),
                n_correct_first_attempt=sum(r['actual_label']==r['reference_label'] for r in rows if r.get('attempt_id',1)==1 and r['observation_status']=='recorded' and r['reference_status']=='known'))
                for key,rows in bins.items()]
        result['stratified_counts']=stratified
    return result


def _independent_reparse(files):
    """Read receipt bytes independently, not through load_packet/decode_cell."""
    def pairs(items):
        value={}
        for k,v in items:
            if k in value:raise ValueError('duplicate JSON key in independent replay')
            value[k]=v
        return value
    def invalid_constant(v):raise ValueError('nonfinite JSON in independent replay')
    def decode(role):return json.loads(files[role].decode('utf-8-sig'),object_pairs_hook=pairs,parse_constant=invalid_constant)
    first=decode('dataset')
    if 'table_files' not in first:data=first
    else:
        data={k:v for k,v in first.items() if k!='table_files'}
        schema=read_json(CONTRACT/'schema.json',allow_finite_floats=True)
        for table in TABLES:
            rows=[];props=schema['$defs'][table]['properties']
            reader=csv.reader(io.StringIO(files['table:'+table].decode('utf-8-sig'),newline=''))
            if next(reader,None)!=list(props):raise ValueError('independent CSV header mismatch')
            for cells in reader:
                if len(cells)!=len(props):raise ValueError('independent CSV width mismatch')
                row={}
                for name,cell in zip(props,cells):
                    if unicodedata.normalize('NFC',cell)!=cell:raise ValueError('non-NFC CSV text')
                    types=props[name].get('type',[]);types=[types] if isinstance(types,str) else types
                    if cell=='':value=None
                    elif 'array' in types:value=json.loads(cell)
                    elif 'boolean' in types:
                        if cell not in ('true','false'):raise ValueError('invalid Boolean')
                        value=cell=='true'
                    elif 'integer' in types:
                        if not re.fullmatch('[1-9][0-9]*',cell):raise ValueError('invalid integer')
                        value=int(cell)
                    else:value=cell
                    row[name]=value
                rows.append(row)
            data[table]=rows
    sources={role[len('source:'):]:decode(role) for role in files if role.startswith('source:')}
    manifest=decode('sources_manifest')
    if manifest.get('version')!='team-observation-sources-1.0' or {r['source_report_id'] for r in manifest['sources']}!=set(sources):
        raise ValueError('independent source manifest differs')
    return dict(dataset=data,protocol=decode('protocol'),lock=decode('lock'),sources=sources)


def verify_intake(directory,*,expected_receipt_hash=None):
    try:
        receipt=read_json(directory/'receipt.json',allow_finite_floats=True)
        if receipt.get('version')!='team-observation-receipt-1.0':raise ValueError('unsupported receipt version')
        if expected_receipt_hash is not None and sha(receipt)!=expected_receipt_hash:raise ValueError('externally held receipt hash differs')
        files={}
        for entry in receipt['files']:
            if entry['role'] in files:raise ValueError('duplicate receipt role')
            snapshot=(directory/entry['snapshot']).resolve()
            if not snapshot.is_relative_to(directory.resolve()):raise ValueError('snapshot path outside intake')
            raw=snapshot.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('raw snapshot bytes changed: '+entry['role'])
            original=Path(entry['original_path'])
            if original.exists() and hashlib.sha256(original.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('original input changed: '+entry['role'])
            files[entry['role']]=raw
        replay=_independent_reparse(files)
        normalized=read_json(directory/'normalized.json',allow_finite_floats=True)
        if replay!=normalized or sha(replay)!=receipt['normalized_sha256']:
            def difference(a,b,path=()):
                if type(a)!=type(b):return path
                if isinstance(a,dict):
                    if set(a)!=set(b):return path+('keys',)
                    for k in a:
                        if a[k]!=b[k]:return difference(a[k],b[k],path+(k,))
                elif isinstance(a,list):
                    if len(a)!=len(b):return path+('length',)
                    for i,(x,y) in enumerate(zip(a,b)):
                        if x!=y:return difference(x,y,path+(i,))
                return path
            raise ValueError('independent parse differs from normalized snapshot at '+str(list(difference(replay,normalized))))
        return check_package(replay['dataset'],replay['protocol'],replay['lock'],replay['sources'])
    except (ValueError,KeyError,TypeError,OSError,IndexError,AttributeError) as exc:
        return dict(version='team-observation-verification-1.0',status='blocked',issues=[dict(code='INTAKE_EVIDENCE_CHANGED',message=str(exc))],domain_validity='pending',production_writeback=False)


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--intake-dir',type=Path,required=True)
    parser.add_argument('--expected-receipt-hash');parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args(argv)
    if a.output.exists():raise ValueError('verification output exists')
    result=verify_intake(a.intake_dir,expected_receipt_hash=a.expected_receipt_hash)
    from .team_observation_intake import save
    save(a.output,result)
    return 0 if result['status']=='passed' else 2


if __name__=='__main__':raise SystemExit(main())
