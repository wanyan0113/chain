"""Probe current v1 statistical semantics against an independent raw-count oracle."""
import argparse
from copy import deepcopy
import csv
import json
from pathlib import Path

from workflow_tools.accuracy_observations import FIELDS,analyze,freeze_protocol
from workflow_tools.benchmark_joint import save
from workflow_tools.observation_semantics_reference import grade


def protocol():
    return dict(version='tfdo-observation-intake-1.0',study_id='semantic-audit-fixture',data_origin='synthetic_fixture',
        registration_mode='retrospective_analysis',target_definition='Common binary category; audit fixture only',
        label_space=['0','1'],ground_truth_method_review='verified',sampling_design='iid_subject',
        evaluation_population='audit-population',label_sources={'audit':'Explicit synthetic reference labels'},strata=['all'],
        flows=[dict(flow_id='F4',calibration_population='audit-population',input_from_previous_output=True,
            stages=[dict(stage_instance_id=f's{i+1}',activity_id=activity,activity_version='1.0',artifact_id=f'fixture-{i+1}',
                artifact_version='fixture-v1',calibration_input_condition='correct_reference_label')
                for i,activity in enumerate(['observe','decide','decide','act'])])],
        acceptance=dict(alpha=.05,absolute_error_tolerance=.05,minimum_stage_calibration=2,
            minimum_complete_evaluation=2,minimum_stratum_class=1))


def fixture(p):
    rows=[];registry=[]
    for flow in p['flows']:
        for split in ('calibration','evaluation'):
            for i in range(4):
                sample=f"{flow['flow_id']}:{split}:{i}";gold=str(i%2);previous=gold
                registry.append(dict(flow_id=flow['flow_id'],split=split,sample_id=sample,endpoint_eligible=True,
                                     expected_stages=[s['stage_instance_id'] for s in flow['stages']]))
                for index,stage in enumerate(flow['stages']):
                    success=i<([3,1,3,3][index] if len(flow['stages'])==4 else [3,2,3][index])
                    output=gold if success else str(1-int(gold))
                    row={k:stage[k] for k in ('stage_instance_id','activity_id','activity_version','artifact_id','artifact_version')}
                    row.update(observation_id=f'{sample}:{index}',sample_id=sample,cluster_id=sample,split=split,flow_id=flow['flow_id'],
                        stratum='all',gold_label=gold,input_label=gold if split=='calibration' else previous,output_label=output,
                        status='observed',label_source_id='audit',recorded_at='2025-01-01T00:00:00Z')
                    rows.append(row);previous=output
    return rows,registry


def run_probe(output,name,p,rows,registry,*,input_sources=None,final_outcomes=None):
    directory=output/name;directory.mkdir()
    save(directory/'protocol.json',p);lock=freeze_protocol(p,directory/'protocol-lock.json')
    path=directory/'observations.csv'
    with path.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=FIELDS,lineterminator='\n');writer.writeheader();writer.writerows(rows)
    save(directory/'enrollment-reference.json',registry)
    save(directory/'reference-inputs.json',dict(input_sources=input_sources or {},
        final_outcomes=[dict(flow_id=k[0],sample_id=k[1],**v) for k,v in (final_outcomes or {}).items()]))
    actual=analyze(p,path,lock);reference=grade(p,rows,registry,input_sources=input_sources,final_outcomes=final_outcomes)
    save(directory/'production-v1-report.json',actual);save(directory/'independent-reference.json',reference)
    return actual,reference


def execute(output):
    if output.exists():raise ValueError('choose a new audit directory')
    output.mkdir(parents=True);p=protocol();rows,registry=fixture(p);cases=[]
    actual,ref=run_probe(output,'repeated_decide',p,rows,registry)
    got=actual['flows'][0]['calibration'];expected=[r for r in ref['flows'][0]['stage_statistics'] if r['split']=='calibration']
    assert [(r['stage_instance_id'],r['n'],r['correct']) for r in got]==[(r['stage_instance_id'],r['counts']['evaluable'],r['correct']) for r in expected]
    assert got[1]['rate']==.25 and got[2]['rate']==.75
    cases.append(dict(case='repeated_decide_is_instance_specific',passed=True,scope='calibration counters agree'))
    unequal=[r for r in rows if not (r['split']=='calibration' and r['stage_instance_id']=='s2' and r['sample_id'].endswith(':3'))]
    actual,ref=run_probe(output,'unequal_calibration_denominators',p,unequal,registry)
    assert [r['n'] for r in actual['flows'][0]['calibration']]==[4,3,4,4]
    cases.append(dict(case='unequal_calibration_denominators_preserved',passed=True))
    for status in ('missing','not_executed'):
        changed=deepcopy(rows)
        target=next(r for r in changed if r['split']=='evaluation' and r['sample_id'].endswith(':1') and r['stage_instance_id']=='s2')
        target.update(status=status,output_label='')
        actual,ref=run_probe(output,status,p,changed,registry)
        assert actual['blockers'] and actual['flows'][0]['statistical_assessment'] is None
        counts=[r['counts'].get('evaluable',0) for r in ref['flows'][0]['stage_statistics'] if r['split']=='evaluation']
        assert counts==[4,3,4,4] and actual['flows'][0]['complete_evaluation']==3
        cases.append(dict(case=status+'_blocks_inference',passed=True,reference_stage_denominators=counts,
                          v1_stage_evaluation_rates_available=False))
    absent=[r for r in rows if r['sample_id']!='F4:evaluation:3']
    actual,ref=run_probe(output,'wholly_absent_subject',p,absent,registry)
    assert not actual['blockers'] and actual['flows'][0]['evaluation_subjects']==3
    assert ref['flows'][0]['wholly_unrecorded_subjects']==['F4:evaluation:3']
    cases.append(dict(case='enrollment_gap_reproduced',passed=True,finding='OBS-SEM-001',
                      v1_observed_subjects=3,reference_registered_subjects=4))
    target=next(r for r in rows if r['sample_id']=='F4:evaluation:2' and r['stage_instance_id']=='s2')
    wrong_source=next(r for r in rows if r['sample_id']=='F4:evaluation:0' and r['stage_instance_id']=='s1')
    assert target['input_label']==wrong_source['output_label']
    actual,ref=run_probe(output,'same_label_wrong_input_origin',p,rows,registry,
        input_sources={target['observation_id']:wrong_source['observation_id']})
    assert not actual['blockers'] and not ref['lineage_checks'][0]['valid']
    cases.append(dict(case='label_continuity_is_not_provenance',passed=True,finding='OBS-SEM-003'))
    finals={('F4',f'F4:evaluation:{i}'):dict(success=i==0,evaluation_method='separate-synthetic-mission-criterion') for i in range(4)}
    actual,ref=run_probe(output,'independent_final_event',p,rows,registry,final_outcomes=finals)
    assert actual['flows'][0]['endpoint_rate']==.75 and ref['flows'][0]['independent_final_rate']==.25
    cases.append(dict(case='different_final_event_requires_new_contract',passed=True,finding='OBS-SEM-004',
                      v1_last_label_accuracy=.75,separate_final_event_rate=.25))
    no_link=deepcopy(p);no_link['flows'][0]['input_from_previous_output']=False
    actual,ref=run_probe(output,'no_declared_direct_label_dependency',no_link,rows,registry)
    assert all(not d['causal_correction_claim'] for d in actual['flows'][0]['input_output_diagnostics'])
    cases.append(dict(case='output_recovery_not_a_causal_claim',passed=True))
    mixed=deepcopy(p);other=deepcopy(mixed['flows'][0]);other['flow_id']='F3';other['stages']=[other['stages'][i] for i in (0,1,3)]
    mixed['flows'].append(other);mixed_rows,mixed_registry=fixture(mixed)
    actual,ref=run_probe(output,'three_and_four_stages_kept_separate',mixed,mixed_rows,mixed_registry)
    assert len(actual['flows'])==2 and [len(f['calibration']) for f in actual['flows']]==[4,3]
    cases.append(dict(case='three_four_stage_flows_not_pooled',passed=True))
    directory=output/'unversioned_missing_reason_column';directory.mkdir()
    save(directory/'protocol.json',p);lock=freeze_protocol(p,directory/'protocol-lock.json')
    path=directory/'observations.csv'
    with path.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=FIELDS+['missing_reason'],lineterminator='\n')
        writer.writeheader();writer.writerows(dict(r,missing_reason='') for r in rows)
    try:analyze(p,path,lock)
    except ValueError as exc:save(directory/'expected-rejection.json',dict(reason=str(exc)))
    else:raise AssertionError('unversioned added column silently accepted')
    cases.append(dict(case='new_fields_need_versioned_contract',passed=True,finding='OBS-SEM-005'))
    duplicate=deepcopy(rows);attempt=dict(duplicate[-1],observation_id='second-attempt-same-stage');duplicate.append(attempt)
    directory=output/'repeated_attempt_same_instance';directory.mkdir();save(directory/'protocol.json',p)
    lock=freeze_protocol(p,directory/'protocol-lock.json');path=directory/'observations.csv'
    with path.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=FIELDS,lineterminator='\n');writer.writeheader();writer.writerows(duplicate)
    actual=analyze(p,path,lock);save(directory/'production-v1-report.json',actual)
    assert any(i['code']=='duplicate_stage_observation' for i in actual['issues']) and actual['flows'][0]['statistical_assessment'] is None
    cases.append(dict(case='repeat_execution_attempt_not_silently_counted',passed=True))
    summary=dict(version='tfdo-observation-semantics-audit-1.0',status='conditional_pass_with_open_findings',
        real_domain_data_available=False,domain_validity='pending',human_independent_domain_review=False,
        reference_independent_of_production_intake_and_estimators=True,cases=cases,
        open_findings=['OBS-SEM-001','OBS-SEM-002','OBS-SEM-003','OBS-SEM-004','OBS-SEM-005'],
        scoring_formula_changed=False,historical_contract_changed=False,
        group_catalog=[dict(flow_id=f['flow_id'],stage_count=len(f['stages']),
            activity_sequence=[s['activity_id'] for s in f['stages']],stage_instances=f['stages']) for f in mixed['flows']],
        correction_grouping_rule='predeclare correction mechanism; observed recovery is descriptive, not a causal allocation rule')
    save(output/'audit.json',summary)
    print(json.dumps(dict(status=summary['status'],cases=len(cases),open_findings=5,domain_validity='pending'),ensure_ascii=False))
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,required=True)
    execute(parser.parse_args(argv).output_dir.resolve())


if __name__=='__main__':main()
