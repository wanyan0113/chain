"""Versioned enrolled-cohort, attempt, lineage and independent-endpoint validation."""
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib
from pathlib import Path

from workflow_tools.accuracy_observations import canonical_hash,read_json,require,timestamp,ROOT
from workflow_tools.accuracy_validation import prediction,wilson
from workflow_tools.design_observation_bridge import build as source_binding
from workflow_tools.benchmark_joint import save

VERSION='tfdo-observation-study-2.0'
REASONS=['not_reached','planned_not_executed','execution_failure','output_lost','truth_missing','cannot_evaluate']
ENROLL={'enrollment_id','sample_id','cluster_id','execution_id','flow_id','split','stratum','eligible','exclusion_reason','expected_stages','registered_at'}
OBS={'observation_id','enrollment_id','sample_id','execution_id','flow_id','stage_instance_id','attempt_id',
     'activity_id','activity_version','artifact_id','artifact_version','input_reference','output_reference',
     'input_source_observation_id','input_label','gold_label','output_label','execution_status','observation_status',
     'stage_outcome','evaluation_method','ground_truth_reference','missing_reason','recorded_at'}
END={'enrollment_id','execution_id','endpoint_target_id','evaluation_method','actual_label','reference_label',
     'final_outcome','result_reference','ground_truth_reference','missing_reason','evaluated_at'}


def exact(row,fields,name):require(type(row) is dict and set(row)==fields,'invalid '+name+' fields')


def source_digest(report):return canonical_hash(report)


def skeleton(report):
    _,binding,_=source_binding(report)
    flows=[]
    for flow in binding['flows']:
        stages=[]
        for index,s in enumerate(flow['stages']):
            stages.append({k:s[k] for k in ('stage_instance_id','activity_id','activity_version','activity_type','artifact_id','artifact_version')})
            stages[-1].update(stage_target_id='binary-stage-target',evaluation_method='binary-equality@1',
                input_from_stage=flow['stages'][index-1]['stage_instance_id'] if index else None,
                calibration_input_condition='natural_case')
        flows.append(dict(flow_id=flow['candidate_id'],task_id=flow['task_id'],source_flow_id=flow['source_flow_id'],
            stages=stages,endpoint_target_id='independent-task-endpoint',endpoint_method='binary-equality@1',
            correction_design='unspecified'))
    return dict(version=VERSION,protocol=dict(study_id='',data_origin='uncollected',sampling_design='iid_subject',
        registration_mode='retrospective_analysis',stage_definition='',endpoint_definition='',
        evaluation_methods={'binary-equality@1':'Compare actual label to an independently supplied reference label'},
        attempt_rule='first_attempt',maximum_attempts=3,missing_reasons=REASONS,
        acceptance=dict(alpha=.05,tolerance=None,minimum_calibration=200,minimum_endpoint=200,minimum_stratum_class=50),
        strata=['all'],flows=flows),enrollments=[],observations=[],endpoints=[])


def validate_study(study,report):
    exact(study,{'version','protocol','enrollments','observations','endpoints'},'study')
    require(study['version']==VERSION,'unsupported v2 version')
    p=study['protocol'];expected=skeleton(report)['protocol'];source={f['flow_id']:f for f in expected['flows']}
    exact(p,set(expected),'protocol')
    require(all(type(p[k]) is str for k in ('study_id','stage_definition','endpoint_definition')),'study definitions must be text')
    require(p['data_origin'] in ('uncollected','synthetic_simulation','real_observational'),'invalid origin')
    require(p['sampling_design'] in ('iid_subject','clustered'),'invalid sampling design')
    require(p['registration_mode'] in ('prospective','retrospective_analysis'),'invalid registration mode')
    require(p['attempt_rule'] in ('first_attempt','last_attempt'),'unsupported attempt rule')
    require(type(p['maximum_attempts']) is int and 1<=p['maximum_attempts']<=10,'invalid attempt budget')
    require(p['missing_reasons']==REASONS,'missing-reason policy mismatch')
    require(type(p['strata']) is list and p['strata'] and all(type(s) is str and s.strip() for s in p['strata']) and len(set(p['strata']))==len(p['strata']),'invalid strata')
    require(type(p['evaluation_methods']) is dict and p['evaluation_methods'] and all(type(k) is str and k and type(v) is str and v.strip() for k,v in p['evaluation_methods'].items()),'invalid evaluation methods')
    require(type(p['flows']) is list and len(p['flows'])==len(source) and {f['flow_id'] for f in p['flows']}==set(source),'source candidate selection changed')
    for f in p['flows']:
        original=source[f['flow_id']];exact(f,set(original),'flow')
        require(f['task_id']==original['task_id'] and f['source_flow_id']==original['source_flow_id'],'task/flow identity mismatch')
        require(len(f['stages'])==len(original['stages']),'stage count mismatch')
        require(f['correction_design'] in ('unspecified','present','absent'),'invalid correction group')
        require(type(f['endpoint_target_id']) is str and f['endpoint_target_id'].strip() and f['endpoint_method'] in p['evaluation_methods'],'unknown endpoint definition')
        for index,(s,t) in enumerate(zip(f['stages'],original['stages'])):
            exact(s,set(t),'stage')
            require(all(s[k]==t[k] for k in ('stage_instance_id','activity_id','activity_version','activity_type','artifact_id')),'stage order or assignment mismatch')
            require(type(s['artifact_version']) is str and s['artifact_version']==s['artifact_version'].strip() and s['artifact_version'],'missing artifact version')
            require(type(s['stage_target_id']) is str and s['stage_target_id'].strip() and s['evaluation_method'] in p['evaluation_methods'],'missing stage evaluation definition')
            require(s['calibration_input_condition'] in ('natural_case','correct_reference_label'),'invalid calibration condition')
            require(s['input_from_stage'] is None or s['input_from_stage'] in [x['stage_instance_id'] for x in f['stages'][:index]],'input producer must be a declared earlier stage')
    a=p['acceptance'];exact(a,set(expected['acceptance']),'acceptance')
    require(type(a['alpha']) in (int,float) and 0<a['alpha']<.5,'invalid alpha')
    require(a['tolerance'] is None or (type(a['tolerance']) in (int,float) and 0<a['tolerance']<1),'invalid tolerance')
    require(all(type(a[k]) is int and a[k]>0 for k in ('minimum_calibration','minimum_endpoint','minimum_stratum_class')),'invalid sample coverage')
    require(all(type(study[k]) is list for k in ('enrollments','observations','endpoints')),'expected tables')
    canonical_hash(study)


def hashes():
    return {name:hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for name in
        ('workflow_tools/observations_v2.py','workflow_tools/accuracy_validation.py','task_flow/joint_candidates.py','task_flow/joint_verify.py')}


def freeze(study,report,path):
    validate_study(study,report);p=study['protocol']
    require(not path.exists(),'v2 lock exists')
    require(p['data_origin']!='uncollected' and p['study_id'] and p['stage_definition'] and p['endpoint_definition'] and p['acceptance']['tolerance'] is not None,'research definitions incomplete')
    require(all(s['artifact_version']!='UNASSIGNED' for f in p['flows'] for s in f['stages']),'actual versions unassigned')
    require(study['enrollments'],'enrolled cohort required')
    # Validate the registry before locking; empty observation tables are expected.
    pre=assess(study,report,None)
    require(not pre['integrity_issues'],'cannot freeze invalid enrollment/protocol data')
    from datetime import datetime,timezone
    lock=dict(version=VERSION,protocol_hash=canonical_hash(p),enrollment_hash=canonical_hash(study['enrollments']),
        protocol_snapshot=deepcopy(p),enrollment_snapshot=deepcopy(study['enrollments']),
        source_report_hash=source_digest(report),source_hashes=hashes(),frozen_at_utc=datetime.now(timezone.utc).isoformat(),
        domain_validity='pending')
    save(path,lock);return lock


def assess(study,report,lock):
    validate_study(study,report);p=study['protocol'];issues=[];block=[]
    def issue(code):issues.append(code)
    if lock is None:block.append('v2_study_not_frozen')
    else:
        if lock.get('version')!=VERSION:block.append('wrong_lock_contract_version')
        if lock.get('version')!=VERSION or lock.get('protocol_hash')!=canonical_hash(p):block.append('protocol_changed_since_freeze')
        if lock.get('enrollment_hash')!=canonical_hash(study['enrollments']):block.append('enrolled_cohort_changed_since_freeze')
        if lock.get('source_report_hash')!=source_digest(report):block.append('source_design_changed_since_freeze')
        if lock.get('source_hashes')!=hashes():block.append('implementation_changed_since_freeze')
        if canonical_hash(lock.get('protocol_snapshot'))!=lock.get('protocol_hash') or canonical_hash(lock.get('enrollment_snapshot'))!=lock.get('enrollment_hash'):
            block.append('locked_snapshot_integrity_failed')
    if p['sampling_design']!='iid_subject':block.append('clustered_inference_not_implemented')
    flows={f['flow_id']:f for f in p['flows']};enrolled={};executions=set();split_subject={};split_cluster={};clusters=defaultdict(set)
    def time_check(value,is_measurement=True):
        try:
            t=timestamp(value)
            from datetime import datetime,timezone
            if t>datetime.now(timezone.utc):issue('future_timestamp')
            if is_measurement and lock and p['registration_mode']=='prospective' and t<timestamp(lock['frozen_at_utc']):issue('prospective_record_precedes_freeze')
            return t
        except (TypeError,ValueError,KeyError):issue('invalid_timestamp');return None
    for r in study['enrollments']:
        exact(r,ENROLL,'enrollment')
        require(all(type(r[k]) is str and r[k]==r[k].strip() and r[k] for k in ('enrollment_id','sample_id','cluster_id','execution_id','flow_id','stratum')),'invalid enrollment identity')
        if r['enrollment_id'] in enrolled or r['execution_id'] in executions:issue('duplicate_enrollment_or_execution');continue
        if r['flow_id'] not in flows or r['split'] not in ('calibration','evaluation') or r['stratum'] not in p['strata']:issue('unknown_enrollment_scope');continue
        require(type(r['eligible']) is bool and type(r['exclusion_reason']) is str,'invalid eligibility')
        expected=[s['stage_instance_id'] for s in flows[r['flow_id']]['stages']]
        require(type(r['expected_stages']) is list and len(set(r['expected_stages']))==len(r['expected_stages']) and set(r['expected_stages'])<=set(expected),'invalid expected stages')
        if r['eligible'] and r['split']=='evaluation' and r['expected_stages']!=expected:issue('evaluation_expected_route_changed')
        if not r['eligible'] and (not r['exclusion_reason'] or r['expected_stages']):issue('excluded_subject_missing_reason_or_has_route')
        for mapping,key in ((split_subject,r['sample_id']),(split_cluster,r['cluster_id'])):
            if key in mapping and mapping[key]!=r['split']:issue('calibration_evaluation_leakage')
            mapping[key]=r['split']
        clusters[(r['flow_id'],r['split'],r['cluster_id'])].add(r['enrollment_id'])
        enrolled[r['enrollment_id']]=r;executions.add(r['execution_id']);time_check(r['registered_at'],False)
    if any(len(v)>1 for v in clusters.values()):block.append('dependent_units_require_cluster_method')
    groups=defaultdict(list);by_id={}
    for r in study['observations']:
        exact(r,OBS,'stage observation');enrollment=enrolled.get(r['enrollment_id'])
        require(type(r['observation_id']) is str and r['observation_id'].strip(),'missing observation ID')
        if enrollment is None:issue('observation_not_in_registered_cohort');continue
        if r['observation_id'] in by_id:issue('duplicate_observation_id');continue
        if any(r[k]!=enrollment[k] for k in ('sample_id','execution_id','flow_id')):issue('observation_subject_or_execution_mismatch');continue
        stage=next((s for s in flows[r['flow_id']]['stages'] if s['stage_instance_id']==r['stage_instance_id']),None)
        if stage is None or r['stage_instance_id'] not in enrollment['expected_stages']:issue('unexpected_stage_observation');continue
        if any(r[k]!=stage[k] for k in ('activity_id','activity_version','artifact_id','artifact_version','evaluation_method')):issue('stage_identity_version_or_method_mismatch');continue
        require(type(r['attempt_id']) is int and 1<=r['attempt_id']<=p['maximum_attempts'],'invalid attempt ID')
        require(r['execution_status'] in ('executed','not_reached','not_executed','execution_failed') and r['observation_status'] in ('recorded','missing'),'invalid stage statuses')
        require(r['stage_outcome'] in ('correct','incorrect','unassessable'),'invalid stage outcome')
        known=r['stage_outcome']!='unassessable'
        expected_reason={'not_reached':'not_reached','not_executed':'planned_not_executed','execution_failed':'execution_failure'}
        if r['execution_status']!='executed':
            if known or r['missing_reason']!=expected_reason[r['execution_status']] or r['output_label']:
                issue('execution_status_reason_or_output_inconsistent')
            if r['execution_status'] in ('not_reached','not_executed') and (r['output_reference'] or r['input_source_observation_id']):
                issue('unexecuted_stage_claims_input_or_output')
        elif r['observation_status']=='missing':
            if known or r['missing_reason']!='output_lost' or r['output_label'] or r['output_reference']:issue('missing_output_status_inconsistent')
        elif not known and r['missing_reason'] not in ('truth_missing','cannot_evaluate'):
            issue('recorded_unassessable_reason_inconsistent')
        if r['execution_status']=='executed' and not r['input_reference']:issue('input_reference_missing')
        if known:
            if r['execution_status']!='executed' or r['observation_status']!='recorded' or r['missing_reason']:issue('assessed_stage_has_missing_or_unexecuted_status')
            if r['gold_label'] not in ('0','1') or r['output_label'] not in ('0','1','ABSTAIN'):issue('missing_stage_truth_or_output')
            elif (r['stage_outcome']=='correct')!=(r['output_label']==r['gold_label']):issue('stage_outcome_disagrees_with_labels')
            if not r['output_reference'] or not r['ground_truth_reference']:issue('stage_evidence_reference_missing')
            if r['output_reference']==r['ground_truth_reference']:issue('stage_truth_reference_is_its_output')
            if enrollment['split']=='calibration' and stage['calibration_input_condition']=='correct_reference_label' and r['input_label']!=r['gold_label']:issue('calibration_input_condition_violated')
        elif r['missing_reason'] not in p['missing_reasons']:issue('unassessable_stage_needs_reason')
        key=(r['enrollment_id'],r['stage_instance_id'])
        if any(o['attempt_id']==r['attempt_id'] for o in groups[key]):issue('duplicate_stage_attempt');continue
        groups[key].append(r);by_id[r['observation_id']]=r;time_check(r['recorded_at'])
    selected={}
    for key,attempts in groups.items():
        attempts.sort(key=lambda r:r['attempt_id'])
        if [r['attempt_id'] for r in attempts]!=list(range(1,len(attempts)+1)):issue('attempt_sequence_has_gap')
        try:
            times=[timestamp(r['recorded_at']) for r in attempts]
            if times!=sorted(times):issue('attempt_time_order_mismatch')
        except (ValueError,TypeError):issue('invalid_attempt_timestamp')
        selected[key]=attempts[0] if p['attempt_rule']=='first_attempt' else attempts[-1]
    for r in by_id.values():
        enrollment=enrolled[r['enrollment_id']]
        if enrollment['split']=='calibration':
            if r['input_source_observation_id']:issue('isolated_calibration_has_undeclared_producer')
            continue
        if r['execution_status'] not in ('executed','execution_failed'):continue
        stage=next(s for s in flows[r['flow_id']]['stages'] if s['stage_instance_id']==r['stage_instance_id'])
        expected=stage['input_from_stage'];source=by_id.get(r['input_source_observation_id'])
        if expected is None:
            if r['input_source_observation_id']:issue('undeclared_input_producer')
        elif source is None:issue('input_producer_missing')
        else:
            if source['enrollment_id']!=r['enrollment_id'] or source['stage_instance_id']!=expected:issue('input_producer_wrong_subject_or_stage')
            if not source['output_reference'] or source['output_reference']!=r['input_reference']:issue('input_output_reference_mismatch')
            try:
                if timestamp(source['recorded_at'])>timestamp(r['recorded_at']):issue('input_producer_is_in_future')
            except (ValueError,TypeError):issue('invalid_lineage_timestamp')
    endpoints={}
    for r in study['endpoints']:
        exact(r,END,'independent endpoint');entry=enrolled.get(r['enrollment_id'])
        if entry is None or entry['split']!='evaluation' or not entry['eligible']:issue('endpoint_not_in_eligible_cohort');continue
        flow=flows[entry['flow_id']]
        if r['enrollment_id'] in endpoints:issue('duplicate_independent_endpoint');continue
        if r['execution_id']!=entry['execution_id'] or r['endpoint_target_id']!=flow['endpoint_target_id'] or r['evaluation_method']!=flow['endpoint_method']:issue('endpoint_identity_or_method_mismatch')
        if r['final_outcome'] in ('success','failure'):
            if r['actual_label'] not in ('0','1') or r['reference_label'] not in ('0','1'):issue('endpoint_truth_or_observation_missing')
            elif (r['final_outcome']=='success')!=(r['actual_label']==r['reference_label']):issue('endpoint_outcome_disagrees_with_labels')
            if not r['result_reference'] or not r['ground_truth_reference'] or r['missing_reason']:issue('endpoint_evidence_incomplete')
            if r['result_reference']==r['ground_truth_reference']:issue('endpoint_truth_reference_is_its_result')
        elif r['final_outcome']!='unassessable' or r['missing_reason'] not in p['missing_reasons']:issue('unknown_endpoint_needs_reason')
        endpoints[r['enrollment_id']]=r;time_check(r['evaluated_at'])
    reports=[];a=p['acceptance']
    for flow in p['flows']:
        fid=flow['flow_id'];stage_stats=[];cal_stats=[]
        cohort=[r for r in enrolled.values() if r['flow_id']==fid and r['eligible']]
        for split in ('calibration','evaluation'):
            for stage in flow['stages']:
                sid=stage['stage_instance_id'];entries=[r for r in cohort if r['split']==split and sid in r['expected_stages']]
                chosen=[selected[(r['enrollment_id'],sid)] for r in entries if (r['enrollment_id'],sid) in selected]
                counts=Counter(n_expected=len(entries),n_recorded=len(chosen),n_attempt_records=sum(len(groups[(r['enrollment_id'],sid)]) for r in entries),
                    n_attempted_executions=sum(o['execution_status'] in ('executed','execution_failed') for r in entries for o in groups[(r['enrollment_id'],sid)]))
                reasons=Counter();evaluable=[]
                for row in chosen:
                    counts['n_executed']+=row['execution_status'] in ('executed','execution_failed')
                    if row['stage_outcome']!='unassessable':evaluable.append(row)
                    else:reasons[row['missing_reason']]+=1
                counts['n_evaluable']=len(evaluable);counts['n_correct']=sum(r['stage_outcome']=='correct' for r in evaluable)
                counts['n_unrecorded']=counts['n_expected']-counts['n_recorded']
                if len(evaluable)!=len(entries):block.append('stage_coverage_incomplete:'+fid+':'+sid+':'+split)
                if split=='calibration' and len(evaluable)<a['minimum_calibration']:block.append('calibration_too_small:'+fid+':'+sid)
                coverage=Counter((enrolled[r['enrollment_id']]['stratum'],r['gold_label']) for r in evaluable)
                if split=='calibration' and any(coverage[(s,label)]<a['minimum_stratum_class'] for s in p['strata'] for label in ('0','1')):block.append('calibration_stratum_coverage_insufficient:'+fid+':'+sid)
                item=dict(stage_instance_id=sid,activity_type=stage['activity_type'],artifact_version=stage['artifact_version'],split=split,
                    counts=dict(counts),missing_reasons=dict(reasons),rate=counts['n_correct']/len(evaluable) if evaluable else None)
                stage_stats.append(item)
                if split=='calibration':cal_stats.append(item)
        population=[r for r in cohort if r['split']=='evaluation'];known=[endpoints[r['enrollment_id']] for r in population if r['enrollment_id'] in endpoints and endpoints[r['enrollment_id']]['final_outcome']!='unassessable']
        missing_final=[r['enrollment_id'] for r in population if r['enrollment_id'] not in endpoints or endpoints[r['enrollment_id']]['final_outcome']=='unassessable']
        if missing_final:block.append('independent_endpoint_coverage_incomplete:'+fid)
        if len(known)<a['minimum_endpoint']:block.append('endpoint_too_small:'+fid)
        successes=sum(r['final_outcome']=='success' for r in known)
        wholly_absent=[r['enrollment_id'] for r in population if not any((r['enrollment_id'],s['stage_instance_id']) in selected for s in flow['stages'])]
        reports.append(dict(flow_id=fid,task_id=flow['task_id'],candidate_id=fid,stage_statistics=stage_stats,
            enrollment_accounting={split:dict(n_registered=sum(e['flow_id']==fid and e['split']==split for e in enrolled.values()),
                excluded=[dict(enrollment_id=e['enrollment_id'],reason=e['exclusion_reason']) for e in enrolled.values() if e['flow_id']==fid and e['split']==split and not e['eligible']])
                for split in ('calibration','evaluation')},
            group=dict(stage_count=len(flow['stages']),activity_type_sequence=[s['activity_type'] for s in flow['stages']],
                artifact_versions=[s['artifact_version'] for s in flow['stages']],correction_design=flow['correction_design']),
            endpoint=dict(n_registered_eligible=len(population),n_evaluable=len(known),n_success=successes,missing_enrollments=missing_final,
                wholly_unrecorded_enrollments=wholly_absent,rate=successes/len(known) if known else None,
                definition='independent endpoint table, not product or last stage label'),statistical_assessment=None))
    if issues:block.append('v2_observation_integrity_failed')
    if p['data_origin']=='uncollected' or a['tolerance'] is None:block.append('research_definitions_incomplete')
    if not enrolled:block.append('no_registered_cohort')
    block=sorted(set(block))
    if not block:
        import math
        for f in reports:
            cal=[s for s in f['stage_statistics'] if s['split']=='calibration'];alpha=a['alpha']/(len(cal)+1)
            bounds=[wilson(s['counts']['n_correct'],s['counts']['n_evaluable'],alpha) for s in cal]
            pred=prediction([s['rate'] for s in cal]);ep=f['endpoint'];interval=wilson(ep['n_success'],ep['n_evaluable'],alpha)
            low=interval[0]-math.prod(v[1] for v in bounds);high=interval[1]-math.prod(v[0] for v in bounds)
            tol=a['tolerance'];decision='equivalent_within_tolerance' if low>=-tol and high<=tol else 'product_underpredicts' if low>tol else 'product_overpredicts' if high<-tol else 'inconclusive'
            f['statistical_assessment']=dict(predicted=pred,observed=ep['rate'],difference=ep['rate']-pred,discrepancy_ci=[low,high],decision=decision)
    return dict(version=VERSION,status='blocked' if block else 'analyzed',blockers=block,integrity_issues=sorted(set(issues)),
        registered_enrollments=len(enrolled),observations=len(study['observations']),independent_endpoints=len(endpoints),
        protocol_hash=canonical_hash(p),enrollment_hash=canonical_hash(study['enrollments']),flows=reports,
        observation_table_hash=canonical_hash(study['observations']),endpoint_table_hash=canonical_hash(study['endpoints']),
        source_global_optimality=report['global_optimality'],raw_metric_policy='sequential_product_v2',
        formula_changed=False,domain_validity='pending',data_origin=p['data_origin'],causal_correction_proven=False)


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['freeze','analyze'])
    parser.add_argument('--study',type=Path,required=True);parser.add_argument('--joint-report',type=Path,required=True)
    parser.add_argument('--lock',type=Path,required=True);parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args(argv);study=read_json(args.study);report=read_json(args.joint_report)
    if args.command=='freeze':
        require(args.output_dir is None,'freeze does not use output directory');freeze(study,report,args.lock)
        print('v2 protocol and enrolled cohort frozen');return 0
    require(args.output_dir is not None and not args.output_dir.exists(),'choose a new result directory')
    result=assess(study,report,read_json(args.lock))
    save(args.output_dir/'report.json',result);save(args.output_dir/'study-snapshot.json',study)
    save(args.output_dir/'source-joint-snapshot.json',report);save(args.output_dir/'lock-snapshot.json',read_json(args.lock))
    print(result['status']+'; domain_validity=pending');return 0 if result['status']=='analyzed' else 2


if __name__=='__main__':raise SystemExit(main())
