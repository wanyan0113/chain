"""Read-only finite model-quality probes for the synthetic expense policy.

Counterexamples use an independent declared domain policy. No generic assertion
that arbitrary business requirements are complete or correctly modeled is made.
"""
from copy import deepcopy
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

from task_flow.contracts import ContractError, canonical_sha256, prepare_problem, read_json, require
from task_flow.external import analyze_candidate
from task_flow.planner import SearchSession
from task_flow.validator import verify_candidate
from tests.expense_policy_reference import check_expense_policy
from .expense_workflow import adapt, validate_input

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT/'docs/task-flow/model-quality/baseline.json'
EFFICIENCY = ROOT/'docs/task-flow/phase6/evidence/efficiency.json'
EXPENSE = ROOT/'task_flow/examples/expense_workflow'


def audit_expense_model(raw, catalog, request, *, max_candidates=100):
    validate_input(raw)
    require(type(max_candidates) is int and 1 <= max_candidates <= 100, 'invalid audit candidate budget')
    declared = deepcopy(request)
    effective = deepcopy(request)
    report = {'quality_status': 'inconclusive', 'raw': deepcopy(raw), 'catalog': deepcopy(catalog),
              'declared_request': declared, 'effective_request': effective,
              'raw_sha256': canonical_sha256(raw), 'scope': 'declared_synthetic_expense_policy_within_bounds',
              'counterexamples': [], 'candidate_checks': [], 'reason': None,
              'business_requirements_complete': 'not_established'}
    try:
        original = prepare_problem(catalog, declared)
        effective = deepcopy(original.request)
        effective['search']['mode'] = 'enumerate_bounded'
        report['effective_request'] = effective
        problem = prepare_problem(original.catalog, effective)
    except ContractError as exc:
        report.update(quality_status='invalid_model_contract', reason=str(exc), error_code=exc.code)
        return report
    search = SearchSession(problem).run(max_candidates=max_candidates)
    report['search'] = search
    uncertified = False
    for candidate in search['candidates']:
        formal = verify_candidate(catalog, effective, candidate)
        domain = None
        if formal['verification_status'] == 'passed':
            domain = check_expense_policy(raw, candidate['sequence']['steps'])
            if domain['passed'] is False:
                report['counterexamples'].append({'candidate_id': candidate['candidate_id'],
                                                  'sequence': candidate['sequence'], 'domain_failure': domain})
        else:
            uncertified = True
        report['candidate_checks'].append({'candidate_id': candidate['candidate_id'],
                                            'formal': formal, 'domain': domain})
    if report['counterexamples']:
        report.update(quality_status='counterexample_found', reason='formally_valid_sequence_violates_independent_domain_policy')
    elif uncertified:
        report['reason'] = 'candidate_not_independently_certified'
    elif search['coverage'] != 'complete':
        report['reason'] = 'search_not_complete'
    elif search['unknown_conditions_seen']:
        report['reason'] = 'unknown_conditions_seen'
    elif not report['candidate_checks']:
        report['reason'] = 'no_certified_workflow_is_not_model_quality_proof'
    else:
        report.update(quality_status='no_counterexample_within_bounds',
                      reason='all_enumerated_certified_sequences_satisfy_the_given_policy_only')
    return report


def quality_probes():
    high = read_json(EXPENSE/'sample.json')
    low = next(c['input'] for c in read_json(EXPENSE/'cases.json')['cases'] if c['id'] == 'low_archived')
    names = ['unmodified_high', 'unmodified_low', 'omit_manager_rule', 'wrong_threshold',
             'omit_receipt_condition', 'omit_business_goal', 'altered_initial_state',
             'duplicate_effect', 'derived_cycle', 'static_write', 'contradictory_precondition',
             'unknown_receipt', 'limited_search']
    records = []
    for name in names:
        raw = deepcopy(low if name in ['unmodified_low', 'omit_receipt_condition'] else high)
        if name == 'omit_receipt_condition':
            raw['claims'][0]['receipt_valid'] = False
        if name == 'unknown_receipt':
            raw['claims'][0]['receipt_valid'] = None
        prepared = adapt(raw)
        catalog, request = prepared['catalog'], prepared['request']
        if name == 'omit_manager_rule':
            catalog['derived_rules'][0]['when'] = {'test': {'fact': {'predicate': 'screened', 'args': [{'param': 'claim'}]}, 'op': 'eq', 'value': True}}
        elif name == 'wrong_threshold':
            catalog['derived_rules'][0]['when']['all'][1]['any'][0]['test']['value'] = 10000000
        elif name == 'omit_receipt_condition':
            catalog['activities'][0]['preconditions']['all'].pop(0)
        elif name == 'omit_business_goal':
            request['goal'] = {'all': []}
        elif name == 'altered_initial_state':
            for item in request['initial_state']:
                item['value'] = True
        elif name == 'duplicate_effect':
            catalog['activities'][0]['effects'].append(deepcopy(catalog['activities'][0]['effects'][0]))
        elif name == 'derived_cycle':
            catalog['derived_rules'][0]['when'] = {'test': {'fact': {'predicate': 'eligible', 'args': [{'param': 'claim'}]}, 'op': 'eq', 'value': True}}
        elif name == 'static_write':
            catalog['activities'][0]['effects'][0]['fact']['predicate'] = 'receipt_valid'
        elif name == 'contradictory_precondition':
            catalog['activities'][0]['preconditions']['all'].append({'test': {
                'fact': {'predicate': 'submitted', 'args': [{'param': 'claim'}]}, 'op': 'eq', 'value': True}})
        elif name == 'limited_search':
            request['search']['max_nodes'] = 1
        request['catalog_sha256'] = canonical_sha256(catalog)
        expected = ('no_counterexample_within_bounds' if name.startswith('unmodified') else
                    'invalid_model_contract' if name in ['duplicate_effect', 'derived_cycle', 'static_write'] else
                    'inconclusive' if name in ['contradictory_precondition', 'unknown_receipt', 'limited_search'] else
                    'counterexample_found')
        report = audit_expense_model(raw, catalog, request)
        records.append({'id': name, 'expected_quality_status': expected,
                        'actual_quality_status': report['quality_status'],
                        'expectation_met': report['quality_status'] == expected, 'report': report})
    return records


def capability_semantics_probe():
    raw = read_json(EXPENSE/'sample.json')
    prepared = adapt(raw)
    catalog, request = prepared['catalog'], prepared['request']
    # Conjunction of two declared capabilities for submit; Archive is also required
    # by archive, providing an explicit many-to-many positive control.
    catalog['activities'][0]['required_capabilities'].append('urn:expense:Archive')
    request['catalog_sha256'] = canonical_sha256(catalog)
    candidate = SearchSession(prepare_problem(catalog, request)).run()['candidates'][0]
    problem = prepare_problem(catalog, request)
    positive = analyze_candidate(problem, candidate, prepared['mapping'], prepared['context'])
    missing = deepcopy(prepared['mapping'])
    missing['entries'] = [e for e in missing['entries'] if e['capability_uri'] != 'urn:expense:Archive']
    unsupported = analyze_candidate(problem, candidate, missing, prepared['context'])
    unavailable = deepcopy(prepared['context'])
    next(s for s in unavailable['services'] if s['service_id'] == 'Archive')['available'] = False
    rejected = analyze_candidate(problem, candidate, prepared['mapping'], unavailable)
    return {'catalog': catalog, 'request': request, 'candidate': candidate,
            'positive': positive, 'missing_mapping': unsupported, 'one_capability_unavailable': rejected,
            'expectation_met': positive['external_status'] == 'passed' and
            len(positive['steps'][0]['demands']) == 2 and unsupported['external_status'] == 'unsupported_mapping' and
            rejected['external_status'] == 'rejected',
            'scope': 'conjunctive_service_availability_not_joint_resource_assignment'}


def efficiency_rows(data):
    require(type(data) is list and bool(data), 'empty efficiency evidence')
    rows = []
    for case in data:
        runs = case['runs']
        require(type(runs) is list and bool(runs), 'empty efficiency repeats')
        require(all(r['search']['input_snapshot']['request']['search']['mode'] == 'first_feasible' for r in runs),
                'summary expects first-feasible experiments only')
        times = [r['search_call_wall_ms'] for r in runs]
        nodes = [r['search']['statistics']['nodes'] for r in runs]
        require(all(type(t) in [int, float] and math.isfinite(t) and t >= 0 for t in times), 'invalid recorded runtime')
        rows.append({'id': case['config']['id'], 'repeats': len(runs), 'search_nodes': nodes,
                     'mean_search_call_wall_ms': statistics.mean(times), 'median_search_call_wall_ms': statistics.median(times),
                     'candidate_counts': [len(r['search']['candidates']) for r in runs],
                     'first_solution_depths': [len(r['search']['candidates'][0]['sequence']['steps']) if r['search']['candidates'] else None for r in runs],
                     'search_statuses': [r['search']['search_status'] for r in runs],
                     'enumeration_completion_rate': None,
                     'enumeration_reason': 'not_applicable_to_first_feasible_mode'})
    return rows


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8', newline='\n')


def _sources():
    return sorted([*ROOT.joinpath('task_flow').rglob('*.py'), ROOT/'task_flow/schema.json',
                   Path(__file__).resolve(), ROOT/'tests/test_model_quality_audit.py',
                   ROOT/'tests/expense_policy_reference.py', ROOT/'workflow_tools/expense_workflow.py',
                   EXPENSE/'sample.json', EXPENSE/'cases.json', EFFICIENCY, BASELINE,
                   BASELINE.with_name('environment-requirements.txt')])


def run(output):
    require(not output.exists(), 'choose a new quality-audit directory')
    output.mkdir(parents=True)
    probes = quality_probes()
    capability = capability_semantics_probe()
    rows = efficiency_rows(read_json(EFFICIENCY, allow_finite_floats=True))
    _write(output/'quality-probes.json', probes)
    _write(output/'capability-semantics.json', capability)
    _write(output/'efficiency-metrics.json', rows)
    summary = {'status': 'expectations_met_with_scope_limits' if all(p['expectation_met'] for p in probes) and capability['expectation_met'] else 'failed',
               'baseline_commit': read_json(BASELINE)['baseline_commit'], 'probe_count': len(probes),
               'status_counts': {s: sum(p['actual_quality_status'] == s for p in probes) for s in
                                 ['no_counterexample_within_bounds', 'counterexample_found', 'invalid_model_contract', 'inconclusive']},
               'capability_probe_expectation_met': capability['expectation_met'],
               'efficiency_configurations_summarized': len(rows),
               'model_quality_proven': False, 'legacy_integration_status': 'not_implemented'}
    _write(output/'summary.json', summary)
    manifest = {'source_sha256_lf_normalized': {p.relative_to(ROOT).as_posix(): hashlib.sha256(
        p.read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in _sources()},
        'artifact_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob('*.json'))}}
    _write(output/'manifest.json', manifest)
    return summary


def verify(output):
    manifest = read_json(output/'manifest.json')
    require(set(manifest) == {'source_sha256_lf_normalized', 'artifact_sha256'}, 'invalid manifest')
    require(set(manifest['source_sha256_lf_normalized']) == {p.relative_to(ROOT).as_posix() for p in _sources()}, 'incomplete source inventory')
    require(set(manifest['artifact_sha256']) == {'summary.json', 'quality-probes.json', 'capability-semantics.json', 'efficiency-metrics.json'}, 'incomplete artifact inventory')
    for name, digest in manifest['source_sha256_lf_normalized'].items():
        require(hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == digest, 'source changed: '+name)
    for name, digest in manifest['artifact_sha256'].items():
        require(hashlib.sha256((output/name).read_bytes()).hexdigest() == digest, 'artifact changed: '+name)
    summary = read_json(output/'summary.json')
    require(summary['status'] == 'expectations_met_with_scope_limits', 'quality probes did not match expectations')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'verify'])
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = run(args.output_dir) if args.command == 'run' else verify(args.output_dir)
        print(json.dumps(summary, ensure_ascii=False))
        return 1 if summary['status'] == 'failed' else 0
    except (ContractError, OSError) as exc:
        print(json.dumps({'status': 'invalid_quality_audit', 'reason': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
