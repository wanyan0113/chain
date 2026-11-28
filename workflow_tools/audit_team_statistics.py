"""Review team measurement semantics; outputs a draft, not a new intake API."""
import argparse
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import zipfile

from task_flow.joint_identity import sha
from task_flow.team_evaluation import plan_key, validate_registry
from task_flow.team_quality_semantics import validate_quality_context
from task_flow.team_optimization_verify import verify_team_optimization
from .team_statistics_reference import group_levels, serial_events, paired_endpoints

ROOT = Path(__file__).resolve().parents[1]


def probes(report):
    """Fixtures explicitly supply independent group and endpoint labels."""
    rows = [dict(sample_id=f'g-{i}',
                 member_outputs={'A': dict(actual='spectrum-ok' if i < 8 else 'spectrum-wrong', reference='spectrum-ok'),
                                 'B': dict(actual='position-ok' if i < 8 else 'position-wrong', reference='position-ok')},
                 group_output=dict(actual='decision-ok' if i < 9 else 'decision-wrong', reference='decision-ok'),
                 task_endpoint=dict(actual='stable' if i < 7 else 'unstable', reference='stable')) for i in range(10)]
    levels = group_levels(rows, ['A', 'B'])
    assert levels['group_rate'] == .9 and levels['endpoint_rate'] == .7 and levels['member_rate_product'] != levels['group_rate']
    correlated = [dict(sample_id=f'c-{i}', events=dict(s1=i < 8, s2=i < 8, s3=i < 8), endpoint=i < 8) for i in range(10)]
    correlated_result = serial_events(correlated, ['s1', 's2', 's3'])
    assert correlated_result['conditional_product'] == correlated_result['joint_event_rate'] == .8
    assert correlated_result['marginal_product'] != .8
    assert correlated_result['conditionals'][2]['conditioned_on'] == ['s1', 's2']
    repaired = deepcopy(correlated)
    repaired[8]['endpoint'] = True
    repair_result = serial_events(repaired, ['s1', 's2', 's3'])
    assert repair_result['joint_event_rate'] == .8 and repair_result['endpoint_rate'] == .9
    zero = serial_events([dict(sample_id='zero', events=dict(s1=False, s2=True), endpoint=False)], ['s1', 's2'])
    assert zero['conditionals'][1]['value'] is None and zero['conditional_product'] is None
    enrollment = [dict(pair_id=f'p-{i}', sample_id=f's-{i}', population='same-enrolled-population') for i in range(10)]
    short = [dict(**e, success=i < 8) for i, e in enumerate(enrollment)]
    long = [dict(**e, success=i < 9) for i, e in enumerate(enrollment)]
    pairs = paired_endpoints(enrollment, short, long)
    assert pairs['n_complete_pairs'] == 10 and pairs['complete_pair_rate_difference'] == .1
    missing_pairs = paired_endpoints(enrollment, short, long[:-1])
    assert missing_pairs['n_enrolled'] == 10 and missing_pairs['n_complete_pairs'] == 9 and not missing_pairs['coverage_complete']
    mismatch = deepcopy(long)
    mismatch[0]['population'] = 'harder-tasks'
    try:
        paired_endpoints(enrollment, short, mismatch)
    except ValueError as exc:
        mismatch_result = dict(rejected=True, reason=str(exc))
    else:
        raise AssertionError('changed paired population silently accepted')
    qrecord = deepcopy(report['registry']['records'][0])
    q = qrecord['quality']
    q['semantics'] = 'conditional_success_probability'
    q['probability_basis'] = dict(success_event='declared group success', conditioning_event='full preceding joint-success event',
        dependence_assumptions='Probability chain rule; independence is not required for full-prefix conditional factors.',
        evidence_refs=['synthetic-event-definition-only'])
    assert not validate_quality_context(report['source_report'], qrecord)
    # Identical scale strings do not establish construct validity. This is an
    # explicit residual domain-review finding, not a new software rejection.
    construct = deepcopy(report['registry']['records'][0])
    construct['quality']['definition'] = 'A different measured construct despite the same scale identifier'
    assert not validate_quality_context(report['source_report'], construct)
    changed_registry = deepcopy(report['registry'])
    changed_registry['records'][0] = construct
    _, missing = validate_registry(report['source_report'], changed_registry)
    assert not missing
    results = [dict(case='member_group_endpoint_separation', result=levels),
               dict(case='correlated_marginals_vs_full_prefix_conditionals', result=correlated_result),
               dict(case='correction_breaks_endpoint_conjunction_equivalence', result=repair_result),
               dict(case='zero_support_conditional_not_filled', result=zero),
               dict(case='paired_same_sample_population', result=pairs),
               dict(case='missing_pair_retains_enrolled_denominator', result=missing_pairs),
               dict(case='changed_population_rejected', result=mismatch_result),
               dict(case='conditional_basis_does_not_require_independence', result=dict(accepted_as_declaration=True, probability_validated=False)),
               dict(case='same_scale_different_construct', result=dict(declaration_accepted=True, measurement_validity='pending', domain_review_required=True))]
    fixtures = dict(data_origin='synthetic_simulation', member_group_endpoint=rows, correlated_serial=correlated,
                    repaired_endpoint=repaired, enrollments=enrollment, short_arm=short, long_arm=long,
                    missing_long_arm=long[:-1], mismatched_population_arm=mismatch)
    return results, fixtures


def protocol_draft(report):
    source = report['source_report']
    analyses = {(a['task_id'], a['flow_id']): a for a in source['analyses']}
    flows = []
    for cid in report['selected_candidate_ids']:
        chain = source['candidates'][cid]
        activity_instances = analyses[chain['task'], chain['flow_id']]['flow_candidate']['sequence']['steps']
        stages = []
        for i, (stage, instance) in enumerate(zip(chain['plan']['steps'], activity_instances)):
            stages.append(dict(stage_instance_id=stage['step_instance_id'], activity_id=instance['activity_id'],
                activity_version=instance['activity_version'], activity_type=stage['activity'],
                evaluation_plan_key=plan_key(chain, stage), hub=stage['hub'],
                capability_assignments=deepcopy(stage['capability_assignments']),
                members=[dict(artifact_id=m, artifact_version='UNASSIGNED',
                              capabilities=sorted(k for k, provider in stage['capability_assignments'].items() if provider == m)) for m in stage['members']],
                measurement_object='', direction='UNASSIGNED', scale_meaning='', evaluation_method='', reference_method='',
                population='', group_success_event='',
                member_to_group_mapping=dict(status='not_assumed', policy=None),
                full_prefix_stage_instance_ids=[s['step_instance_id'] for s in chain['plan']['steps'][:i]]))
        flows.append(dict(task_id=chain['task'], candidate_id=cid, flow_id=chain['flow_id'], stages=stages,
                          endpoint_definition='', endpoint_method='', endpoint_reference_method='',
                          endpoint_equals_all_stage_success='UNASSIGNED'))
    binding = dict(source_input_hash=source['input_hash'], execution_context_hash=sha(source['context']),
                   selected_candidate_ids=report['selected_candidate_ids'], evaluation_context_hash=report['evaluation']['evaluation_context_hash'])
    draft = dict(version='team-statistical-protocol-draft-0.1', implementation_status='design_draft_not_collection_interface',
                 source_binding=binding, flows=flows,
                 paired_design=dict(enrollment_required=True, baseline_sample_link_required=True,
                    assignment_method='UNASSIGNED', execution_order_policy='UNASSIGNED',
                    carryover_control='UNASSIGNED', population_comparability='pending',
                    calibration_evaluation_split='UNASSIGNED', minimum_sample_coverage=None,
                    acceptable_prediction_error=None),
                 probability_review=dict(marginal_basis='Joint-product interpretation requires appropriate independence evidence.',
                    conditional_basis='Full-prefix chain rule; no independence requirement. First factor is unconditional.',
                    zero_support='Keep undefined conditional values null; report their denominators.',
                    endpoint_equivalence='Review separately, especially with correction; never infer from flow order.'),
                 data_origin='uncollected', domain_validity='pending', formula_changed=False)
    draft['draft_design_id'] = 'team-study-draft-' + sha(draft)
    return draft


HEADERS = {
    'enrollments': ['pair_id','sample_id','population','cluster_id','split','stratum','arm_id','candidate_id','execution_id','eligible','exclusion_reason','registered_at','baseline_input_reference','execution_order','environment_reference'],
    'member_observations': ['observation_id','sample_id','execution_id','candidate_id','stage_instance_id','attempt_id','artifact_id','artifact_version','capability_id','input_reference','output_reference','reference_label','actual_label','evaluation_method','missing_reason'],
    'group_outputs': ['group_observation_id','sample_id','execution_id','candidate_id','stage_instance_id','attempt_id','member_observation_ids','hub','group_output_reference','independent_reference','reference_label','actual_label','evaluation_method','missing_reason'],
    'task_endpoints': ['sample_id','execution_id','candidate_id','endpoint_event_id','result_reference','independent_reference','reference_label','actual_label','evaluation_method','missing_reason']}


def run(output, source_report=None):
    if output.exists():
        raise ValueError('choose a new output directory')
    if source_report:
        from task_flow.contracts import read_json
        report = read_json(source_report, allow_finite_floats=True)
    else:
        with zipfile.ZipFile(ROOT/'docs/task-flow/team-quality-semantics-evidence/full-run.zip') as archive:
            report = json.loads(archive.read('delivery/team-optimization.json').decode('utf-8'))
    if report.get('format_version') != 'task-flow-team-optimization-1.1' or report.get('registry', {}).get('format_version') != 'task-flow-team-evaluation-1.1':
        raise ValueError('statistical review requires the explicit team quality 1.1 contract')
    check = verify_team_optimization(report)
    if check['status'] != 'passed' or not report['selected_candidate_ids']:
        raise ValueError('verified selected team source required: ' + str(check))
    cases, fixtures = probes(report)
    draft = protocol_draft(report)
    findings = [dict(id='TEAM-STAT-001', topic='Measurement constructs, directions and scales require domain comparability review', status='pending'),
                dict(id='TEAM-STAT-002', topic='Member contributions, independent group output and endpoint require separate observations', status='pending'),
                dict(id='TEAM-STAT-003', topic='Conditional/marginal denominators and endpoint/conjunction equivalence need evidence', status='pending'),
                dict(id='TEAM-STAT-004', topic='Paired enrollment, baseline difficulty, order and carryover controls need prospective design', status='pending'),
                dict(id='TEAM-STAT-005', topic='Current single-equipment observation intake does not support teams', status='not_implemented')]
    result = dict(version='team-statistical-semantics-audit-1.0', status='software_review_passed_with_open_domain_findings',
                  source_report_hash=sha(report), source_verification=check, cases=cases, findings=findings,
                  draft_design_id=draft['draft_design_id'], collection_interface_implemented=False,
                  domain_validity='pending', probability_calibrated=False, formula_changed=False)
    output.mkdir(parents=True)
    for name, value in [('audit', result), ('protocol-draft', draft), ('synthetic-fixtures', fixtures)]:
        (output/(name+'.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    for name, fields in HEADERS.items():
        with (output/(name+'.draft.csv')).open('w', encoding='utf-8', newline='') as f:
            csv.writer(f).writerow(fields)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
    (output/'manifest.json').write_text(json.dumps(hashes, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(status=result['status'], probes=len(cases), open_findings=len(findings),
                         domain_validity='pending', draft_design_id=draft['draft_design_id']), ensure_ascii=False))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-report', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    run(args.output_dir, args.source_report)


if __name__ == '__main__':
    main()
