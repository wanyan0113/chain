"""Source-backed table 10 screening, independent of task-to-capability rules.

Usage: python -B -m design.article_rules input.json
Input: task/artifact/context attribute dictionaries and role. Numeric values
require explicit value/unit pairs. This runs no chain/network search.
"""
from copy import deepcopy
import json
from pathlib import Path

from .engine import DesignError, condition_result, require_finite

RULE_PATH = Path(__file__).resolve().parents[1] / 'data/article_design/table10_screening.json'


def screen_table10(document):
    """Report only the two explicitly transcribed direct-screening groups."""
    from jsonschema import Draft202012Validator
    definitions = json.loads(Path(__file__).with_name('schema.json').read_text(encoding='utf-8'))['$defs']
    schema = {'type': 'object', 'additionalProperties': False,
              'required': ['role', 'task', 'artifact', 'context'],
              'properties': {'role': {'enum': ['insight', 'manifestation']},
                             **{key: {'$ref': '#/$defs/attributes'} for key in ('task','artifact','context')}},
              '$defs': definitions}
    require_finite(document)
    errors = list(Draft202012Validator(schema).iter_errors(document))
    if errors:
        raise DesignError('; '.join(error.message for error in errors[:5]))
    catalog = json.loads(RULE_PATH.read_text(encoding='utf-8'))
    rule = next(rule for rule in catalog['rules'] if rule['role'] == document['role'])
    checks = []
    for condition in rule['conditions']:
        status, reason = condition_result(condition, document)
        checks.append({**condition, 'status': status, 'reason': reason})
    statuses = {check['status'] for check in checks}
    return deepcopy({'scope': catalog['scope'], 'source': catalog['source'], 'rule': rule['id'],
                     'status': 'fail' if 'fail' in statuses else 'unknown' if 'unknown' in statuses else 'pass',
                     'input': document, 'checks': checks,
                     'validation_scope': '仅表10对应角色的直接筛选条件；pass不代表已推出能力、法器可用、协同成立或网络可执行'})


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    args = parser.parse_args()
    try:
        report = screen_table10(json.loads(args.input.read_text(encoding='utf-8')))
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if report['status'] == 'pass' else 1
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'invalid', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
