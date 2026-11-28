"""Natural observation intake; no fault injection or shadow labels required."""
from collections import Counter,defaultdict
from datetime import datetime,timezone
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import unicodedata

from workflow_tools.accuracy_validation import prediction,wilson

ROOT=Path(__file__).resolve().parents[1]
VERSION='tfdo-observation-intake-1.0'
FIELDS=['observation_id','sample_id','cluster_id','split','flow_id','stage_instance_id',
        'activity_id','activity_version','artifact_id','artifact_version','stratum',
        'gold_label','input_label','output_label','status','label_source_id','recorded_at']
STAGE_FIELDS={'stage_instance_id','activity_id','activity_version','artifact_id','artifact_version','calibration_input_condition'}
SOURCES=['workflow_tools/accuracy_observations.py','workflow_tools/validate_accuracy_observations.py',
         'workflow_tools/accuracy_validation.py','task_flow/joint_candidates.py']
ABSTAIN='__ABSTAIN__'


def require(value,message):
    if not value:raise ValueError(message)


def read_json(path):
    def pairs(items):
        result={}
        for k,v in items:
            require(k not in result,'duplicate JSON key');result[k]=v
        return result
    def bad(value):raise ValueError('non-finite JSON value')
    return json.loads(path.read_text(encoding='utf-8-sig'),object_pairs_hook=pairs,parse_constant=bad)


def canonical_hash(value):
    text=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    require(unicodedata.normalize('NFC',text)==text,'protocol must use NFC text')
    return hashlib.sha256(text.encode()).hexdigest()


def source_hashes():
    return {p:hashlib.sha256((ROOT/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in SOURCES}


def timestamp(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    require(result.tzinfo is not None,'timestamp needs timezone')
    return result


def validate_protocol(p):
    keys={'version','study_id','data_origin','registration_mode','target_definition','label_space',
          'ground_truth_method_review','sampling_design','evaluation_population','label_sources','strata','flows','acceptance'}
    require(type(p) is dict and set(p)==keys,'unexpected protocol fields')
    require(p['version']==VERSION,'unsupported protocol version')
    require(p['data_origin'] in ('uncollected','synthetic_fixture','real_observational'),'unknown data origin')
    require(p['registration_mode'] in ('prospective','retrospective_analysis'),'unknown registration mode')
    require(p['sampling_design'] in ('iid_subject','clustered','unknown'),'unknown sampling design')
    require(p['ground_truth_method_review'] in ('pending','verified','rejected'),'unknown truth-method review status')
    for key in ('study_id','target_definition','evaluation_population'):require(type(p[key]) is str,'protocol text expected')
    require(type(p['label_space']) is list and len(p['label_space'])==2 and
            all(type(v) is str and v.strip() and v!=ABSTAIN for v in p['label_space']) and len(set(p['label_space']))==2,'exactly two distinct labels required')
    require(type(p['strata']) is list and bool(p['strata']) and all(type(x) is str and x.strip() for x in p['strata']) and
            len(set(p['strata']))==len(p['strata']),'invalid strata')
    require(type(p['label_sources']) is dict and all(type(k) is str and k.strip() and type(v) is str and v.strip()
            for k,v in p['label_sources'].items()),'invalid label sources')
    require(type(p['flows']) is list and 1<=len(p['flows'])<=50,'expected 1..50 flows')
    ids=set()
    for flow in p['flows']:
        require(type(flow) is dict and set(flow)=={'flow_id','calibration_population','input_from_previous_output','stages'},'invalid flow fields')
        require(type(flow['flow_id']) is str and flow['flow_id'].strip() and flow['flow_id'] not in ids,'duplicate/empty flow')
        ids.add(flow['flow_id']);require(type(flow['calibration_population']) is str,'missing population declaration')
        require(type(flow['input_from_previous_output']) is bool,'explicit continuity declaration required')
        require(type(flow['stages']) is list and 1<=len(flow['stages'])<=30,'expected 1..30 stages')
        stages=set()
        for stage in flow['stages']:
            require(type(stage) is dict and set(stage)==STAGE_FIELDS,'invalid stage fields')
            require(all(type(v) is str and v.strip() for v in stage.values()),'stage identity/version must be nonempty text')
            require(stage['stage_instance_id'] not in stages,'duplicate stage instance')
            stages.add(stage['stage_instance_id'])
            require(stage['calibration_input_condition'] in ('natural_case','correct_reference_label'),'unsupported calibration condition')
    a=p['acceptance']
    require(type(a) is dict and set(a)=={'absolute_error_tolerance','alpha','minimum_stage_calibration','minimum_complete_evaluation','minimum_stratum_class'},'invalid acceptance policy')
    tol=a['absolute_error_tolerance']
    require(tol is None or (type(tol) in (int,float) and math.isfinite(tol) and 0<tol<1),'invalid tolerance')
    require(type(a['alpha']) in (int,float) and math.isfinite(a['alpha']) and 0<a['alpha']<.5,'invalid alpha')
    require(all(type(a[k]) is int and a[k]>0 for k in ('minimum_stage_calibration','minimum_complete_evaluation','minimum_stratum_class')),'positive sample budgets required')
    canonical_hash(p)
    return p


def readiness(p):
    blockers=[]
    if p['data_origin']=='uncollected':blockers.append('no_real_data_collected')
    if not all(p[k].strip() for k in ('study_id','target_definition','evaluation_population')):blockers.append('incomplete_study_definition')
    if not p['label_sources']:blockers.append('missing_label_source_definition')
    if p['ground_truth_method_review']!='verified':blockers.append('ground_truth_method_not_reviewed')
    if p['sampling_design']!='iid_subject':blockers.append('iid_subject_inference_not_supported_for_declared_sampling')
    if p['acceptance']['absolute_error_tolerance'] is None:blockers.append('tolerance_not_prespecified')
    for flow in p['flows']:
        if any(stage[k]=='UNASSIGNED' for stage in flow['stages'] for k in ('activity_id','activity_version','artifact_id','artifact_version')):
            blockers.append('unassigned_stage_identity:'+flow['flow_id'])
        if not flow['calibration_population'].strip():blockers.append('missing_calibration_population:'+flow['flow_id'])
        elif flow['calibration_population']!=p['evaluation_population']:blockers.append('population_mismatch:'+flow['flow_id'])
    return blockers


def freeze_protocol(p,path):
    validate_protocol(p);require(not readiness(p),'protocol not ready: '+','.join(readiness(p)))
    require(not path.exists(),'lock already exists')
    lock=dict(version=VERSION,protocol_hash=canonical_hash(p),source_hashes=source_hashes(),
              frozen_at_utc=datetime.now(timezone.utc).isoformat(),registration_mode=p['registration_mode'],
              scope='integrity lock; does not certify provenance or independent human review')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(lock,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return lock


def analyze(p,csv_path,lock=None,*,observations_bytes=None):
    validate_protocol(p);blockers=readiness(p);issues=[];issue_count=0
    snapshot=csv_path.read_bytes() if observations_bytes is None else observations_bytes
    def issue(code,row=None):
        nonlocal issue_count
        issue_count+=1
        if len(issues)<100:issues.append(dict(code=code,row=row))
    if lock is None:blockers.append('protocol_not_frozen')
    else:
        if lock.get('version')!=VERSION or lock.get('protocol_hash')!=canonical_hash(p):blockers.append('protocol_lock_mismatch')
        if lock.get('source_hashes')!=source_hashes():blockers.append('source_changed_since_freeze')
        try:frozen_time=timestamp(lock['frozen_at_utc'])
        except (ValueError,KeyError,TypeError):blockers.append('invalid_lock_time');frozen_time=None
    flows={f['flow_id']:f for f in p['flows']};groups=defaultdict(dict)
    observations=set();subjects={};split_by_subject={};split_by_cluster={};cluster_subjects=defaultdict(set)
    counts=Counter();labels=set(p['label_space']);valid_status={'observed','abstained'}
    with io.StringIO(snapshot.decode('utf-8-sig'),newline='') as handle:
        reader=csv.DictReader(handle);require(reader.fieldnames==FIELDS,'unexpected CSV columns')
        for number,row in enumerate(reader,2):
            counts['rows']+=1
            if None in row or any(v is None for v in row.values()):issue('malformed_row',number);continue
            if any(not row[k].strip() for k in ('observation_id','sample_id','cluster_id')):issue('missing_identity',number);continue
            if any(row[k]!=row[k].strip() or row[k]!=unicodedata.normalize('NFC',row[k]) for k in ('observation_id','sample_id','cluster_id')):
                issue('noncanonical_identity',number);continue
            if row['observation_id'] in observations:issue('duplicate_observation',number);continue
            observations.add(row['observation_id'])
            split=row['split'];fid=row['flow_id'];sid=row['stage_instance_id']
            if split not in ('calibration','evaluation') or fid not in flows:issue('unknown_split_or_flow',number);continue
            stage=next((s for s in flows[fid]['stages'] if s['stage_instance_id']==sid),None)
            if stage is None:issue('unknown_stage_instance',number);continue
            if any(row[k]!=stage[k] for k in ('activity_id','activity_version','artifact_id','artifact_version')):
                issue('activity_or_artifact_version_mismatch',number);continue
            if row['stratum'] not in p['strata']:issue('unknown_stratum',number);continue
            if row['status'] not in ('observed','abstained','missing','not_executed'):issue('unknown_status',number);continue
            counts['status:'+row['status']]+=1
            if row['gold_label'] and row['gold_label'] not in labels:issue('invalid_ground_truth_label',number);continue
            if row['label_source_id'] and row['label_source_id'] not in p['label_sources']:issue('unknown_label_source',number);continue
            if row['status'] in valid_status:
                if row['gold_label'] not in labels or row['label_source_id'] not in p['label_sources']:
                    issue('missing_or_unknown_ground_truth',number);continue
                if ((row['status']=='observed' and row['output_label'] not in labels) or
                    (row['status']=='abstained' and row['output_label'])):issue('invalid_output_label',number);continue
            else:
                blockers.append('incomplete_observations')
                if row['output_label']:issue('unobserved_record_has_output',number);continue
            if row['input_label'] and row['input_label'] not in labels|{ABSTAIN}:issue('invalid_input_label',number);continue
            if split=='calibration' and row['status'] in valid_status and stage['calibration_input_condition']=='correct_reference_label' and row['input_label']!=row['gold_label']:
                issue('calibration_input_condition_violated',number);continue
            try:
                recorded=timestamp(row['recorded_at'])
                if recorded>datetime.now(timezone.utc):issue('observation_time_in_future',number)
                if lock is not None and p['registration_mode']=='prospective' and frozen_time and recorded<frozen_time:
                    issue('prospective_observation_precedes_freeze',number)
            except (ValueError,TypeError):issue('missing_or_invalid_observation_time',number)
            subject=row['sample_id'];cluster=row['cluster_id'];gold=row['gold_label']
            if subject in split_by_subject and split_by_subject[subject]!=split:issue('subject_leaks_across_splits',number)
            if cluster in split_by_cluster and split_by_cluster[cluster]!=split:issue('cluster_leaks_across_splits',number)
            split_by_subject[subject]=split;split_by_cluster[cluster]=split;cluster_subjects[cluster].add(subject)
            identity=(cluster,row['stratum'])
            if subject in subjects and (subjects[subject][0]!=identity or (gold and subjects[subject][1] and gold!=subjects[subject][1])):
                issue('subject_ground_truth_or_population_conflict',number)
            else:subjects[subject]=(identity,gold or subjects.get(subject,(None,''))[1])
            key=(split,fid,subject)
            if sid in groups[key]:issue('duplicate_stage_observation',number);continue
            groups[key][sid]=row
    if not counts['rows']:blockers.append('no_observations')
    if any(len(ids)>1 for ids in cluster_subjects.values()):blockers.append('dependent_subject_clusters_require_other_ci_method')
    diagnostics=[];a=p['acceptance']
    for fid,flow in flows.items():
        stages=flow['stages'];cal=[];cal_coverage=[]
        evaluations=[records for (split,f,subject),records in groups.items() if split=='evaluation' and f==fid]
        complete=[]
        for records in evaluations:
            if not all(s['stage_instance_id'] in records and records[s['stage_instance_id']]['status'] in valid_status for s in stages):
                blockers.append('incomplete_evaluation_flow:'+fid);continue
            sequence=[records[s['stage_instance_id']] for s in stages]
            if flow['input_from_previous_output']:
                for previous,current in zip(sequence,sequence[1:]):
                    expected=previous['output_label'] if previous['status']=='observed' else ABSTAIN
                    if current['input_label']!=expected:issue('serial_input_continuity_mismatch')
            complete.append(sequence)
        eval_coverage=Counter((seq[0]['stratum'],seq[0]['gold_label']) for seq in complete)
        correct=lambda r:r['status']=='observed' and r['output_label']==r['gold_label']
        for stage in stages:
            records=[g[stage['stage_instance_id']] for (split,f,subject),g in groups.items() if split=='calibration' and f==fid and
                     stage['stage_instance_id'] in g and g[stage['stage_instance_id']]['status'] in valid_status]
            n=len(records);k=sum(correct(r) for r in records)
            coverage=Counter((r['stratum'],r['gold_label']) for r in records);cal_coverage.append(coverage)
            cal.append(dict(stage_instance_id=stage['stage_instance_id'],n=n,correct=k,rate=k/n if n else None,ci95=None,
                stratum_class_coverage=[dict(stratum=key[0],label=key[1],n=value) for key,value in sorted(coverage.items())]))
        if any(c['n']<a['minimum_stage_calibration'] for c in cal):blockers.append('insufficient_stage_calibration:'+fid)
        if len(complete)<a['minimum_complete_evaluation']:blockers.append('insufficient_complete_evaluation:'+fid)
        if any(c[(s,label)]<a['minimum_stratum_class'] for c in cal_coverage+[eval_coverage] for s in p['strata'] for label in labels):
            blockers.append('insufficient_stratum_class_coverage:'+fid)
        transitions=[]
        for index,stage in enumerate(stages):
            table=Counter()
            for sequence in complete:
                row=sequence[index]
                if row['input_label']:table[(row['input_label']==row['gold_label'],correct(row))]+=1
            conditional=[]
            for state in (True,False):
                n=sum(v for (input_ok,output_ok),v in table.items() if input_ok==state)
                k=table[(state,True)]
                conditional.append(dict(input_correct=state,n=n,output_correct=k,rate=k/n if n else None,
                    ci95=None,reason=None if n else 'input_quality_unobserved_or_no_eligible_samples'))
            recoveries=sum(not correct(seq[index-1]) and correct(seq[index]) for seq in complete) if index else None
            transitions.append(dict(stage_instance_id=stage['stage_instance_id'],direct_input_conditionals=conditional,
                previous_output_recoveries=recoveries,causal_correction_claim=False))
        endpoint_correct=sum(correct(seq[-1]) for seq in complete)
        diagnostics.append(dict(flow_id=fid,calibration=cal,evaluation_subjects=len(evaluations),complete_evaluation=len(complete),
            endpoint_correct=endpoint_correct,endpoint_rate=endpoint_correct/len(complete) if complete else None,
            endpoint_ci95=None,
            input_output_diagnostics=transitions,raw_population_coverage=dict(evaluation=[dict(stratum=k[0],label=k[1],n=v) for k,v in sorted(eval_coverage.items())]),
            statistical_assessment=None))
    if issue_count:blockers.append('observation_integrity_failed')
    blockers=sorted(set(blockers))
    if not blockers:
        for flow in diagnostics:
            length=len(flow['calibration']);alpha=a['alpha']/(length+1)
            rates=[r['rate'] for r in flow['calibration']]
            for row in flow['calibration']:row['ci95']=wilson(row['correct'],row['n'],a['alpha'])
            intervals=[wilson(r['correct'],r['n'],alpha) for r in flow['calibration']]
            predicted=prediction(rates);observed=flow['endpoint_rate']
            observed_ci=wilson(flow['endpoint_correct'],flow['complete_evaluation'],alpha)
            flow['endpoint_ci95']=wilson(flow['endpoint_correct'],flow['complete_evaluation'],a['alpha'])
            for stage in flow['input_output_diagnostics']:
                for row in stage['direct_input_conditionals']:row['ci95']=wilson(row['output_correct'],row['n'],a['alpha'])
            lower=observed_ci[0]-math.prod(v[1] for v in intervals);upper=observed_ci[1]-math.prod(v[0] for v in intervals)
            tol=a['absolute_error_tolerance']
            decision=('equivalent_within_tolerance' if lower>=-tol and upper<=tol else
                      'product_underpredicts' if lower>tol else 'product_overpredicts' if upper<-tol else 'inconclusive')
            flow['statistical_assessment']=dict(prediction=predicted,observed=observed,signed_error=observed-predicted,
                discrepancy_ci=[lower,upper],decision=decision,
                interval_scope='nominal per-flow Wilson/Bonferroni envelope; not family-wide',
                conditional_independence_proven=False)
    return dict(version=VERSION,study_id=p['study_id'],protocol_hash=canonical_hash(p),
        observations_sha256=hashlib.sha256(snapshot).hexdigest(),
        data_origin=p['data_origin'],data_origin_is_self_declared=True,registration_mode=p['registration_mode'],
        data_readiness='blocked' if blockers else 'ready_for_statistical_assessment',
        blockers=blockers,issues=issues,issue_count=issue_count,issues_truncated=issue_count>len(issues),
        counts=dict(counts),flows=diagnostics,domain_validity='pending',
        domain_approval_automatic=False,missing_values_imputed=False)
