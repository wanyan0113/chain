"""Contract preflight, bounded sequence search and independent replay verification."""
import argparse
import json
from pathlib import Path

from .contracts import ContractError, prepare_problem, read_json, require, validate_shape
from .planner import SearchSession
from .state import TransitionError, check_invariants, initial_state, state_records, state_view
from .validator import MAX_TRACE_RECORDS, verify_search_report, verify_sequence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check', 'plan', 'verify'])
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--request', required=True)
    parser.add_argument('--mode', choices=['first_feasible', 'enumerate_bounded'])
    parser.add_argument('--max-nodes', type=int)
    parser.add_argument('--timeout-ms', type=int)
    parser.add_argument('--max-candidates', type=int)
    parser.add_argument('--verify', action='store_true', help='Independently verify plan candidates')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--sequence', type=Path)
    source.add_argument('--report', type=Path)
    parser.add_argument('--verification-max-candidates', type=int)
    parser.add_argument('--verification-max-trace-records', type=int)
    parser.add_argument('--output', type=Path, help='Write report to a new file; never overwrite')
    args = parser.parse_args(argv)
    try:
        if args.output is not None:
            require(not args.output.exists(), 'output already exists; choose a new report file')
        request = read_json(args.request)
        require(args.command == 'plan' or not args.verify, '--verify requires plan command')
        require(args.command == 'verify' or (args.sequence is None and args.report is None), 'sequence/report inputs require verify command')
        require(args.command != 'verify' or args.sequence is not None or args.report is not None, 'verify needs --sequence or --report')
        require(args.command == 'verify' or args.verify or all(v is None for v in (args.verification_max_candidates, args.verification_max_trace_records)),
                'verification budgets require verify or plan --verify')
        if args.command != 'plan':
            require(all(v is None for v in (args.mode, args.max_nodes, args.timeout_ms, args.max_candidates)),
                    'search options require plan command')
        elif args.mode is not None:
            validate_shape(request, 'request')
            request['search']['mode'] = args.mode
        problem = prepare_problem(read_json(args.catalog), request)
        verification_options = {'max_candidates': args.verification_max_candidates if args.verification_max_candidates is not None else 1000,
                                'max_trace_records': args.verification_max_trace_records if args.verification_max_trace_records is not None else MAX_TRACE_RECORDS}
        if args.command == 'verify':
            if args.sequence is not None:
                require(args.verification_max_candidates is None, 'sequence verification has no candidate batch budget')
                result = verify_sequence(problem.catalog, problem.request, read_json(args.sequence), max_trace_records=verification_options['max_trace_records'])
            else:
                result = verify_search_report(problem.catalog, problem.request, read_json(args.report, allow_finite_floats=True), **verification_options)
            _emit(result, args.output)
            return 0 if result['verification_status'] == 'passed' else 1
        if args.command == 'plan':
            result = SearchSession(problem).run(max_nodes=args.max_nodes, timeout_ms=args.timeout_ms,
                                               max_candidates=args.max_candidates)
            code = 0 if result['candidates'] and result['search_status'] != 'limit_reached' else 1
            if args.verify:
                batch = verify_search_report(problem.catalog, problem.request, result, **verification_options)
                result['independent_verification'] = batch
                result['independent_verification_executed'] = batch['independent_verification_executed']
                statuses = {item['candidate_id']: item['report']['verification_status'] for item in batch['candidate_verifications']}
                for candidate in result['candidates']:
                    candidate['verification_status'] = statuses.get(candidate['candidate_id'], 'not_run')
                if batch['verification_status'] != 'passed':
                    code = 1
            _emit(result, args.output)
            return code
        base = initial_state(problem)
        initial_status = 'passed'
        reason = None
        try:
            check_invariants(problem, state_view(problem, base))
        except TransitionError as exc:
            initial_status, reason = exc.status, exc.reason
        result = {'status': 'valid_input', 'catalog_sha256': problem.catalog_sha256,
                  'request_sha256': problem.request_sha256, 'ground_facts': len(problem.fact_specs),
                  'ground_activity_instances': len(problem.instances), 'initial_constraint_status': initial_status,
                  'initial_constraint_reason': reason, 'initial_state': state_records(problem, base),
                  'unmapped_capabilities': [c['uri'] for c in problem.catalog['capabilities']],
                  'planning_executed': False, 'independent_verification_executed': False}
        _emit(result, args.output)
        return 0 if initial_status == 'passed' else 1
    except (ContractError, OSError) as exc:
        print(json.dumps({'status': getattr(exc, 'code', 'invalid_input'), 'reason': str(exc),
                          'planning_executed': False, 'independent_verification_executed': False}, ensure_ascii=False))
        return 2


def _emit(result, path):
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(payload + '\n')
    print(payload)


if __name__ == '__main__':
    raise SystemExit(main())
