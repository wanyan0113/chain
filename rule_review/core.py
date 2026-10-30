"""Read a fixed Excel rule ledger and explain generic conditional conclusions."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import math

from excel_entry.importer import EntryError, read_xlsx, records

HEADERS = {
    '任务': ['任务编号', '说明'],
    '任务事实': ['任务编号', '字段', '类型', '原值', '单位', '来源定位'],
    '规则台账': ['规则编号', '需求结论', '依据类别', '来源文件', '原文位置', '确认状态', '说明'],
    '规则条件': ['规则编号', '字段', '运算符', '类型', '比较值', '单位'],
    '对照用例': ['用例编号', '任务编号', '规则编号', '预期规则状态', '预期需求结论'],
}
KINDS = ('文本', '数值', '布尔', '列表')
OPERATORS = ('eq', 'contains', 'lt', 'le', 'gt', 'ge')


def text(value, where, optional=False):
    if optional and value is None:
        return ''
    if not isinstance(value, str) or not value.strip():
        raise EntryError(f'{where}：需要非空文本')
    return value.strip()


def typed(kind, value, unit, where, allow_missing=False):
    kind = text(kind, where)
    unit = text(unit, where, optional=True)
    if kind not in KINDS:
        raise EntryError(f'{where}：类型只能为{KINDS}')
    if value is None and allow_missing:
        return {'type': kind, 'value': None, 'unit': unit}
    if kind == '数值':
        if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
            raise EntryError(f'{where}：数值必须填写有限数字，空白不当作0')
        if not unit:
            raise EntryError(f'{where}：数值必须填写单位；无量纲请填1')
    else:
        if unit:
            raise EntryError(f'{where}：非数值类型不填写单位')
        value = text(value, where)
        if kind == '布尔':
            if value.lower() not in ('true', 'false'):
                raise EntryError(f'{where}：布尔值请填文本true或false')
            value = value.lower() == 'true'
        if kind == '列表':
            value = list(dict.fromkeys(v.strip() for v in value.replace('；', ';').split(';') if v.strip()))
            if not value:
                raise EntryError(f'{where}：列表不能为空')
    return {'type': kind, 'value': value, 'unit': unit}


def read_document(data):
    sheets = read_xlsx(data)
    if set(sheets) != set(HEADERS):
        raise EntryError('请使用规则验证模板，保留五张表及其名称')
    if sheets['对照用例'].get(1, {}) == {i + 1: h for i, h in enumerate(HEADERS['对照用例'][:-1])}:
        raise EntryError('对照用例模板已更新：请重新下载模板，独立填写新增的“预期需求结论”列；无结论时填“无结论”')
    tasks, rules, cases = {}, {}, []
    for row, (identifier, description) in records(sheets, '任务', HEADERS['任务']):
        identifier = text(identifier, f'任务第{row}行')
        if identifier in tasks:
            raise EntryError(f'任务编号重复：{identifier}')
        tasks[identifier] = {'id': identifier, 'description': text(description, '任务说明', optional=True), 'facts': {}}
    if not tasks:
        raise EntryError('任务表至少需要一项任务')
    for row, (task, field, kind, value, unit, source) in records(sheets, '任务事实', HEADERS['任务事实']):
        task, field = text(task, '任务编号'), text(field, '字段')
        if task not in tasks or field in tasks[task]['facts']:
            raise EntryError(f'任务事实第{row}行：任务不存在或字段重复')
        fact = typed(kind, value, unit, f'任务事实第{row}行', allow_missing=True)
        fact['source'] = text(source, f'任务事实第{row}行来源')
        tasks[task]['facts'][field] = fact
    for row, values in records(sheets, '规则台账', HEADERS['规则台账']):
        identifier, conclusion, basis, source, location, status, note = values
        identifier = text(identifier, '规则编号')
        if identifier in rules:
            raise EntryError(f'规则编号重复：{identifier}')
        basis, status = text(basis, '依据类别'), text(status, '确认状态')
        if basis not in ('文献依据', '工程约定', '技术测试') or status not in ('已确认', '待确认', '已停用'):
            raise EntryError(f'规则台账第{row}行：依据类别或确认状态无效')
        rules[identifier] = {'id': identifier, 'conclusion': text(conclusion, '需求结论'),
            'basis': basis, 'source': text(source, '来源文件'), 'location': text(location, '原文位置'),
            'status': status, 'note': text(note, '说明', optional=True), 'conditions': []}
    if not rules:
        raise EntryError('规则台账至少需要一条规则')
    for row, (rule, field, op, kind, value, unit) in records(sheets, '规则条件', HEADERS['规则条件']):
        rule, field, op = text(rule, '规则编号'), text(field, '条件字段'), text(op, '运算符')
        if rule not in rules or op not in OPERATORS:
            raise EntryError(f'规则条件第{row}行：规则不存在或运算符无效')
        condition = typed(kind, value, unit, f'规则条件第{row}行', allow_missing=rules[rule]['status'] != '已确认')
        if op in ('lt', 'le', 'gt', 'ge') and condition['type'] != '数值':
            raise EntryError(f'规则条件第{row}行：大小比较需要数值类型')
        if op == 'contains' and condition['type'] != '文本':
            raise EntryError(f'规则条件第{row}行：contains的比较值需要文本类型')
        rules[rule]['conditions'].append({'field': field, 'op': op, 'expected': condition})
    for rule in rules.values():
        if not rule['conditions']:
            raise EntryError(f"规则{rule['id']}没有条件；请登记条件，或将未完整条目保存在文献台账中")
    ids = set()
    for row, (identifier, task, rule, expected, expected_conclusion) in records(sheets, '对照用例', HEADERS['对照用例']):
        identifier, task, rule, expected = [text(v, f'对照用例第{row}行') for v in (identifier, task, rule, expected)]
        if identifier in ids or task not in tasks or rule not in rules or expected not in ('pass', 'fail', 'unknown', 'skipped'):
            raise EntryError(f'对照用例第{row}行：编号重复、引用不存在或预期状态无效')
        ids.add(identifier)
        expected_conclusion = text(expected_conclusion, f'对照用例第{row}行预期需求结论；没有结论请填“无结论”')
        has_expected_conclusion = expected_conclusion != '无结论'
        if has_expected_conclusion != (expected == 'pass'):
            raise EntryError(f'对照用例第{row}行：pass需要具体预期结论，其他状态应填“无结论”')
        cases.append({'id': identifier, 'task': task, 'rule': rule, 'expected': expected,
                      'expected_conclusion': None if expected_conclusion == '无结论' else expected_conclusion})
    return {'tasks': list(tasks.values()), 'rules': list(rules.values()), 'cases': cases}


def check_condition(condition, facts):
    field, expected = condition['field'], condition['expected']
    fact = facts.get(field)
    if not fact or fact['value'] is None:
        return {'status': 'unknown', 'reason': f'缺少事实：{field}', 'missing': field}
    if expected['value'] is None:
        return {'status': 'unknown', 'reason': f'规则比较值待确认：{field}', 'missing': f'规则比较值：{field}'}
    if condition['op'] == 'contains':
        if fact['type'] != '列表':
            return {'status': 'unknown', 'reason': f'{field}需要列表类型'}
        passed = expected['value'] in fact['value']
    else:
        if fact['type'] != expected['type'] or fact['unit'] != expected['unit']:
            return {'status': 'unknown', 'reason': f'{field}类型或单位不一致，不自动换算'}
        left, right = fact['value'], expected['value']
        if fact['type'] == '数值':
            left, right = Decimal(str(left)), Decimal(str(right))
        if condition['op'] == 'eq':
            passed = left == right
        else:
            passed = {'lt': left < right, 'le': left <= right, 'gt': left > right, 'ge': left >= right}[condition['op']]
    return {'status': 'pass' if passed else 'fail', 'reason': '条件成立' if passed else '条件不成立'}


def review(data):
    """One-step, positive conclusions only; never infer negation from no match."""
    document = read_document(data)
    check_count = len(document['tasks']) * sum(len(r['conditions']) for r in document['rules'])
    if check_count > 100_000:
        raise EntryError('本次条件检查超过100000项，请拆分任务或规则；程序不会截断后给出完整结论')
    tasks = []
    for task in document['tasks']:
        results = []
        for rule in document['rules']:
            checks = [dict(condition=deepcopy(c), actual=deepcopy(task['facts'].get(c['field'])),
                           **check_condition(c, task['facts'])) for c in rule['conditions']]
            statuses = {c['status'] for c in checks}
            state = ('skipped' if rule['status'] == '已停用' else 'unknown' if rule['status'] == '待确认'
                     else 'fail' if 'fail' in statuses else 'unknown' if 'unknown' in statuses else 'pass')
            results.append({'rule': deepcopy(rule), 'status': state, 'checks': checks})
        conclusions = list(dict.fromkeys(r['rule']['conclusion'] for r in results if r['status'] == 'pass'))
        unknown = [r for r in results if r['status'] == 'unknown']
        tasks.append({**deepcopy(task), 'status': 'needs_information' if unknown else 'matched' if conclusions else 'no_match',
                      'conclusions': conclusions, 'rules': results,
                      'missing': sorted({c['missing'] for r in unknown for c in r['checks'] if 'missing' in c}),
                      'pending_rules': [r['rule']['id'] for r in unknown if r['rule']['status'] == '待确认']})
    lookup = {(t['id'], r['rule']['id']): r for t in tasks for r in t['rules']}
    cases = []
    for case in document['cases']:
        result = lookup[(case['task'], case['rule'])]
        actual = result['status']
        actual_conclusion = result['rule']['conclusion'] if actual == 'pass' else None
        status_matches = actual == case['expected']
        conclusion_matches = actual_conclusion == case['expected_conclusion']
        cases.append({**case, 'actual': actual, 'actual_conclusion': actual_conclusion,
                      'status_matches': status_matches, 'conclusion_matches': conclusion_matches,
                      'matches': status_matches and conclusion_matches})
    matched = sum(case['matches'] for case in cases)
    return {'status': 'needs_information' if any(t['status'] == 'needs_information' for t in tasks) else 'evaluated',
            'input_sha256': hashlib.sha256(data).hexdigest(), 'tasks': tasks, 'cases': cases,
            'case_validation': {'status': 'not_run' if not cases else 'passed' if matched == len(cases) else 'failed',
                                'total': len(cases), 'matched': matched, 'mismatched': len(cases) - matched},
            'ledger': deepcopy(document['rules']),
            'scope': '给定事实与已登记规则的一步条件验证；未命中不代表不需要；对照用例不补全原始事实。'}
