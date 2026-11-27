"""Reproduce the bounded, synthetic expense-approval application study."""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path

from task_flow.contracts import ContractError, canonical_sha256, prepare_problem, read_json, require
from task_flow.external import IntegrationSession
from task_flow.planner import SearchSession
from task_flow.validator import verify_candidate
from tests.expense_policy_reference import check_expense_policy
from .expense_workflow import adapt, validate_input

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT/'task_flow/examples/expense_workflow'


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8', newline='\n')


def prepare(input_path, output):
    require(not output.exists(), 'choose a new prepared-input directory')
    raw = read_json(input_path)
    prepared = adapt(raw)
    # Validate the generated planning contracts before publishing the files.
    prepare_problem(prepared['catalog'], prepared['request'])
    output.mkdir(parents=True)
    for name in ['catalog', 'request', 'mapping', 'context']:
        _write(output/(name+'.json'), prepared[name])
    _write(output/'input-trace.json', {'raw': raw, 'raw_sha256': prepared['raw_sha256'],
                                      'field_mapping': prepared['field_mapping'], 'real_payment_executed': False})
    return {'status': 'prepared', 'output': str(output), 'real_payment_executed': False}


def evaluate_case(spec):
    prepared = adapt(spec['input'])
    context = deepcopy(prepared['context'])
    services = {s['service_id']: s for s in context['services']}
    require(type(spec['service_overrides']) is dict and set(spec['service_overrides']) <= services.keys(), 'unknown service override')
    for name, value in spec['service_overrides'].items():
        require(type(value) is bool or type(value) is str and value == 'unknown', 'invalid service override')
        services[name]['available'] = value
    integration = IntegrationSession(prepare_problem(prepared['catalog'], prepared['request']),
                                     prepared['mapping'], context).run(max_checks=100)
    domain_checks = [{'candidate_id': a['candidate']['candidate_id'],
                      'report': check_expense_policy(spec['input'], a['candidate']['sequence']['steps'])}
                     for a in integration['attempts']]
    selected = next((a for a in integration['attempts'] if a['candidate']['candidate_id'] == integration['selected_candidate_id']), None)
    selected_steps = selected['candidate']['sequence']['steps'] if selected else None
    actual = {'integration_status': integration['integration_status'], 'conclusion': integration['conclusion'],
              'search_conclusion': integration['search']['conclusion'], 'attempt_count': len(integration['attempts']),
              'selected_length': None if selected_steps is None else len(selected_steps),
              'manager_seen': None if selected_steps is None else any(s['activity_id'] == 'approve_manager' for s in selected_steps),
              'domain_checks_executed': len(domain_checks)}
    require(type(spec['expected']) is dict and bool(spec['expected']) and set(spec['expected']) <= actual.keys(), 'empty or unsupported expected fields')
    passed = all(type(actual[k]) is type(v) and actual[k] == v for k, v in spec['expected'].items())
    passed = passed and all(c['report']['passed'] for c in domain_checks)
    return {'id': spec['id'], 'raw': spec['input'], 'expected': spec['expected'], 'actual': actual,
            'criterion': 'exact typed equality of expected fields, plus all executed independent domain checks pass',
            'prepared': prepared, 'integration': integration, 'domain_checks': domain_checks,
            'passed': passed, 'real_payment_executed': False}


def policy_mutation(raw):
    """Negative control: weaken the model, keep the raw-domain monitor unchanged."""
    prepared = adapt(raw)
    catalog, request = deepcopy(prepared['catalog']), deepcopy(prepared['request'])
    # Omit the high-amount manager requirement from the derived eligibility rule.
    catalog['derived_rules'][0]['when'] = {'test': {'fact': {'predicate': 'screened', 'args': [{'param': 'claim'}]},
                                                  'op': 'eq', 'value': True}}
    request['catalog_sha256'] = canonical_sha256(catalog)
    search = SearchSession(prepare_problem(catalog, request)).run()
    require(bool(search['candidates']), 'mutation control failed to produce a witness')
    candidate = search['candidates'][0]
    formal = verify_candidate(catalog, request, candidate)
    domain = check_expense_policy(raw, candidate['sequence']['steps'])
    return {'raw': raw, 'mutated_catalog': catalog, 'mutated_request': request,
            'search': search, 'formal_verification': formal, 'independent_domain_check': domain,
            'expected': {'formal': 'passed', 'domain': False, 'reason': 'high amount requires manager approval'},
            'passed': formal['verification_status'] == 'passed' and domain['passed'] is False and
                      domain['reason'] == 'high amount requires manager approval', 'real_payment_executed': False}


def _sources():
    return sorted([*ROOT.joinpath('task_flow').rglob('*.py'), ROOT/'task_flow/schema.json',
                   Path(__file__).resolve(), ROOT/'workflow_tools/expense_workflow.py',
                   ROOT/'tests/expense_policy_reference.py', ROOT/'tests/test_expense_workflow.py',
                   EXAMPLES/'cases.json', EXAMPLES/'sample.json'])


def run(output, *, suite=None):
    suite = read_json(EXAMPLES/'cases.json') if suite is None else deepcopy(suite)
    require(type(suite) is dict and set(suite) == {'format_version', 'cases'} and
            suite['format_version'] == 'expense-study-cases-1.0', 'invalid expense study format')
    require(type(suite['cases']) is list and 1 <= len(suite['cases']) <= 32, 'expected nonempty bounded expense cases')
    ids = set()
    for spec in suite['cases']:
        require(type(spec) is dict and set(spec) == {'id', 'input', 'service_overrides', 'expected'}, 'invalid expense case fields')
        validate_input(spec['input'])
        require(spec['id'] == spec['input']['case_id'] and spec['id'] not in ids, 'ambiguous case identity')
        ids.add(spec['id'])
    require(not output.exists(), 'choose a new expense evidence directory')
    output.mkdir(parents=True)
    _write(output/'suite.json', suite)
    cases = [evaluate_case(c) for c in suite['cases']]
    for index, case in enumerate(cases):
        _write(output/f'case-{index:02d}.json', case)
    mutation = policy_mutation(read_json(EXAMPLES/'sample.json'))
    _write(output/'policy-mutation.json', mutation)
    summary = {'status': 'passed_with_scope_limits' if all(c['passed'] for c in cases) and mutation['passed'] else 'failed',
               'suite_sha256': canonical_sha256(suite), 'case_count': len(cases),
               'selected_case_count': sum(c['actual']['integration_status'] == 'selected' for c in cases),
               'independent_domain_checks': sum(len(c['domain_checks']) for c in cases),
               'mutation_control_passed': mutation['passed'],
               'scope': 'synthetic_expense_approval_no_real_financial_actions',
               'legacy_integration_status': 'not_implemented', 'real_payment_executed': False}
    _write(output/'summary.json', summary)
    manifest = {'source_sha256_lf_normalized': {p.relative_to(ROOT).as_posix(): hashlib.sha256(
        p.read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in _sources()},
        'artifact_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob('*.json'))}}
    _write(output/'manifest.json', manifest)
    return summary


def verify(output):
    manifest = read_json(output/'manifest.json')
    require(type(manifest) is dict and set(manifest) == {'source_sha256_lf_normalized', 'artifact_sha256'}, 'invalid expense manifest')
    require(set(manifest['source_sha256_lf_normalized']) == {p.relative_to(ROOT).as_posix() for p in _sources()}, 'incomplete source inventory')
    summary = read_json(output/'summary.json')
    require(type(summary['case_count']) is int and 1 <= summary['case_count'] <= 32, 'invalid evidence count')
    expected = {'suite.json', 'summary.json', 'policy-mutation.json'} | {f'case-{i:02d}.json' for i in range(summary['case_count'])}
    require(set(manifest['artifact_sha256']) == expected, 'incomplete artifact inventory')
    for name, digest in manifest['source_sha256_lf_normalized'].items():
        require(hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == digest, 'source changed: '+name)
    for name, digest in manifest['artifact_sha256'].items():
        require(hashlib.sha256((output/name).read_bytes()).hexdigest() == digest, 'artifact changed: '+name)
    require(summary['suite_sha256'] == canonical_sha256(read_json(output/'suite.json')), 'expense suite mismatch')
    require(summary['status'] == 'passed_with_scope_limits', 'expense evaluation failed')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'run', 'verify'])
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        require((args.command == 'prepare') == (args.input is not None), '--input is required only for prepare')
        result = prepare(args.input, args.output_dir) if args.command == 'prepare' else (
            run(args.output_dir) if args.command == 'run' else verify(args.output_dir))
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result['status'] == 'failed' else 0
    except (ContractError, OSError) as exc:
        print(json.dumps({'status': 'invalid_expense_input', 'reason': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
