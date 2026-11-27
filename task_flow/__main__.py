"""Contract preflight and bounded sequence search; no independent verifier."""
import argparse
import json
from pathlib import Path

from .contracts import ContractError, prepare_problem, read_json, require, validate_shape
from .planner import SearchSession
from .state import TransitionError, check_invariants, initial_state, state_records, state_view


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check', 'plan'])
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--request', required=True)
    parser.add_argument('--mode', choices=['first_feasible', 'enumerate_bounded'])
    parser.add_argument('--max-nodes', type=int)
    parser.add_argument('--timeout-ms', type=int)
    parser.add_argument('--max-candidates', type=int)
    parser.add_argument('--output', type=Path, help='Write report to a new file; never overwrite')
    args = parser.parse_args(argv)
    try:
        if args.output is not None:
            require(not args.output.exists(), 'output already exists; choose a new report file')
        request = read_json(args.request)
        if args.command == 'check':
            require(all(v is None for v in (args.mode, args.max_nodes, args.timeout_ms, args.max_candidates)),
                    'search options require plan command')
        elif args.mode is not None:
            validate_shape(request, 'request')
            request['search']['mode'] = args.mode
        problem = prepare_problem(read_json(args.catalog), request)
        if args.command == 'plan':
            result = SearchSession(problem).run(max_nodes=args.max_nodes, timeout_ms=args.timeout_ms,
                                               max_candidates=args.max_candidates)
            code = 0 if result['candidates'] and result['search_status'] != 'limit_reached' else 1
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
        print(json.dumps({'status': getattr(exc, 'code', 'invalid_input'), 'reason': str(exc), 'planning_executed': False}, ensure_ascii=False))
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
