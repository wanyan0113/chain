"""Phase-two contract preflight. This command never generates a task plan."""
import argparse
import json

from .contracts import ContractError, prepare_problem, read_json
from .state import TransitionError, check_invariants, initial_state, state_records, state_view


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check'])
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--request', required=True)
    args = parser.parse_args(argv)
    try:
        problem = prepare_problem(read_json(args.catalog), read_json(args.request))
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
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if initial_status == 'passed' else 1
    except (ContractError, OSError) as exc:
        print(json.dumps({'status': getattr(exc, 'code', 'invalid_input'), 'reason': str(exc), 'planning_executed': False}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
