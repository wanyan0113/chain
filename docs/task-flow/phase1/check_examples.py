"""Check phase-one design artifacts and manual witnesses; not a planner.

Run from any directory with Python 3.11+. No third-party packages required.
This checks positive witnesses only; it does not enumerate or prove coverage.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = ROOT / '\u6269\u5c55\u94fe\u4efb\u52a1\u4e66.txt'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f'duplicate JSON key: {key}')
        result[key] = value
    return result


def matches(conditions, state):
    # This deliberately handles only the explicitly boolean design example.
    return all(state.get(key) is value for key, value in conditions.items())


def derive(state, rules):
    for rule in rules:
        values = [state.get(key) is value if state.get(key) is not None else None
                  for key, value in rule['all'].items()]
        state[rule['head']] = (False if False in values else
                               None if None in values else True)


def main():
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    require(source_hash in (HERE / 'README.md').read_text(encoding='utf-8'),
            'task source changed; update the review baseline explicitly')
    links = 0
    for path in HERE.glob('*.md'):
        for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            require('://' not in target, 'design review expects local links')
            require((path.parent / target.split('#')[0]).exists(), f'broken link: {target}')
            links += 1
    data = json.loads((HERE / 'examples/document-workflow.json').read_text(encoding='utf-8'),
                      object_pairs_hook=unique_pairs)
    facts = {f['id']: f for f in data['facts']}
    activities = {a['id']: a for a in data['activities']}
    require(len(facts) == len(data['facts']), 'duplicate fact id')
    require(len(activities) == len(data['activities']), 'duplicate activity id')
    require(len({c['id'] for c in data['cases']}) == len(data['cases']), 'duplicate case id')
    for action in activities.values():
        require(action['version'] == data['version'], 'version mismatch')
        require(action['parameters'] == {}, 'example checker only supports ground activities')
        require(action['required_capabilities'] and action['source_refs'], 'missing provenance/capability')
        for group in ('preconditions', 'applicable_when', 'effects'):
            require(set(action[group]) <= facts.keys(), 'undeclared fact')
            require(all(type(v) is bool for v in action[group].values()), 'non-boolean condition/effect')
        require(all(facts[key]['kind'] == 'mutable' for key in action['effects']), 'write to static/derived')
    for rule in data['derived_rules']:
        require(facts[rule['head']]['kind'] == 'derived', 'invalid derived head')
        require(all(facts[key]['kind'] != 'derived' for key in rule['all']), 'example requires nonrecursive rules')
    witness_count = 0
    case_results = []
    for case in data['cases']:
        request = deepcopy(data['base_request'])
        request['initial_state'].update(case.get('initial_state_overrides', {}))
        request['goal'].update(case.get('goal_overrides', {}))
        require(set(request['initial_state']) <= facts.keys(), 'undeclared initial fact')
        require(set(request['goal']) <= facts.keys(), 'undeclared goal fact')
        required = case.get('required_activities', request['required_activities'])
        limit = case.get('max_length', request['search']['max_length'])
        for witness in case.get('manual_witnesses', []):
            state = deepcopy(request['initial_state'])
            derive(state, data['derived_rules'])
            require(len(witness) <= limit, 'witness exceeds length bound')
            counts = Counter(witness)
            for name in witness:
                action = activities[name]
                require(counts[name] <= action['max_occurrences'], 'repetition limit exceeded')
                require(matches(action['applicable_when'], state), 'applicability not established')
                require(matches(action['preconditions'], state), f'precondition failed: {case["id"]}/{name}')
                state.update(action['effects'])
                derive(state, data['derived_rules'])
            require(matches(request['goal'], state), 'witness does not reach final goal')
            require(set(required) <= set(witness), 'mandatory activity missing')
            require(witness not in case.get('external_rejected_sequences', []), 'external rejection ignored')
            witness_count += 1
        if case['expected'] == 'witness_exists':
            require(bool(case.get('manual_witnesses')), 'positive case lacks manual witness')
        else:
            require(bool(case.get('manual_reason')), 'negative case lacks manual explanation')
        case_results.append({'case': case['id'], 'manual_witnesses_checked': len(case.get('manual_witnesses', [])),
                             'negative_expectation': 'manual_only' if case['expected'] != 'witness_exists' else None})
    print(json.dumps({'check': 'passed', 'scope': 'design links, source digest and positive manual witnesses only',
                      'source_sha256': source_hash, 'links_checked': links, 'activities': len(activities),
                      'cases': len(data['cases']), 'witnesses_checked': witness_count,
                      'planner_implemented': False, 'coverage_proven': False, 'results': case_results}, indent=2))


if __name__ == '__main__':
    main()
