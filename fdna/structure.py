"""Chapter 3 entity layers, explicit interpretation and structural correspondence."""
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .model import ModelError, validate_model

ROLE_TYPES = {'insight': ('S', 'R'), 'coordination': ('D', 'D'), 'manifestation': ('F', 'E')}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def members(group):
    for member in group:
        if isinstance(member, list):
            yield from members(member)
        else:
            yield member


def validate_structure(model, structure):
    validate_model(model)
    schema = json.loads(Path(__file__).with_name('structure_schema.json').read_text(encoding='utf-8'))
    errors = list(Draft202012Validator(schema).iter_errors(structure))
    if errors:
        raise ModelError('第三节结构不符合数据契约：'+errors[0].message)
    if structure['case_id'] != model['id'] or structure['case_sha256'] != digest(model):
        raise ModelError('结构登记的案例版本不一致，请重新核对实体与映射')
    nodes = {n['id']: n for n in model['nodes']}
    chains = {c['id']: c for c in model['chains']}
    subjects = {s['id'] for s in model['subjects']}
    if set(structure['system']['chains']) != set(chains):
        raise ModelError('奥术网须包含案例中的全部法术链')
    refs = {f"{s['id']}:{loc}" for s in model['sources'] for loc in s['locators']}
    source_ids = {s['id'] for s in model['sources']}
    for locator in structure['source_locators']:
        ref = locator['source']+':'+locator['id']
        if locator['source'] not in source_ids or ':' in locator['id'] or ref in refs:
            raise ModelError('第三节来源位置不存在、重复或与既有位置冲突')
        refs.add(ref)

    def evidence(value):
        if isinstance(value, dict):
            if 'source_refs' in value and not set(value['source_refs']) <= refs:
                raise ModelError('结构映射引用未登记来源位置')
            for item in value.values():
                evidence(item)
        elif isinstance(value, list):
            for item in value:
                evidence(item)
    evidence(structure)

    def index(collection):
        items = structure[collection]
        result = {item['id']: item for item in items}
        if len(result) != len(items):
            raise ModelError(collection+'存在重复标识')
        return result
    tasks, activities, functions = (index(name) for name in ('tasks', 'activities', 'functions'))
    dependencies = index('function_dependencies')
    layers = [set(nodes), set(chains), subjects, {t['id'] for t in model['targets']},
              {structure['system']['id']}, set(tasks), set(activities), set(functions), set(dependencies)]
    if len(set().union(*layers)) != sum(len(layer) for layer in layers):
        raise ModelError('体系、链、任务、活动、能力、主体、功能及对应边须使用不同标识')
    by_capability = {}
    for function in functions.values():
        cap = function['capability']
        if cap not in nodes or cap in by_capability:
            raise ModelError('能力与功能须一一对应，不能漏配或重配')
        if function['subject'] != nodes[cap]['subject'] or function['type'] != ROLE_TYPES[nodes[cap]['role']][0]:
            raise ModelError('功能所属主体或类别与对应能力不一致')
        by_capability[cap] = function
    if set(by_capability) != set(nodes):
        raise ModelError('每个能力节点须对应一个独立功能实例')
    represented, used_activities = set(), set()
    for task in tasks.values():
        chain_id = task['chain']
        if chain_id not in chains or chain_id in represented:
            raise ModelError('任务与法术链须一一对应')
        represented.add(chain_id)
        chain = chains[chain_id]
        if task['target'] != chain['target'] or len(task['activity_order']) != len(chain['members']):
            raise ModelError('任务目标或活动槽位数与表4最外层成员不一致')
        for activity_id, original in zip(task['activity_order'], chain['members']):
            if activity_id not in activities or activity_id in used_activities:
                raise ModelError('活动不存在或被重复分配到多个槽位')
            used_activities.add(activity_id)
            activity = activities[activity_id]
            expected = original if isinstance(original, list) else [original]
            if activity['task'] != task['id'] or activity['capability_group'] != expected:
                raise ModelError('活动所属任务、能力分组或顺序与登记解释不一致')
            caps = list(members(expected))
            types = {ROLE_TYPES[nodes[c]['role']][1] for c in caps}
            expected_type = next(iter(types)) if len(types) == 1 else None
            if activity['type'] != expected_type:
                raise ModelError('活动类别须由能力类别对应；混合类别时保持未知')
            if set(activity['performers']) != {nodes[c]['subject'] for c in caps}:
                raise ModelError('活动执行主体须与支撑能力所属主体一致')
            if set(activity['functions']) != {by_capability[c]['id'] for c in caps}:
                raise ModelError('活动支撑功能与所需能力不对应')
    if represented != set(chains) or used_activities != set(activities):
        raise ModelError('存在未被任务覆盖的法术链或游离活动')
    expected_pairs = {(e['feeder'], e['receiver']) for e in model['edges']}
    actual_pairs = set()
    for edge in dependencies.values():
        pair = edge['capability_feeder'], edge['capability_receiver']
        if pair not in expected_pairs or pair in actual_pairs:
            raise ModelError('功能依赖的能力层对应边不存在或重复')
        actual_pairs.add(pair)
        if (edge['feeder'] != by_capability[pair[0]]['id'] or
                edge['receiver'] != by_capability[pair[1]]['id']):
            raise ModelError('功能依赖方向或端点与能力层对应不一致')
    if actual_pairs != expected_pairs:
        raise ModelError('功能层须逐一登记能力依赖的结构对应')


def inspect_structure(model, structure):
    validate_structure(model, structure)
    functions = {f['capability']: f for f in structure['functions']}
    tasks = {t['id']: t for t in structure['tasks']}
    nodes = {n['id']: n for n in model['nodes']}
    rows, order_relations = [], []
    activities = {a['id']: a for a in structure['activities']}
    for task in structure['tasks']:
        for first, second in zip(task['activity_order'], task['activity_order'][1:]):
            order_relations.append({'task': task['id'], 'first': first, 'second': second,
                                    'meaning': 'interpreted_outer_order', 'numeric_dependency': False})
        for aid in task['activity_order']:
            activity = activities[aid]
            for cap in members(activity['capability_group']):
                rows.append({'system': structure['system']['id'], 'chain': task['chain'], 'task': task['id'],
                             'activity': aid, 'activity_type': activity['type'], 'capability': cap,
                             'subject': nodes[cap]['subject'], 'function': functions[cap]['id'],
                             'function_type': functions[cap]['type'], 'activity_evidence': activity['evidence']['kind']})
    # Six node layers of section 3.2; Task is an explicit alias-level entity of KC.
    graph_nodes = ([{'id': structure['system']['id'], 'layer': 'NKW'}] +
                   [{'id': c['id'], 'layer': 'NKC'} for c in model['chains']] +
                   [{'id': a['id'], 'layer': 'NAct', 'type': a['type']} for a in structure['activities']] +
                   [{'id': n['id'], 'layer': 'NC', 'type': ROLE_TYPES[n['role']][0]} for n in model['nodes']] +
                   [{'id': s['id'], 'layer': 'NU'} for s in model['subjects']] +
                   [{'id': f['id'], 'layer': 'NF', 'type': f['type']} for f in structure['functions']])
    links = []
    for task in tasks.values():
        links.append({'source': structure['system']['id'], 'target': task['chain'], 'kind': 'contains_chain'})
        links.extend({'source': task['chain'], 'target': aid, 'kind': 'interpreted_activity'} for aid in task['activity_order'])
    for row in rows:
        links.extend([{'source': row['activity'], 'target': row['capability'], 'kind': 'requires_capability'},
                      {'source': row['capability'], 'target': row['function'], 'kind': 'corresponds_to_function'},
                      {'source': row['function'], 'target': row['activity'], 'kind': 'supports_interpreted_activity'}])
    for function in functions.values():
        links.append({'source': function['subject'], 'target': function['id'], 'kind': 'owns_function'})
    for activity in structure['activities']:
        links.extend({'source': subject, 'target': activity['id'], 'kind': 'performs_interpreted_activity'}
                     for subject in activity['performers'])
    links.extend({'source': p['first'], 'target': p['second'], 'kind': 'interpreted_activity_order',
                  'numeric_dependency': False} for p in order_relations)
    links.extend({'source': e['feeder'], 'target': e['receiver'], 'kind': 'capability_dependency',
                  'alpha': e['alpha'], 'beta': e['beta']} for e in model['edges'])
    links.extend({'source': e['feeder'], 'target': e['receiver'], 'kind': 'function_dependency_correspondence',
                  'parameters': None, 'interaction_mechanism': None} for e in structure['function_dependencies'])
    return {'format_version': '1.0', 'status': 'valid', 'case': model['id'],
            'scope': '第三节六层实体与规则映射；活动槽位为表4最外层分组的显式解释。'
                     '功能依赖仅作结构对应，未推定其机理或数值参数。',
            'input_sha256': digest({'case': model, 'structure': structure}),
            'counts': {'systems': 1, 'chains': len(model['chains']), 'tasks': len(tasks),
                       'activity_slots': len(activities), 'capabilities': len(nodes),
                       'subjects': len(model['subjects']), 'functions': len(functions),
                       'capability_dependencies': len(model['edges']),
                       'function_dependency_correspondences': len(structure['function_dependencies'])},
            'rows': rows, 'graph': {'nodes': graph_nodes, 'links': links}, 'activity_order_relations': order_relations,
            'type_catalog': {'NAct': {'R': '探知', 'D': '决策', 'E': '施法'},
                             'NC': {'S': '灵视探知', 'D': '协调统筹', 'F': '魔力具现'},
                             'NF': {'S': '灵视探知', 'D': '协调统筹', 'F': '魔力具现'}},
            'structure_snapshot': deepcopy(structure), 'source_snapshot': deepcopy(model['sources']),
            'structural_contract_ready': True, 'source_confirmed_activity_partition': False,
            'dependency_identification_ready': False, 'case_reproduction_ready': False,
            'pending': ['活动槽位的实际划分、开始结束与组内时序仍需活动记录确认',
                        '功能实例依据规则6构造，未确认具体法器或术式的内部模块划分',
                        '功能层物资流与信息流机理未知，能力层α、β不作为功能层参数',
                        '活动顺序与依赖结构分别保存，尚未由闭合记录识别能力依赖']}


def structure_csv(report):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(report['rows'][0]))
    writer.writeheader()
    writer.writerows(report['rows'])
    return stream.getvalue()


def render_structure(report):
    lines = ['# 第三节实体与映射核对', '', report['scope'], '',
             '层次：奥术网、法术链、活动、能力、施法主体、魔法功能。任务单独登记并与法术链一一对应。', '',
             '| 奥术网 | 法术链 | 任务 | 活动槽位 | 类别 | 能力 | 主体 | 功能 |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for row in report['rows']:
        lines.append('| '+' | '.join(str(row[k]) for k in ('system', 'chain', 'task', 'activity',
                                                         'activity_type', 'capability', 'subject', 'function'))+' |')
    lines += ['', '表中活动均为解释性槽位；原始能力分组完整保存在结构快照中。'
              'FN前缀为构造的功能实例标识，F7等仍是能力节点，二者含义不同。', '',
              f"能力依赖{report['counts']['capability_dependencies']}条，功能层结构对应"
              f"{report['counts']['function_dependency_correspondences']}条；未复制数值参数。", '',
              '仍待核实：', '']
    lines.extend('- '+gap for gap in report['pending'])
    return '\n'.join(lines)+'\n'
