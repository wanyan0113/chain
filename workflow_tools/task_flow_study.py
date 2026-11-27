"""Finite service-ticket studies: correctness, template coverage, model risk, cost.

This is an experiment driver, not a new planner or a real-world model validator.
"""
from copy import deepcopy
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

from task_flow.contracts import (ContractError, canonical_sha256, prepare_problem,
                                 read_json, require, validate_shape)
from task_flow.planner import SearchSession
from task_flow.validator import verify_candidate, verify_sequence
from tests.task_flow_oracle import exhaustive_sequences, sequence_key

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/task-flow/phase6/experiment-plan.json'
REFS = ['synthetic-phase6-service-ticket-model']


def condition(value, *, parameter=False):
    return {'test': {'fact': {'predicate': 'progress', 'args': [{'param': 'ticket'}] if parameter else ['t1']},
                     'op': 'eq', 'value': value, 'unit': 'stage'}}


def ticket_model(depth, width, constraints=0, *, max_length=None, mode='first_feasible', max_nodes=20000):
    for value, low, high in [(depth, 1, 8), (width, 1, 4), (constraints, 0, 32)]:
        require(type(value) is int and low <= value <= high, 'model parameter outside study bounds')
    length = depth if max_length is None else max_length
    require(type(length) is int and 0 <= length <= 8, 'invalid study length')
    facts = [{'id': 'progress', 'arguments': ['ticket'], 'kind': 'mutable', 'type': 'integer',
              'minimum': 0, 'maximum': depth, 'unit': 'stage', 'source_refs': REFS}]
    static = []
    forbidden = []
    for index in range(constraints):
        name = f'policy_{index}'
        facts.append({'id': name, 'arguments': [], 'kind': 'static', 'type': 'boolean', 'source_refs': REFS})
        static.append({'fact': {'predicate': name, 'args': []}, 'value': True})
        forbidden.append({'test': {'fact': {'predicate': name, 'args': []}, 'op': 'eq', 'value': False}})
    activities = []
    for stage in range(depth):
        for variant in range(width):
            activities.append({'id': f'stage_{stage}_v{variant}', 'version': '1.0', 'type': 'ticket_processing',
                               'parameters': {'ticket': 'ticket'}, 'applicable_when': {'all': []},
                               'preconditions': condition(stage, parameter=True),
                               'effects': [{'fact': {'predicate': 'progress', 'args': [{'param': 'ticket'}]},
                                            'set': {'value': stage + 1, 'unit': 'stage'}}],
                               'required_capabilities': ['urn:ticket:Process'], 'max_occurrences': 1, 'source_refs': REFS})
    catalog = {'format_version': 'task-flow-1.0', 'catalog_id': 'service-ticket', 'version': '1.0',
               'objects': {'ticket': ['t1']}, 'facts': facts, 'derived_rules': [], 'activities': activities,
               'capabilities': [{'uri': 'urn:ticket:Process', 'mapping_status': 'unmapped', 'source_refs': REFS}],
               'source_refs': REFS}
    request = {'format_version': 'task-flow-1.0', 'request_id': 'ticket-study', 'catalog_id': catalog['catalog_id'],
               'catalog_version': catalog['version'], 'catalog_sha256': canonical_sha256(catalog),
               'static_facts': static, 'initial_state': [{'fact': {'predicate': 'progress', 'args': ['t1']},
                                                        'value': 0, 'unit': 'stage'}],
               'ontology_evidence': [], 'goal': condition(depth), 'required_activities': [],
               'forbidden_conditions': forbidden, 'activity_limits': {},
               'search': {'mode': mode, 'max_length': length, 'max_nodes': max_nodes, 'timeout_ms': 30000},
               'source_refs': REFS}
    return catalog, request


def sequence(catalog, request, names):
    return {'format_version': 'task-flow-1.0', 'catalog_sha256': canonical_sha256(catalog),
            'request_sha256': canonical_sha256(request),
            'steps': [{'activity_id': name, 'activity_version': '1.0', 'parameters': {'ticket': 't1'}} for name in names]}


def correctness_case(config):
    catalog, request = ticket_model(**config, mode='enumerate_bounded')
    g, length = len(catalog['activities']), request['search']['max_length']
    raw_count = sum(g ** k for k in range(length + 1))
    require(raw_count <= 10000, 'reference enumeration exceeds study bound')
    # Independent reference does not call production contracts, grounding, search or state.
    reference = exhaustive_sequences(catalog, request)
    report = SearchSession(prepare_problem(catalog, request)).run()
    actual = {sequence_key(c['sequence']['steps']) for c in report['candidates']}
    verifications = [verify_candidate(catalog, request, c) for c in report['candidates']]
    # A separate mathematical witness for this model family: every full path must
    # use one variant at each stage in order. It is not a general planning proof.
    expected_count = config['width'] ** config['depth'] if length >= config['depth'] else 0
    passed = (report['coverage'] == 'complete' and actual == reference and len(actual) == expected_count
              and all(v['verification_status'] == 'passed' for v in verifications)
              and len(actual) == len(report['candidates']))
    return {'config': config, 'raw_sequence_count': raw_count, 'reference_sequences': sorted(reference),
            'actual_sequences': sorted(actual), 'expected_model_family_count': expected_count,
            'search': report, 'verifications': verifications, 'passed': passed}


def template_cases(plan):
    catalog, _ = ticket_model(4, 2)
    cases = []
    for spec in plan['template_cases']:
        _, request = ticket_model(4, 2)
        request['request_id'] = spec['id']
        request['initial_state'][0]['value'] = spec['initial']
        request['goal'] = condition(spec['goal'])
        request['required_activities'] = spec['required']
        # Exactly the frozen templates: no goal-based trimming or new templates.
        trials = [verify_sequence(catalog, request, sequence(catalog, request, names))
                  for names in plan['fixed_templates']]
        report = SearchSession(prepare_problem(catalog, request)).run()
        checks = [verify_candidate(catalog, request, c) for c in report['candidates']]
        found = bool(report['candidates']) and all(v['verification_status'] == 'passed' for v in checks)
        baseline = any(t['verification_status'] == 'passed' for t in trials)
        cases.append({'spec': spec, 'template_verifications': trials, 'search': report, 'verifications': checks,
                      'actual': {'planner_feasible': found, 'template_feasible': baseline},
                      'passed': found == spec['planner_feasible'] and baseline == spec['template_feasible']
                      and report['search_status'] != 'limit_reached'})
    return cases


def model_risks():
    records = []
    for name in ['conflicting_effects', 'static_write', 'derived_cycle',
                 'contradictory_precondition', 'skipped_stage', 'missing_guard']:
        catalog, request = ticket_model(3, 1)
        if name == 'conflicting_effects':
            catalog['activities'][0]['effects'].append(deepcopy(catalog['activities'][0]['effects'][0]))
        elif name == 'static_write':
            catalog['facts'][0]['kind'] = 'static'
        elif name == 'derived_cycle':
            catalog['facts'].append({'id': 'loop', 'arguments': [], 'kind': 'derived', 'type': 'boolean', 'source_refs': REFS})
            catalog['derived_rules'].append({'id': 'loop_rule', 'parameters': {}, 'head': {'predicate': 'loop', 'args': []},
                                             'when': {'test': {'fact': {'predicate': 'loop', 'args': []}, 'op': 'eq', 'value': True}},
                                             'source_refs': REFS})
        elif name == 'contradictory_precondition':
            catalog['activities'][0]['preconditions'] = {'all': [condition(0, parameter=True), condition(1, parameter=True)]}
        elif name == 'skipped_stage':
            catalog['activities'][0]['effects'][0]['set']['value'] = 3
        else:
            catalog['activities'][2]['preconditions'] = {'all': []}
        request['catalog_sha256'] = canonical_sha256(catalog)
        # All six are schema-valid; semantic rejection is deliberately distinguished.
        validate_shape(catalog, 'catalog')
        validate_shape(request, 'request')
        record = {'name': name, 'catalog': catalog, 'request': request, 'schema_valid': True}
        try:
            problem = prepare_problem(catalog, request)
        except ContractError as exc:
            outcome = 'semantic_contract_rejected'
            record['error'] = {'code': exc.code, 'reason': str(exc)}
        else:
            report = SearchSession(problem).run()
            record['search'] = report
            record['verifications'] = [verify_candidate(catalog, request, c) for c in report['candidates']]
            if not report['candidates']:
                outcome = 'no_flow_without_model_error_diagnosis'
            else:
                outcome = 'formal_pass_domain_error_not_detected'
                # Outside-model domain expectation: progress must increase by exactly
                # one per step. The system has not been given this invariant.
                progress = 0
                witness = []
                for instance in report['candidates'][0]['sequence']['steps']:
                    action = problem.activities[instance['activity_id']]
                    after = action['effects'][0]['set']['value']
                    witness.append({'before': progress, 'after': after, 'increment_is_one': after == progress + 1})
                    progress = after
                record['external_manual_domain_check'] = witness
        expected = ('semantic_contract_rejected' if name in ['conflicting_effects', 'static_write', 'derived_cycle']
                    else 'no_flow_without_model_error_diagnosis' if name == 'contradictory_precondition'
                    else 'formal_pass_domain_error_not_detected')
        record.update(outcome=outcome, expected=expected, passed=outcome == expected)
        if outcome == 'formal_pass_domain_error_not_detected':
            record['passed'] = record['passed'] and all(v['verification_status'] == 'passed' for v in record['verifications']) and any(
                not w['increment_is_one'] for w in record['external_manual_domain_check'])
        records.append(record)
    return records


def efficiency_case(config, repeats):
    # Length-axis runs retain the same six-stage catalog (12 instances); only
    # requested final progress and the search length bound change.
    catalog_depth = 6 if config['axis'] == 'required_length' else config['depth']
    catalog, request = ticket_model(catalog_depth, config['width'], config['constraints'],
                                    max_length=config['depth'], max_nodes=config['max_nodes'])
    request['goal'] = condition(config['depth'])
    runs = []
    for _ in range(repeats):
        start = time.perf_counter()
        problem = prepare_problem(catalog, request)
        session = SearchSession(problem)
        prepared_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        report = session.run()
        run_wall_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        checks = [verify_candidate(catalog, request, c) for c in report['candidates']]
        verification_ms = (time.perf_counter() - start) * 1000
        runs.append({'prepare_wall_ms': prepared_ms, 'search_call_wall_ms': run_wall_ms,
                     'verification_wall_ms': verification_ms, 'search': report, 'verifications': checks})
    expected = 'limit_reached' if config['axis'] == 'budget_control' else 'candidate_found'
    statuses = [r['search']['search_status'] for r in runs]
    nodes = [r['search']['statistics']['nodes'] for r in runs]
    signatures = [(r['search']['search_status'], r['search']['termination_reason'],
                   r['search']['statistics']['nodes'], [c['candidate_id'] for c in r['search']['candidates']]) for r in runs]
    stable = all(s == signatures[0] for s in signatures)
    return {'config': config, 'activity_instances': len(catalog['activities']), 'runs': runs,
            'median_search_call_wall_ms': statistics.median(r['search_call_wall_ms'] for r in runs),
            'min_search_call_wall_ms': min(r['search_call_wall_ms'] for r in runs),
            'max_search_call_wall_ms': max(r['search_call_wall_ms'] for r in runs),
            'nodes': nodes, 'statuses': statuses, 'non_time_results_equal': stable,
            'passed': stable and all(s == expected for s in statuses) and
            all(v['verification_status'] == 'passed' for r in runs for v in r['verifications'])}


def validate_plan(plan):
    require(type(plan) is dict and set(plan) == {'format_version', 'model_family', 'repeats', 'correctness',
            'efficiency', 'fixed_templates', 'template_cases'}, 'invalid study plan fields')
    require(plan['format_version'] == 'task-flow-study-1.0', 'unsupported study plan')
    require(plan['model_family'] == 'synthetic_service_ticket_progress_v1', 'unsupported model family')
    require(type(plan['repeats']) is int and 1 <= plan['repeats'] <= 5, 'repeats must be 1..5 exact integers')
    for key in ['correctness', 'efficiency', 'fixed_templates', 'template_cases']:
        require(type(plan[key]) is list and 1 <= len(plan[key]) <= 32, 'empty or oversized study section: ' + key)
    require(len({c['id'] for c in plan['efficiency']}) == len(plan['efficiency']), 'duplicate efficiency identifiers')
    for config in plan['correctness']:
        require(type(config) is dict and set(config) == {'depth', 'width', 'constraints', 'max_length'}, 'invalid correctness configuration')
        cat, req = ticket_model(**config)
        require(sum(len(cat['activities']) ** k for k in range(req['search']['max_length'] + 1)) <= 10000,
                'correctness reference budget exceeded')
    for config in plan['efficiency']:
        require(type(config) is dict and set(config) == {'id', 'axis', 'depth', 'width', 'constraints', 'max_nodes'}, 'invalid efficiency configuration')
        require(type(config['id']) is str and bool(config['id']), 'invalid efficiency id')
        require(config['axis'] in ['activity_instances', 'required_length', 'invariant_count', 'budget_control'], 'invalid efficiency axis')
        require(type(config['max_nodes']) is int and 1 <= config['max_nodes'] <= 20000, 'invalid node budget')
        ticket_model(config['depth'], config['width'], config['constraints'])
    actions = {a['id'] for a in ticket_model(4, 2)[0]['activities']}
    for names in plan['fixed_templates']:
        require(type(names) is list and len(names) <= 4 and all(n in actions for n in names), 'invalid frozen template')
    for case in plan['template_cases']:
        require(type(case) is dict and set(case) == {'id', 'initial', 'goal', 'required', 'planner_feasible', 'template_feasible'}, 'invalid template case')
        require(type(case['id']) is str and bool(case['id']), 'invalid template case id')
        require(all(type(case[k]) is int and 0 <= case[k] <= 4 for k in ['initial', 'goal']), 'invalid ticket states')
        require(type(case['required']) is list and set(case['required']) <= actions, 'invalid mandatory activities')
        require(all(type(case[k]) is bool for k in ['planner_feasible', 'template_feasible']), 'expected outcomes must be booleans')


def _write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8', newline='\n')


def _sources():
    return sorted([*ROOT.joinpath('task_flow').rglob('*.py'), ROOT/'task_flow/schema.json',
                   Path(__file__).resolve(), PLAN, ROOT/'tests/task_flow_oracle.py', ROOT/'tests/test_task_flow_study.py'])


def run(output, *, plan=None):
    plan = read_json(PLAN) if plan is None else deepcopy(plan)
    validate_plan(plan)
    require(not output.exists(), 'choose a new study directory')
    output.mkdir(parents=True)
    _write(output / 'plan.json', plan)  # Freeze configuration before executing probes.
    correct = [correctness_case(c) for c in plan['correctness']]
    templates = template_cases(plan)
    risks = model_risks()
    efficiency = [efficiency_case(c, plan['repeats']) for c in plan['efficiency']]
    for name, data in [('correctness', correct), ('templates', templates), ('model-risks', risks), ('efficiency', efficiency)]:
        _write(output / (name + '.json'), data)
    passed = all(c['passed'] for group in [correct, templates, risks, efficiency] for c in group)
    summary = {'status': 'passed_with_scope_limits' if passed else 'failed', 'plan_sha256': canonical_sha256(plan),
               'correctness_models': len(correct), 'raw_sequences': sum(c['raw_sequence_count'] for c in correct),
               'accepted_sequences': sum(len(c['actual_sequences']) for c in correct),
               'template_cases': len(templates), 'known_feasible_cases': sum(c['spec']['planner_feasible'] for c in templates),
               'planner_solved': sum(c['actual']['planner_feasible'] for c in templates),
               'template_solved': sum(c['actual']['template_feasible'] for c in templates),
               'model_risk_cases': len(risks), 'efficiency_configurations': len(efficiency),
               'efficiency_runs': sum(len(c['runs']) for c in efficiency),
               'legacy_integration_status': 'not_implemented',
               'interpretation': 'finite_synthetic_model_evidence_not_general_proof_or_algorithm_superiority',
               'environment': {'python': sys.version, 'platform': platform.platform(), 'clock': 'perf_counter',
                               'processor': platform.processor(), 'logical_cpu_count': os.cpu_count(),
                               'jsonschema': version('jsonschema'),
                               'warmup': 'none; repeats run sequentially in one process',
                               'date_policy': 'client study date 2026-11-27; raw timing is host measured'}}
    _write(output / 'summary.json', summary)
    manifest = {'source_sha256_lf_normalized': {p.relative_to(ROOT).as_posix(): hashlib.sha256(
        p.read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in _sources()},
        'artifact_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob('*.json'))}}
    _write(output/'manifest.json', manifest)
    return summary


def verify(output):
    manifest = read_json(output/'manifest.json')
    require(type(manifest) is dict and set(manifest) == {'source_sha256_lf_normalized', 'artifact_sha256'},
            'invalid manifest fields')
    require(set(manifest['source_sha256_lf_normalized']) == {p.relative_to(ROOT).as_posix() for p in _sources()},
            'incomplete source inventory')
    artifacts = {'plan.json', 'correctness.json', 'templates.json', 'model-risks.json', 'efficiency.json', 'summary.json'}
    require(set(manifest['artifact_sha256']) == artifacts, 'incomplete artifact inventory')
    for name, expected in manifest['source_sha256_lf_normalized'].items():
        require(hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == expected,
                'source changed: ' + name)
    for name, expected in manifest['artifact_sha256'].items():
        require(hashlib.sha256((output/name).read_bytes()).hexdigest() == expected, 'artifact changed: ' + name)
    summary = read_json(output/'summary.json')
    require(canonical_sha256(read_json(output/'plan.json')) == summary['plan_sha256'], 'study plan binding mismatch')
    require(summary['status'] == 'passed_with_scope_limits', 'study contains failed checks')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'verify'])
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = run(args.output_dir) if args.command == 'run' else verify(args.output_dir)
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary['status'] == 'passed_with_scope_limits' else 1
    except (ContractError, OSError) as exc:
        print(json.dumps({'status': 'invalid_study', 'reason': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
