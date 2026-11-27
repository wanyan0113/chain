"""Reproduce finite document-service phase-five acceptance examples.

Correctness examples only; no scale/performance claim or old-system integration.
"""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path

from task_flow.contracts import prepare_problem, read_json, require
from task_flow.external import FeedbackStore, IntegrationSession

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'task_flow/examples/document_workflow'


def run(output):
    require(not output.exists(), 'choose a new evidence directory')
    mapping = read_json(EXAMPLES / 'service_mapping.json')
    context = read_json(EXAMPLES / 'services_backup_offline.json')
    catalog = read_json(EXAMPLES / 'catalog.json')
    cases = []
    output.mkdir(parents=True)

    def case(name, request, expected, *, m=None, c=None, feedback=None, max_checks=100):
        report = IntegrationSession(prepare_problem(catalog, request), m or mapping,
                                    context if c is None else c, feedback=feedback).run(max_checks=max_checks)
        actual = {'status': report['integration_status'], 'conclusion': report['conclusion'],
                  'external_statuses': [a['analysis']['external_status'] for a in report['attempts']],
                  'lengths': [len(a['candidate']['sequence']['steps']) for a in report['attempts']],
                  'cache_hits': [a['feedback_reused'] for a in report['attempts']]}
        passed = all(actual[k] == value for k, value in expected.items())
        path = output / (name + '.json')
        path.write_text(json.dumps({'name': name, 'expected': expected, 'actual': actual,
                                    'passed': passed, 'criterion': 'exact equality for every declared expected field',
                                    'report': report}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
        cases.append({'name': name, 'file': path.name, 'passed': passed})

    request = read_json(EXAMPLES / 'feedback_fallback.json')
    selected = 'declared_external_conditions_satisfied'
    none = 'no_supported_flow_in_declared_bounds_and_context'
    case('fallback', request, {'status': 'selected', 'conclusion': selected,
                              'external_statuses': ['rejected', 'passed'], 'lengths': [3, 3]})
    online = deepcopy(context)
    for service in online['services']:
        service['available'] = True
    for name, length in [('already_done', 0), ('mandatory_verify', 1), ('collected', 2), ('basic', 3), ('verified', 4)]:
        case(name, read_json(EXAMPLES / (name + '.json')),
             {'status': 'selected', 'lengths': [length], 'external_statuses': ['passed']}, c=online)
    unsupported = deepcopy(mapping)
    unsupported['backend'] = 'legacy-design'
    short = read_json(EXAMPLES / 'collected.json')
    short['search']['max_length'] = 2
    case('unsupported_legacy', short, {'status': 'search_exhausted', 'conclusion': 'undetermined',
                                      'external_statuses': ['unsupported_mapping', 'unsupported_mapping']}, m=unsupported)
    unknown = deepcopy(context)
    unknown['services'] = []
    case('unknown_services', short, {'status': 'search_exhausted', 'conclusion': 'undetermined',
                                     'external_statuses': ['blocked_unknown', 'blocked_unknown']}, c=unknown)
    case('too_short', read_json(EXAMPLES / 'too_short.json'),
         {'status': 'search_exhausted', 'conclusion': none, 'lengths': []})
    case('unknown_source', read_json(EXAMPLES / 'unknown_source.json'),
         {'status': 'search_exhausted', 'conclusion': 'undetermined', 'lengths': []})
    case('analysis_limit', request, {'status': 'analysis_limit', 'conclusion': 'undetermined',
                                    'external_statuses': ['rejected']}, max_checks=1)
    limited = deepcopy(request)
    limited['search']['max_nodes'] = 1
    case('search_limit', limited, {'status': 'search_limit', 'conclusion': 'undetermined', 'lengths': []})
    offline = deepcopy(context)
    for service in offline['services']:
        service['available'] = False
    case('all_rejected', short, {'status': 'search_exhausted', 'conclusion': none,
                                 'external_statuses': ['rejected', 'rejected']}, c=offline)
    store = FeedbackStore()
    case('feedback_first', request, {'cache_hits': [False, False]}, feedback=store)
    case('feedback_repeat', request, {'cache_hits': [True, False]}, feedback=store)
    case('context_changed', request, {'cache_hits': [False], 'external_statuses': ['passed']}, c=online, feedback=store)
    sources = sorted({*ROOT.joinpath('task_flow').rglob('*.py'), *ROOT.joinpath('task_flow').rglob('*.json'),
                      Path(__file__).resolve(), ROOT / 'tests/test_task_flow_external.py'})
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
              for p in sources}
    result = {'status': 'passed' if all(c['passed'] for c in cases) else 'failed',
              'cases': cases, 'source_sha256_lf_normalized': hashes,
              'scope': 'finite_synthetic_document_examples_only',
              'legacy_integration_status': 'not_implemented', 'performance_evaluated': False}
    (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    result = run(args.output_dir)
    print(json.dumps({'status': result['status'], 'cases': len(result['cases'])}))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
