"""Explicit full-design XLSX contract; no implicit business rules or defaults."""
import math
import re

from excel_entry.importer import EntryError, read_xlsx, records
from rule_review.core import text, typed
from .engine import validate_document


HEADERS = {
    '设计设置': ['设置', '值'],
    '任务': ['任务编号', '目标编号', '开始时间秒', '来源', '阻塞疑点'],
    '法器': ['法器编号', '角色', '可用', '耗时秒', '精度0至1', '使用成本', '成本单位', '容量', '消耗品', '库存', '冷却秒', '来源', '阻塞疑点'],
    '情境': ['任务编号', '法器编号', '来源', '阻塞疑点'],
    '属性': ['对象类型', '对象编号', '情境法器编号', '字段', '类型', '原值', '单位'],
    '规则': ['规则类型', '规则编号', '角色', '能力名称', '无条件', '来源', '阻塞疑点'],
    '规则条件': ['规则类型', '规则编号', '字段', '运算符', '比较方式', '类型', '比较值', '单位', '引用字段'],
    '协同关系': ['起点法器', '终点法器', '传讯', '统筹', '距离', '距离单位', '最大距离', '最大距离单位', '来源', '阻塞疑点'],
    '任务依赖': ['前置任务', '后置任务', '间隔秒'],
    '资源组': ['资源组编号', '容量', '法器编号列表'],
    '填写指南': ['项目', '说明'],
}
SETTINGS = ['范围说明', '时间权重', '精度权重', '成本权重', '同值指标评分', '成本单位', '链组合上限', '网络组合上限']
ROLES = {'探知': 'insight', '协调': 'coordination', '具现': 'manifestation'}
RULES = {'任务': 'task_rules', '法器': 'artifact_rules'}
GUIDE = [
    ['用途', '完整链网设计输入。示例所有数据和规则均为虚构工程配置，不是文章案例复现依据。'],
    ['填写顺序', '设计设置、任务、法器、情境、属性、规则、规则条件、协同关系。依赖和资源组可留空。'],
    ['布尔与列表', '布尔填文本true或false。来源、阻塞疑点和列表使用英文分号分隔。不要使用公式。'],
    ['角色', '探知、协调、具现。能力名称使用本体中明确登记的名称；未映射时保留报告并停止规范RDF导出。'],
    ['属性', '对象类型填任务、法器或情境。情境的对象编号填任务编号，并填写情境法器编号；其他类型此列留空。'],
    ['属性值', '类型填文本、数值、布尔或列表。数值必须填写单位，无量纲填1。原值空白表示未知，不表示0。'],
    ['条件', '同一规则多行条件为且关系。比较方式填常量或字段；字段比较仅填写引用字段，常量填写类型、比较值和单位。'],
    ['条件字段', 'task.字段、artifact.字段、context.字段。运算符为eq、contains、lt、le、gt、ge。任务规则仅能引用task字段。'],
    ['无条件', '规则无条件列填true时不允许条件行；填false时至少提供一条条件。来源必填，待确认事项登记阻塞疑点。'],
    ['指标', '耗时、精度、成本和成本单位全部空白时表示指标未知，部分填写会报错。其他数字不能为空。精度填写0至1。'],
    ['关系与时间', '协同关系为有向边，距离严格小于最大距离。开始时间固定；容量、库存、冷却、依赖和资源组参与网络约束。'],
    ['输出', '上传后显示候选链、选中网络和阻塞原因。完整且规范能力映射就绪时才导出RDF；执行静态本体检查。'],
]


def number(value, where, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (integer and int(value) != value):
        raise EntryError(f'{where}：请填写有限数字' + ('整数' if integer else '') + '，空白不当作0')
    return int(value) if integer else value


def boolean(value, where):
    value = text(value, where).lower()
    if value not in ('true', 'false'):
        raise EntryError(f'{where}：请填写文本true或false')
    return value == 'true'


def refs(value, where):
    if value is None:
        return []
    parts = [s.strip() for s in text(value, where).split(';')]
    if any(not s for s in parts) or len(set(parts)) != len(parts):
        raise EntryError(f'{where}：列表含空项或重复项')
    return parts


def evidence(source, issues, where):
    return {'source_refs': refs(text(source, where + '来源'), where),
            'blocking_issue_ids': refs(issues, where)}


def value_of(kind, value, unit, where):
    parsed = typed(kind, value, unit, where, allow_missing=True)
    if parsed['value'] is None:
        return None
    return {'value': parsed['value'], 'unit': parsed['unit']} if parsed['type'] == '数值' else parsed['value']


def convert(sheets):
    if set(sheets) != set(HEADERS):
        raise EntryError('请使用完整链网设计模板，保留全部工作表及名称')
    rows = {name: list(records(sheets, name, headers)) for name, headers in HEADERS.items()}
    settings = {}
    for row, (key, value) in rows['设计设置']:
        if key not in SETTINGS or key in settings:
            raise EntryError(f'设计设置第{row}行：未知或重复设置')
        settings[key] = value
    if set(settings) != set(SETTINGS):
        raise EntryError('设计设置缺项')
    doc = {'format_version': '1.0', 'scope': text(settings['范围说明'], '范围说明'),
           'weights': {key: number(settings[label], label) for key, label in zip(('time', 'accuracy', 'cost'), SETTINGS[1:4])},
           'policies': {'equal_metric_score': number(settings['同值指标评分'], '同值指标评分'),
                        'cost_unit': text(settings['成本单位'], '成本单位'),
                        'max_chain_combinations': number(settings['链组合上限'], '链组合上限', True),
                        'max_network_combinations': number(settings['网络组合上限'], '网络组合上限', True)},
           **{k: [] for k in ('tasks', 'artifacts', 'contexts', 'task_rules', 'artifact_rules', 'links', 'dependencies', 'resource_groups')}}
    owners = {}
    def add_owner(kind, key, obj):
        if (kind, key) in owners:
            raise EntryError(f'{kind}编号重复：{key}')
        owners[kind, key] = obj
        return obj
    for row, (identifier, target, start, source, issues) in rows['任务']:
        where = f'任务第{row}行'
        identifier = text(identifier, where)
        doc['tasks'].append(add_owner('任务', identifier, dict(id=identifier, target=text(target, where),
            start_s=number(start, where), attributes={}, **evidence(source, issues, where))))
    for row, cells in rows['法器']:
        identifier, role, available, duration, accuracy, cost, unit, capacity, consumable, inventory, cooldown, source, issues = cells
        where = f'法器第{row}行'
        identifier = text(identifier, where)
        if role not in ROLES:
            raise EntryError(f'{where}：角色应为探知、协调或具现')
        metrics = None if all(v is None for v in (duration, accuracy, cost, unit)) else dict(
            duration_s=number(duration, where), accuracy=number(accuracy, where),
            cost=dict(value=number(cost, where), unit=text(unit, where)))
        doc['artifacts'].append(add_owner('法器', identifier, dict(id=identifier, role=ROLES[role],
            available=boolean(available, where), attributes={}, metrics=metrics, capacity=number(capacity, where, True),
            consumable=boolean(consumable, where), inventory=number(inventory, where, True),
            cooldown_s=number(cooldown, where), **evidence(source, issues, where))))
    for row, (task, artifact, source, issues) in rows['情境']:
        where = f'情境第{row}行'
        task, artifact = text(task, where), text(artifact, where)
        doc['contexts'].append(add_owner('情境', (task, artifact), dict(task=task, artifact=artifact,
            attributes={}, **evidence(source, issues, where))))
    for row, (kind, identifier, artifact, field, typ, value, unit) in rows['属性']:
        where = f'属性第{row}行'
        identifier, field = text(identifier, where), text(field, where)
        if kind != '情境' and artifact is not None:
            raise EntryError(f'{where}：仅情境属性填写情境法器编号')
        key = (identifier, text(artifact, where)) if kind == '情境' else identifier
        owner = owners.get((kind, key))
        if owner is None or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', field) or field in owner['attributes']:
            raise EntryError(f'{where}：对象不存在、字段无效或属性重复')
        owner['attributes'][field] = value_of(typ, value, unit, where)
    rules = {}
    for row, (kind, identifier, role, capability, unconditional, source, issues) in rows['规则']:
        where = f'规则第{row}行'
        identifier = text(identifier, where)
        if kind not in RULES or role not in ROLES or (kind, identifier) in rules:
            raise EntryError(f'{where}：规则类型、角色无效或编号重复')
        rule = dict(id=identifier, role=ROLES[role], capability=text(capability, where), when=[], **evidence(source, issues, where))
        rules[kind, identifier] = (rule, boolean(unconditional, where))
        doc[RULES[kind]].append(rule)
    for row, (kind, identifier, field, op, mode, typ, value, unit, ref) in rows['规则条件']:
        where = f'规则条件第{row}行'
        pair = rules.get((kind, identifier))
        if pair is None:
            raise EntryError(f'{where}：规则不存在')
        condition = dict(field=text(field, where), op=text(op, where))
        if mode == '字段' and all(v is None for v in (typ, value, unit)):
            condition['ref'] = text(ref, where)
        elif mode == '常量' and ref is None:
            condition['value'] = value_of(typ, value, unit, where)
        else:
            raise EntryError(f'{where}：比较方式只能为常量或字段，且不得同时填写两种比较值')
        pair[0]['when'].append(condition)
    for rule, unconditional in rules.values():
        if unconditional == bool(rule['when']):
            raise EntryError(f"规则{rule['id']}：无条件标记与条件行不一致")
    for row, (start, end, communicates, controls, distance, unit, maximum, maxunit, source, issues) in rows['协同关系']:
        where = f'协同关系第{row}行'
        doc['links'].append({'from': text(start, where), 'to': text(end, where),
            'communicates': boolean(communicates, where), 'controls': boolean(controls, where),
            'distance': {'value': number(distance, where), 'unit': text(unit, where)},
            'max_distance': {'value': number(maximum, where), 'unit': text(maxunit, where)}, **evidence(source, issues, where)})
    for row, (before, after, gap) in rows['任务依赖']:
        doc['dependencies'].append(dict(before=text(before, '前置任务'), after=text(after, '后置任务'), gap_s=number(gap, '间隔秒')))
    for row, (identifier, capacity, artifacts) in rows['资源组']:
        doc['resource_groups'].append(dict(id=text(identifier, '资源组'), capacity=number(capacity, '资源组容量', True), artifacts=refs(artifacts, '资源组法器')))
    validate_document(doc)
    return doc


def read_design(data):
    return convert(read_xlsx(data))
