"""Run the generic document-service integration prototype."""
import argparse
import json
from pathlib import Path

from .contracts import ContractError, prepare_problem, read_json, require
from .external import IntegrationSession


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('catalog', 'request', 'mapping', 'context'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--max-checks', type=int, default=100)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        require(args.output is None or not args.output.exists(), 'output already exists')
        problem = prepare_problem(read_json(args.catalog), read_json(args.request))
        result = IntegrationSession(problem, read_json(args.mapping), read_json(args.context)).run(
            max_checks=args.max_checks)
        payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(payload + '\n')
        print(payload)
        return 0 if result['integration_status'] == 'selected' else 1
    except (ContractError, OSError) as exc:
        print(json.dumps({'status': getattr(exc, 'code', 'invalid_input'), 'reason': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
