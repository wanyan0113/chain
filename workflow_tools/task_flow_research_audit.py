"""Reproducible phase-one-to-four research audit; no new planning features.

python -m workflow_tools.task_flow_research_audit run --output-dir NEW_DIRECTORY
python -m workflow_tools.task_flow_research_audit verify --output-dir DIRECTORY
"""
import argparse
import ast
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from importlib import metadata
from itertools import product
import json
import platform
from pathlib import Path
import random
import re
import subprocess
import sys
import time
from unittest.mock import Mock, patch

from task_flow import SearchSession, prepare_problem, read_json, verify_candidate, verify_search_report, verify_sequence
from task_flow.contracts import ContractError, canonical_sha256
from task_flow.state import TransitionError, apply_activity, goal_status, initial_state, state_records, state_view
from tests.task_flow_oracle import exhaustive_sequences, sequence_key

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'task_flow/examples/document_workflow'
SEED = 2026112704
BASELINE_MODULES = ['test_task_flow_contracts', 'test_task_flow_state', 'test_task_flow_planner',
                    'test_task_flow_validator', 'test_network_search', 'test_design', 'test_sequences',
                    'test_table10_pruning', 'test_flow_scenario', 'test_equipment_bridge',
                    'test_bridge_intake', 'test_bridge_observed', 'test_flow_compare']


def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8', newline='\n')


def fixture(name='basic'):
    return read_json(EXAMPLES / 'catalog.json'), read_json(EXAMPLES / (name + '.json'))


def rebind(cat, req):
    req['catalog_sha256'] = canonical_sha256(cat)
    return cat, req


def ref(name, symbolic=False):
    return {'predicate': name, 'args': [{'param': 'document'}] if symbolic else ['doc-1']}


def test(name, value=True, op='eq', unit=None):
    item = {'fact': ref(name), 'op': op, 'value': value}
    if unit:
        item['unit'] = unit
    return {'test': item}


def seq(cat, req, names):
    return {'format_version': 'task-flow-1.0', 'catalog_sha256': canonical_sha256(cat),
            'request_sha256': canonical_sha256(req), 'steps': [
                {'activity_id': n, 'activity_version': '1.0', 'parameters': {'document': 'doc-1'}} for n in names]}


def outcome_error(function):
    try:
        function()
    except ContractError as exc:
        return exc.code
    return 'unexpected_acceptance'


def stepset(report):
    return {sequence_key(c['sequence']['steps']) for c in report['candidates']}


def fields(report):
    return {k: report[k] for k in ('search_status', 'termination_reason', 'conclusion', 'coverage')}


@dataclass
class Case:
    id: str
    title: str
    requirements: list
    inputs: dict
    expected: dict
    probe: object


def mixed_models(count=32):
    """Finite deterministic synthetic models; all exact instances saved in the audit."""
    _validate_model_count(count)
    rng = random.Random(SEED)
    models = []
    sources = ['synthetic-research-audit']
    for number in range(count):
        def atom(n):
            return {'predicate': n, 'args': []}
        def condition():
            n = rng.choice(['p', 'q', 'ready', 'left', 'right', 'available'])
            item = {'fact': atom(n), 'op': 'eq', 'value': bool(rng.randrange(2))}
            if n in ('left', 'right'):
                item.update(op=rng.choice(['eq', 'ne', 'ge', 'lt']), value=rng.randrange(-1, 5), unit='count')
            result = {'test': item}
            return {'not': result} if rng.randrange(3) == 0 else result
        cat = {'format_version': 'task-flow-1.0', 'catalog_id': 'audit-mixed', 'version': '1', 'objects': {},
               'facts': [{'id': n, 'arguments': [], 'kind': 'static' if n == 'available' else 'derived' if n == 'ready' else 'mutable',
                          'type': 'boolean', 'source_refs': sources} for n in ['available', 'p', 'q', 'ready']],
               'derived_rules': [{'id': 'derive-ready', 'parameters': {}, 'head': atom('ready'),
                                  'when': {'all': [{'test': {'fact': atom('left'), 'op': 'ge', 'value': 1, 'unit': 'count'}},
                                                   {'test': {'fact': atom('p'), 'op': 'eq', 'value': True}}]}, 'source_refs': sources}],
               'activities': [], 'capabilities': [{'uri': 'urn:audit:capability', 'mapping_status': 'unmapped', 'source_refs': sources}], 'source_refs': sources}
        cat['facts'] += [{'id': n, 'arguments': [], 'kind': 'mutable', 'type': 'integer', 'minimum': 0,
                         'maximum': 3, 'unit': 'count', 'source_refs': sources} for n in ['left', 'right']]
        for index in range(rng.randint(2, 4)):
            if rng.randrange(2):
                effects = [{'fact': atom(n), 'set': {'read': atom(rng.choice(['left', 'right'])), 'offset': rng.randrange(-2, 3), 'unit': 'count'}}
                           for n in ['left', 'right']]
            else:
                effect = {'fact': atom(rng.choice(['p', 'q']))}
                effect.update({'forget': True} if rng.randrange(3) == 0 else {'set': {'value': bool(rng.randrange(2))}})
                effects = [effect]
            cat['activities'].append({'id': f'a{index}', 'version': '1', 'type': 'audit', 'parameters': {},
                                       'applicable_when': {'all': []}, 'preconditions': condition() if rng.randrange(2) else {'all': []},
                                       'effects': effects, 'required_capabilities': ['urn:audit:capability'], 'max_occurrences': 2, 'source_refs': sources})
        request = {'format_version': 'task-flow-1.0', 'request_id': f'model-{number:02}', 'catalog_id': cat['catalog_id'],
                   'catalog_version': '1', 'catalog_sha256': canonical_sha256(cat),
                   'static_facts': [{'fact': atom('available'), 'value': rng.choice([True, False, None])}],
                   'initial_state': [{'fact': atom(n), 'value': rng.choice([True, False, None])} for n in ['p', 'q']] +
                                    [{'fact': atom(n), 'value': rng.choice([None, 0, 1, 2, 3]), 'unit': 'count'} for n in ['left', 'right']],
                   'ontology_evidence': [], 'goal': condition(), 'required_activities': ['a0'] if number % 4 == 0 else [],
                   'forbidden_conditions': [condition()] if number % 6 == 0 else [], 'activity_limits': {},
                   'search': {'mode': 'enumerate_bounded', 'max_length': 3, 'max_nodes': 20000, 'timeout_ms': 20000}, 'source_refs': sources}
        models.append({'catalog': cat, 'request': request})
    return models


def build_cases(model_count=32):
    _validate_model_count(model_count)
    cases = []
    cat, req = fixture()
    base = {'catalog': cat, 'request': req}
    def add(number, title, requirements, inputs, expected, probe):
        cases.append(Case(f'C{number:02}', title, requirements, deepcopy(inputs), expected, probe))

    def contract_probe(data):
        c, r = deepcopy(data['catalog']), deepcopy(data['request'])
        good = prepare_problem(c, r)
        del c['activities'][0]['required_capabilities']
        return {'instance_count': len(good.instances), 'capabilities_structured': all(type(a['required_capabilities']) is list and a['required_capabilities'] for a in good.activities.values()),
                'missing_capability_field': outcome_error(lambda: prepare_problem(c, r))}
    add(1, '活动定义与能力结构', ['R01'], base,
        {'instance_count': 5, 'capabilities_structured': True, 'missing_capability_field': 'input_incomplete'}, contract_probe)

    def domains(data):
        c, r = deepcopy(data['catalog']), deepcopy(data['request'])
        c['objects']['document'].append('doc-2'); rebind(c, r)
        p = prepare_problem(c, r)
        budget = outcome_error(lambda: prepare_problem(c, r, max_groundings=2))
        c['activities'][0]['effects'][0]['fact']['args'] = ['missing-object']; rebind(c, r)
        return {'instances': len(p.instances), 'facts': len(p.fact_specs), 'budget': budget,
                'outside_domain': outcome_error(lambda: prepare_problem(c, r))}
    add(2, '有限域及实例上限', ['R02'], base, {'instances': 10, 'facts': 11, 'budget': 'invalid_input', 'outside_domain': 'invalid_input'}, domains)

    table = [(True, True, True, True), (True, False, False, True), (True, None, None, True),
             (False, True, False, True), (False, False, False, False), (False, None, False, None),
             (None, True, None, True), (None, False, False, None), (None, None, None, None)]
    expected_truth = [{'left': a, 'right': b, 'operator': op, 'value': v} for a, b, both, either in table
                      for op, v in [('all', both), ('any', either), ('not_all', None if both is None else not both)]]
    def truth(data):
        results = []
        for row in data['truth_table']:
            c, r = deepcopy(data['catalog']), deepcopy(data['request'])
            r['initial_state'][0]['value'], r['initial_state'][1]['value'] = row['left'], row['right']
            children = [test('collected'), test('approved')]
            r['goal'] = {'not': {'all': children}} if row['operator'] == 'not_all' else {row['operator']: children}
            p = prepare_problem(c, r)
            generated = goal_status(p, initial_state(p))
            checked = verify_sequence(c, r, seq(c, r, []))['final_checks']['goal']['value']
            results.append(dict(row, value=generated, verifier_value=checked))
        return {'rows': results}
    add(3, '三值事实完整真值表', ['R03'], dict(base, truth_table=[{k: row[k] for k in ('left', 'right', 'operator')} for row in expected_truth]),
        {'rows': [dict(row, verifier_value=row['value']) for row in expected_truth]}, truth)

    numeric = deepcopy(base)
    for name, value in [('left', 5), ('right', 2)]:
        numeric['catalog']['facts'].append({'id': name, 'arguments': ['document'], 'kind': 'mutable', 'type': 'integer',
                                            'minimum': 0, 'maximum': 20, 'unit': 'count', 'source_refs': ['audit']})
        numeric['request']['initial_state'].append({'fact': ref(name), 'value': value, 'unit': 'count'})
    numeric['catalog']['activities'][0]['effects'] = [
        {'fact': ref('left', True), 'set': {'read': ref('right', True), 'offset': 1, 'unit': 'count'}},
        {'fact': ref('right', True), 'set': {'read': ref('left', True), 'offset': -1, 'unit': 'count'}}]
    numeric['request']['goal'] = test('left', 3, unit='count'); rebind(numeric['catalog'], numeric['request'])
    def atomic(data):
        c, r = deepcopy(data['catalog']), deepcopy(data['request']); p = prepare_problem(c, r)
        before = initial_state(p); after = apply_activity(p, before, seq(c, r, ['collect'])['steps'][0])
        v = verify_sequence(c, r, seq(c, r, ['collect']))
        values = lambda state: [state[(n, 'doc-1')] for n in ('left', 'right')]
        c['activities'][0]['effects'][1]['set']['offset'] = 100; rebind(c, r); p = prepare_problem(c, r)
        original = initial_state(p)
        try:
            apply_activity(p, original, seq(c, r, ['collect'])['steps'][0]); status = 'unexpected_acceptance'
        except TransitionError as error:
            status = error.status
        bad = verify_sequence(c, r, seq(c, r, ['collect']))
        final_values = {row['fact']['predicate']: row['value'] for row in bad['final_state']}
        return {'old_reads': values(after), 'initial_unchanged': values(before), 'valid_replay': v['verification_status'],
                'overflow': status, 'rollback': values(original), 'replay_overflow': bad['verification_status'],
                'replay_rollback': [final_values[n] for n in ('left', 'right')]}
    add(4, '多效果旧值读取及失败原子性', ['R03'], numeric,
        {'old_reads': [3, 4], 'initial_unchanged': [5, 2], 'valid_replay': 'passed', 'overflow': 'rejected',
         'rollback': [5, 2], 'replay_overflow': 'rejected', 'replay_rollback': [5, 2]}, atomic)

    def derived(data):
        c, r = deepcopy(data['catalog']), deepcopy(data['request'])
        r['initial_state'][0]['value'] = r['initial_state'][1]['value'] = True
        r['goal'] = test('ready')
        c['activities'][0]['effects'] = [{'fact': ref('collected', True), 'forget': True}]; rebind(c, r)
        p = prepare_problem(c, r); old = initial_state(p)
        after = apply_activity(p, old, seq(c, r, ['collect'])['steps'][0])
        replay = verify_sequence(c, r, seq(c, r, ['collect']))
        return {'before': state_view(p, old)[('ready', 'doc-1')], 'after': state_view(p, after)[('ready', 'doc-1')],
                'replay_value': replay['final_checks']['goal']['value'], 'replay_status': replay['verification_status']}
    add(5, '派生事实失效重算', ['R03'], base, {'before': True, 'after': None, 'replay_value': None, 'replay_status': 'blocked_unknown'}, derived)

    def conflict(data):
        c, r = deepcopy(data['catalog']), deepcopy(data['request'])
        c['activities'][0]['effects'].append({'fact': ref('collected', True), 'set': {'value': False}}); rebind(c, r)
        return {'conflicting_effects': outcome_error(lambda: prepare_problem(c, r))}
    add(6, '同一事实冲突写入', ['R02', 'R03'], base, {'conflicting_effects': 'invalid_input'}, conflict)

    def binding(data):
        c, r = data['catalog'], data['request']; s = seq(c, r, ['collect'])
        s['steps'][0]['activity_version'] = 'bad'
        wrong_version = verify_sequence(c, r, s)
        s = seq(c, r, []); s['request_sha256'] = '0' * 64
        return {'version': wrong_version['verification_status'], 'failed_step': wrong_version['failure']['step_index'],
                'request_digest': verify_sequence(c, r, s)['verification_status']}
    add(7, '版本与输入绑定', ['R01', 'R06'], base, {'version': 'rejected', 'failed_step': 0, 'request_digest': 'rejected'}, binding)

    variants = {name: fixture(name)[1] for name in ['basic', 'verified', 'collected', 'already_done', 'mandatory_verify']}
    def variation(data):
        rows = {}
        for name, request in data['requests'].items():
            result = SearchSession(prepare_problem(data['catalog'], request)).run()
            rows[name] = len(result['candidates'][0]['sequence']['steps'])
        return rows
    add(8, '目标和初始状态决定活动数量', ['R04', 'R05'], {'catalog': cat, 'requests': variants},
        {'basic': 3, 'verified': 4, 'collected': 2, 'already_done': 0, 'mandatory_verify': 1}, variation)

    def destroyed(data):
        c, r = deepcopy(data['catalog']), deepcopy(data['request'])
        r['initial_state'][2]['value'] = True
        c['activities'][0]['effects'] = [{'fact': ref('published', True), 'set': {'value': False}}]; rebind(c, r)
        p = prepare_problem(c, r); before = initial_state(p)
        return {'before': goal_status(p, before), 'after': goal_status(p, apply_activity(p, before, seq(c, r, ['collect'])['steps'][0])),
                'verification': verify_sequence(c, r, seq(c, r, ['collect']))['verification_status']}
    add(9, '中途满足不能替代最终目标', ['R05'], base, {'before': True, 'after': False, 'verification': 'rejected'}, destroyed)

    requests = {Path(row['request']).stem: read_json(EXAMPLES / row['request']) for row in read_json(EXAMPLES / 'manual_cases.json')['cases']}
    references = {}
    for name, r in requests.items():
        r['search']['mode'] = 'enumerate_bounded'
        keys = exhaustive_sequences(cat, r)
        references[name] = {'count': len(keys), 'sequence_set_sha256': digest(sorted(keys))}
    def enumeration(data):
        rows = {}
        for name, r in data['requests'].items():
            result = SearchSession(prepare_problem(data['catalog'], r)).run()
            rows[name] = {'count': len(result['candidates']), 'sequence_set_sha256': digest(sorted(stepset(result)))}
        return rows
    add(10, '八场景完整集合独立穷举对照', ['R07', 'R11'], {'catalog': cat, 'requests': requests}, references, enumeration)

    def continuation(data):
        p = prepare_problem(data['catalog'], data['request']); session = SearchSession(p)
        ids = []
        for _ in range(100):
            result = session.run()
            if result['search_status'] == 'exhausted': break
            ids.append(result['candidates'][-1]['candidate_id'])
        return {'terminal': result['search_status'], 'candidates': len(result['candidates']), 'unique': len(set(ids)),
                'complete_set': digest(sorted(stepset(result)))}
    add(11, '首解恢复与完整枚举一致', ['R07'], base,
        {'terminal': 'exhausted', 'candidates': 6, 'unique': 6, 'complete_set': references['basic']['sequence_set_sha256']}, continuation)

    def limits(data):
        c, r = deepcopy(data['catalog']), deepcopy(data['request']); r['search']['mode'] = 'enumerate_bounded'
        p = prepare_problem(c, r); node = SearchSession(p).run(max_nodes=1); cap = SearchSession(p).run(max_candidates=1)
        ticks = iter(range(10000)); timed = SearchSession(p, clock=lambda: next(ticks) / 1000).run(timeout_ms=1)
        return {'node': [node['search_status'], node['statistics']['nodes'], node['coverage']],
                'candidate': [cap['termination_reason'], len(cap['candidates']), cap['coverage']],
                'time': [timed['termination_reason'], timed['coverage']]}
    add(12, '节点时间及候选超限不冒充无解', ['R08'], base,
        {'node': ['limit_reached', 1, 'partial'], 'candidate': ['candidate_limit', 1, 'partial'], 'time': ['time_limit', 'partial']}, limits)

    def identity(data):
        c, r = deepcopy(data['catalog']), deepcopy(data['request']); r['search']['mode'] = 'enumerate_bounded'
        triples = [row for row in SearchSession(prepare_problem(c, r)).run()['candidates'] if len(row['sequence']['steps']) == 3]
        return {'count': len(triples), 'same_final': triples[0]['final_state'] == triples[1]['final_state'],
                'different_id': triples[0]['candidate_id'] != triples[1]['candidate_id'],
                'different_capability': triples[0]['capability_demands'] != triples[1]['capability_demands']}
    add(13, '同状态不同序列与能力不误去重', ['R07', 'R10'], base,
        {'count': 2, 'same_final': True, 'different_id': True, 'different_capability': True}, identity)

    def repeat(data):
        c, r = deepcopy(data['catalog']), deepcopy(data['request']); r['search']['mode'] = 'enumerate_bounded'
        signatures = []
        for _ in range(3):
            report = SearchSession(prepare_problem(c, r)).run()
            del report['statistics']['elapsed_ms']
            signatures.append(digest(report))
        return {'runs': 3, 'identical_non_time_records': len(set(signatures)) == 1}
    add(14, '同版本同输入重复运行', ['R07', 'R11'], base, {'runs': 3, 'identical_non_time_records': True}, repeat)

    mutations = [(['approve', 'collect', 'publish_standard'], 0), (['collect', 'publish_standard'], 1),
                 (['missing'], 0), (['collect', 'collect'], 1)]
    controls = []
    for row in read_json(EXAMPLES / 'manual_cases.json')['cases']:
        for witness in row['manual_witnesses']:
            controls.append({'request': read_json(EXAMPLES / row['request']), 'steps': witness})
    def mutation_probe(data):
        c, r = data['catalog'], data['request']; rows = []
        for names, expected_index in data['wrong_sequences']:
            v = verify_sequence(c, r, seq(c, r, names))
            rows.append([v['verification_status'], v['failure']['step_index']])
        good = SearchSession(prepare_problem(c, r)).run()['candidates'][0]
        for field in ('candidate_id', 'final_state', 'capability_demands', 'generation_goal_satisfied'):
            candidate = deepcopy(good)
            candidate[field] = 'wrong' if field == 'candidate_id' else False if field == 'generation_goal_satisfied' else []
            rows.append([verify_candidate(c, r, candidate)['verification_status'], None])
        good_controls = []
        for control in data['positive_controls']:
            s = seq(c, control['request'], []); s['steps'] = control['steps']
            good_controls.append(verify_sequence(c, control['request'], s)['verification_status'])
        return {'mutation_results': rows, 'positive_controls': good_controls}
    add(15, '故意错误流程与伪造候选', ['R06', 'R11'], dict(base, wrong_sequences=mutations, positive_controls=controls),
        {'mutation_results': [['rejected', i] for _, i in mutations] + [['rejected', None]] * 4,
         'positive_controls': ['passed'] * 7}, mutation_probe)

    def independence(data):
        c, r = data['catalog'], data['request']; bad = Mock(side_effect=AssertionError('generation-side function called'))
        with patch.multiple('task_flow.state', evaluate_condition=bad, state_view=bad, apply_activity=bad, goal_status=bad, initial_state=bad), patch('task_flow.planner.SearchSession.run', bad):
            v = verify_sequence(c, r, seq(c, r, ['collect', 'approve', 'publish_standard']))
        tree = ast.parse((ROOT / 'task_flow/validator.py').read_text(encoding='utf-8'))
        imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        return {'status': v['verification_status'], 'generation_calls': bad.call_count,
                'imports_generation_semantics': any(n in ('state', 'planner') for n in imports)}
    add(16, '验证核心语义执行独立性', ['R06'], base,
        {'status': 'passed', 'generation_calls': 0, 'imports_generation_semantics': False}, independence)

    def injection(data):
        c, r = data['catalog'], data['request']; p = prepare_problem(c, r)
        with patch('task_flow.planner.goal_status', return_value=True):
            candidate = SearchSession(p).run()['candidates'][0]
        a = verify_candidate(c, r, candidate)
        def illegal_step(problem, base, instance):
            changed = deepcopy(base); changed[('published', 'doc-1')] = True
            return changed
        with patch('task_flow.planner.apply_activity', side_effect=illegal_step):
            candidate = SearchSession(p).run()['candidates'][0]
        b = verify_candidate(c, r, candidate)
        return {'false_goal': a['verification_status'], 'bypassed_precondition': b['verification_status']}
    add(17, '主动注入生成器语义故障', ['R06', 'R11'], base,
        {'false_goal': 'rejected', 'bypassed_precondition': 'rejected'}, injection)

    def evidence(data):
        c, r = data['catalog'], data['request']; v = verify_sequence(c, r, seq(c, r, ['collect', 'approve', 'publish_standard']))
        required = {'instance', 'before_state', 'after_state', 'preconditions', 'effects', 'committed', 'source_refs', 'state_delta'}
        return {'status': v['verification_status'], 'steps': len(v['trace']),
                'complete_records': all(required <= row.keys() and row['preconditions']['evidence'] for row in v['trace']),
                'final_goal_recorded': v['final_checks']['goal']['value'], 'derived_rule_recorded': bool(v['trace'][-1]['derived_checks'])}
    add(18, '逐步证据及规则来源完整性', ['R06'], base,
        {'status': 'passed', 'steps': 3, 'complete_records': True, 'final_goal_recorded': True, 'derived_rule_recorded': True}, evidence)

    def external(data):
        c, r = data['catalog'], data['request']; generated = SearchSession(prepare_problem(c, r)).run()
        checked = verify_search_report(c, r, generated)
        return {'logic': checked['verification_status'], 'external': checked['external_status'],
                'search_claims_verified': checked['search_claims_verified'],
                'all_capabilities_unmapped': all(cap['mapping_status'] == 'unmapped' for cap in c['capabilities'])}
    add(19, '逻辑通过不冒充外部落实', ['R09', 'R10', 'R12'], base,
        {'logic': 'passed', 'external': 'not_run', 'search_claims_verified': False, 'all_capabilities_unmapped': True}, external)

    def permutation(data):
        c, r = deepcopy(data['catalog']), deepcopy(data['request']); r['search']['mode'] = 'enumerate_bounded'
        original = SearchSession(prepare_problem(c, r)).run()
        for field in ('facts', 'activities', 'derived_rules'): c[field].reverse()
        rebind(c, r); changed = SearchSession(prepare_problem(c, r)).run()
        return {'same_sequences': stepset(original) == stepset(changed),
                'same_order': [sequence_key(v['sequence']['steps']) for v in original['candidates']] == [sequence_key(v['sequence']['steps']) for v in changed['candidates']],
                'provenance_changed': original['catalog_sha256'] != changed['catalog_sha256']}
    add(20, '等价输入排列与来源身份区别', ['R07', 'R11'], base,
        {'same_sequences': True, 'same_order': True, 'provenance_changed': True}, permutation)

    def completion(data):
        c, r = deepcopy(data['catalog']), deepcopy(data['request']); result = {}
        for label, value in [('unknown', None), ('true', True), ('false', False)]:
            r['static_facts'][0]['value'] = value
            result[label] = SearchSession(prepare_problem(c, r)).run()['conclusion']
        return result
    add(21, '未知信息补全与无解结论边界', ['R03', 'R08'], base,
        {'unknown': 'blocked_unknown', 'true': 'candidates_available', 'false': 'bounded_no_solution'}, completion)

    models = mixed_models(model_count)
    reference = []
    for row in models:
        keys = exhaustive_sequences(row['catalog'], row['request'])
        reference.append({'case': row['request']['request_id'], 'count': len(keys), 'sequence_set_sha256': digest(sorted(keys)),
                          'sequences': json.loads(json.dumps(sorted(keys)))})
    def differential(data):
        rows = []
        for row in data['models']:
            c, r = row['catalog'], row['request']; p = prepare_problem(c, r)
            produced = SearchSession(p).run(); verified = set()
            raw_instances = [{'activity_id': a['id'], 'activity_version': a['version'], 'parameters': {}} for a in c['activities']]
            for length in range(r['search']['max_length'] + 1):
                for steps in product(raw_instances, repeat=length):
                    s = {'format_version': 'task-flow-1.0', 'catalog_sha256': p.catalog_sha256, 'request_sha256': p.request_sha256, 'steps': list(steps)}
                    result = verify_sequence(c, r, s)
                    if result['verification_status'] == 'passed':
                        verified.add(sequence_key(steps))
                        base_state = initial_state(p)
                        for step in steps: base_state = apply_activity(p, base_state, step)
                        if state_records(p, base_state) != result['final_state']:
                            raise AssertionError('generation/replay final-state mismatch')
            keys = stepset(produced)
            if produced['search_status'] != 'exhausted' or len(keys) != len(produced['candidates']) or keys != verified:
                raise AssertionError('generator/replay sequence-set mismatch')
            rows.append({'case': r['request_id'], 'count': len(keys), 'sequence_set_sha256': digest(sorted(keys)),
                         'sequences': json.loads(json.dumps(sorted(keys)))})
        return {'models': rows}
    add(22, '混合布尔整数模型三方精确对照', ['R03', 'R07', 'R11'], {'seed': SEED, 'models': models}, {'models': reference}, differential)

    def baseline(data):
        command = [sys.executable, '-B', '-m', 'unittest'] + ['tests.' + name for name in data['modules']]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        text = result.stdout + result.stderr
        match = re.search(r'Ran (\d+) tests', text)
        return {'returncode': result.returncode, 'tests': int(match[1]) if match else None, 'skipped': bool(re.search(r'skipped=\d+', text))}, {'log': text}
    add(23, '既有相关回归基线', ['R01', 'R06', 'R11'], {'modules': BASELINE_MODULES},
        {'returncode': 0, 'tests': 215, 'skipped': False}, baseline)
    return cases


def source_files():
    paths = list((ROOT / 'task_flow').glob('*.py')) + [ROOT / 'task_flow/schema.json', Path(__file__).resolve(), ROOT / 'tests/task_flow_oracle.py']
    paths += list((ROOT / 'task_flow/examples').rglob('*.json'))
    paths += [ROOT / 'tests' / (name + '.py') for name in BASELINE_MODULES]
    paths += [ROOT / 'tests/test_task_flow_research_audit.py']
    paths += [ROOT / '扩展链任务书.txt']
    paths += [p for p in ROOT.glob('requirements*') if p.suffix in ('.txt', '.lock')]
    return sorted(set(paths))


def source_bytes(path):
    return path.read_bytes().replace(b'\r\n', b'\n')


def _validate_model_count(count):
    if type(count) is not int or not 1 <= count <= 128:
        raise ValueError('audit model count must be an exact integer in [1, 128]; empty studies are invalid')


def _captured(function, operation, observations):
    """Record effective inputs at the audit boundary, without changing semantics."""
    def invoke(catalog, request, *args, **kwargs):
        model = {'catalog': deepcopy(catalog), 'request': deepcopy(request)}
        model_id = digest(model)
        observations['models'][model_id] = model
        event = {'operation': operation, 'model_id': model_id, 'options': deepcopy(kwargs)}
        if args:
            subject = deepcopy(args[0])
            subject_id = digest(subject)
            observations['subjects'][subject_id] = subject
            event['subject_id'] = subject_id
        observations['calls'].append(event)
        try:
            result = function(catalog, request, *args, **kwargs)
        except Exception as error:
            event['outcome'] = {'exception': type(error).__name__, 'code': getattr(error, 'code', None), 'reason': str(error)}
            raise
        if isinstance(result, dict):
            event['outcome'] = {k: deepcopy(result[k]) for k in ('verification_status', 'failure', 'counts') if k in result}
            if 'final_state' in result:
                event['outcome']['final_state_sha256'] = digest(result['final_state'])
        else:
            event['outcome'] = {'ground_facts': len(result.fact_specs), 'ground_instances': len(result.instances)}
        return result
    return invoke


def run(output, *, model_count=32):
    _validate_model_count(model_count)
    if output.exists():
        raise ValueError('output must not exist; historical evidence is never overwritten')
    output.mkdir(parents=True)
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    except subprocess.SubprocessError:
        commit = 'unavailable'
    sources = {p.relative_to(ROOT).as_posix(): hashlib.sha256(source_bytes(p)).hexdigest() for p in source_files()}
    for name in sources:
        path = output / 'sources' / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source_bytes(ROOT / name))
    cases = build_cases(model_count)
    (output / 'cases').mkdir()
    rows = []
    for case in cases:
        start = time.monotonic()
        details = {}
        observed = {'models': {}, 'subjects': {}, 'calls': [], 'search_runs': []}
        try:
            replacements = {name: _captured(globals()[name], name, observed) for name in
                            ('prepare_problem', 'verify_sequence', 'verify_candidate', 'verify_search_report')}
            original_run = SearchSession.run
            def capture_run(session, *args, **kwargs):
                report = original_run(session, *args, **kwargs)
                observed['search_runs'].append({'options': deepcopy(kwargs), 'report': deepcopy(report)})
                return report
            with patch.dict(globals(), replacements), patch.object(SearchSession, 'run', capture_run):
                actual = case.probe(deepcopy(case.inputs))
            if isinstance(actual, tuple): actual, details = actual
            passed = digest(actual) == digest(case.expected)
        except Exception as error:
            actual = {'exception': type(error).__name__, 'reason': str(error)}
            passed = False
        record = {'id': case.id, 'title': case.title, 'requirements': case.requirements, 'inputs': case.inputs,
                  'inputs_sha256': digest(case.inputs), 'expected': case.expected, 'actual': actual,
                  'effective_inputs_and_calls': observed,
                  'criterion': 'exact typed JSON equality; reference sets are computed independently before the production probe',
                  'passed': passed, 'elapsed_ms': round((time.monotonic() - start) * 1000, 3)}
        if details.get('log') is not None:
            (output / 'baseline-tests.log').write_text(details['log'], encoding='utf-8', newline='\n')
        write_json(output / 'cases' / (case.id + '.json'), record)
        rows.append({k: record[k] for k in ('id', 'title', 'requirements', 'passed', 'elapsed_ms')})
        print(case.id, 'PASS' if passed else 'FAIL', case.title, flush=True)
    unchanged = all(hashlib.sha256(source_bytes(ROOT / name)).hexdigest() == sha for name, sha in sources.items())
    report = {'audit_version': 'task-flow-research-audit-1', 'review_date': '2026-11-27', 'baseline_commit': commit,
              'python': sys.version, 'platform': platform.platform(), 'seed': SEED, 'mixed_models': model_count,
              'cases': rows, 'passed': sum(r['passed'] for r in rows), 'failed': sum(not r['passed'] for r in rows),
              'sources_unchanged_during_audit': unchanged, 'source_sha256': sources,
              'hash_policy': 'source text: CRLF to LF only; artifacts: exact bytes',
              'scope': 'model-relative correctness and evidence for phases 1-4; not external feasibility or system integration',
              'unimplemented_requirements': ['R09 scoped external feedback', 'R10 complete legacy capability/resource adapter', 'R12 complete external end-to-end integration']}
    report['dependencies'] = {}
    for name in ('jsonschema', 'rdflib', 'owlready2', 'numpy', 'scipy'):
        try:
            report['dependencies'][name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            report['dependencies'][name] = 'not_installed'
    report['status'] = 'passed_with_scope_limits' if report['failed'] == 0 and unchanged else 'failed'
    write_json(output / 'report.json', report)
    lines = ['# 前四阶段专项验收实际结果', '', '每项完整输入、预期、实际和通过判据见cases/Cxx.json。', '',
             '| 编号 | 检查 | 需求 | 结果 |', '| --- | --- | --- | --- |']
    lines += [f'| {r["id"]} | {r["title"]} | {", ".join(r["requirements"])} | {"通过" if r["passed"] else "失败"} |' for r in rows]
    lines += ['', '结论只适用于声明模型与本次测试边界。R09、R10的完整适配及R12仍未验收。', '']
    (output / 'acceptance.md').write_text('\n'.join(lines), encoding='utf-8', newline='\n')
    manifest = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(output.rglob('*')) if p.is_file()}
    write_json(output / 'manifest.json', manifest)
    return report


def verify(output):
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    actual_paths = {p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file() and p != output / 'manifest.json'}
    if actual_paths != set(manifest):
        raise ValueError('artifact inventory differs')
    for name, sha in manifest.items():
        path = (output / name).resolve()
        if not path.is_relative_to(output.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError('artifact digest mismatch: ' + name)
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    for name, sha in report['source_sha256'].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or hashlib.sha256(source_bytes(path)).hexdigest() != sha:
            raise ValueError('current source differs from audited source: ' + name)
    return {'status': report['status'], 'artifacts_checked': len(manifest), 'source_files_checked': len(report['source_sha256'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'verify'])
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = run(args.output_dir.resolve()) if args.command == 'run' else verify(args.output_dir.resolve())
        print(json.dumps({k: result[k] for k in ('status', 'passed', 'failed', 'artifacts_checked', 'source_files_checked') if k in result}, ensure_ascii=False))
        return 0 if result['status'] == 'passed_with_scope_limits' else 1
    except (ValueError, OSError) as error:
        print(json.dumps({'status': 'invalid', 'reason': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
