"""Configurable ordered chains with one executor per step.

This is a class-to-instance adaptation, not a reproduction of the paper's
numerical experiments or an implementation of simultaneous coalition actions.
"""
from collections import Counter
from contextvars import ContextVar
from copy import deepcopy
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from .vocabulary import ROLES

SEQUENCES = ContextVar('arcane_sequences', default=None)
ACTIVITY_ROLES = {'observe': 'insight', 'decide': 'coordination', 'act': 'manifestation', 'assess': 'insight'}


def step_id(task, template, step):
    # JSON tuples avoid collisions when identifiers contain slashes or dashes.
    return json.dumps([task, template, step], ensure_ascii=False, separators=(',', ':'))


def validate_sequences(config, ontology):
    from .engine import DesignError, require_finite
    from .capability_identity import capability_binding
    schema = json.loads(Path(__file__).with_name('sequence_schema.json').read_text(encoding='utf-8'))
    require_finite(config)
    errors = list(Draft202012Validator(schema).iter_errors(config))
    if errors:
        raise DesignError('序列知识无效：' + '; '.join(e.message for e in errors[:3]))
    seen = set()
    for template in config['templates']:
        if template['id'] in seen:
            raise DesignError('序列模板编号重复')
        seen.add(template['id'])
        steps = template['steps']
        if len({step['id'] for step in steps}) != len(steps):
            raise DesignError('同一序列内环节编号重复')
        activities = [s['activity'] for s in steps]
        # Keep the insight-coordination-manifestation backbone without fixing the
        # number of instances in each role. Assessment is an optional legacy
        # extension, never an implicitly inserted or required final step.
        ends_with_act = activities[-1] == 'act' or activities[-2:] == ['act', 'assess']
        if activities[0] != 'observe' or not ends_with_act or 'decide' not in activities:
            raise DesignError('序列须以observe开始、包含decide并以act结束；可显式选配act后的assess，但不强制评估')
        for index, step in enumerate(steps):
            if step['role'] != ACTIVITY_ROLES[step['activity']]:
                raise DesignError('环节活动与角色不一致；评估明确使用insight角色及单独能力/指标')
            if index and 'incoming_relation' not in step:
                raise DesignError('非首环节须明确incoming_relation')
            if not index and 'incoming_relation' in step:
                raise DesignError('首环节不能声明incoming_relation')
            if step.get('incoming_relation') == 'control' and (steps[index - 1]['role'], step['role']) != ('coordination', 'manifestation'):
                raise DesignError('control仅用于协调到具现；其他相邻环节请使用communication')
            for uri in step['capabilities']:
                if capability_binding(step['role'], uri, ontology=ontology)['status'] != 'mapped':
                    raise DesignError('环节能力必须是在当前本体声明且匹配角色的具名能力URI：' + uri)
        for condition in template['when']:
            if not condition['field'].startswith('task.') or ('ref' in condition and not condition['ref'].startswith('task.')):
                raise DesignError('序列适用条件只能引用task字段')
    return config


def step_requirements(task, template, step, owl):
    from .owl_scene import scene_uri
    from .capability_identity import capability_binding
    from .engine import DesignError
    node = str(scene_uri('step', step_id(task['id'], template['id'], step['id'])))
    from .vocabulary import KW
    capabilities = sorted({o for s, p, o in owl['facts'] if s == node and p == str(KW.requiresCapability)})
    for uri in capabilities:
        if capability_binding(step['role'], uri)['status'] != 'mapped':
            raise DesignError('OWL环节能力与环节角色不一致：' + uri)
    return node, capabilities


def connection(previous, current, incoming, links, model):
    """Physical reverse communication is allowed only under explicit policy.

    A control relationship remains directed even in an undirected physical net.
    """
    edge = links.get((previous, current))
    reversed_ = False
    if edge is None and model == 'undirected' and incoming == 'communication':
        edge = links.get((current, previous))
        reversed_ = edge is not None
    if edge is None:
        return 'unknown', 'MISSING_LINK', None
    if edge['communicates'] is False or edge['distance']['value'] >= edge['max_distance']['value']:
        return 'fail', 'NO_COMMUNICATION_OR_RANGE', edge
    if incoming == 'control' and edge['controls'] is False:
        return 'fail', 'NO_CONTROL', edge
    if edge['blocking_issue_ids'] or edge['communicates'] is None or (incoming == 'control' and edge['controls'] is None):
        return 'unknown', 'UNKNOWN_COOPERATION', edge
    return 'pass', 'CONNECTED', {**edge, 'used_from': previous, 'used_to': current, 'reversed_physical_link': reversed_}


def generate_task(task, doc, ontology_inference, owl, task_number):
    from .engine import infer_task_capabilities, evaluate, DesignError, SearchLimitError
    from .owl_scene import task_requirements
    from .plans import resource_conflicts
    config = SEQUENCES.get()
    mode = config['evaluation']['mode']
    base = infer_task_capabilities(task, doc['task_rules'], required_roles=(), ontology_requirements=task_requirements(task, owl))
    required = deepcopy(base['required_capabilities'])
    result = {'task': task['id'], 'target': task['target'], 'inference': base['inference'],
              'required_capabilities': {r: [] for r in ROLES}, 'selection': [], 'chains': [], 'rejected_chains': [],
              'blockers': base['blockers'], 'owl_evidence': base.get('owl_evidence', {}),
              'sequence_generation': {'mode': mode, 'nodes': 0, 'pruned': {}, 'templates': [],
                                      'scope': '按环节生成全部已知可行轨迹；不表示这些轨迹能同时执行，不计算论文转化率'}}
    stats = result['sequence_generation']
    artifacts = {a['id']: a for a in doc['artifacts']}
    evidence = {a['artifact']: a for a in ontology_inference['artifacts']}
    contexts = {(c['task'], c['artifact']): c for c in doc.get('contexts', [])}
    links = {(e['from'], e['to']): e for e in doc['links']}
    pruned = Counter()
    applicable = 0
    task_facts = {**task['attributes'], 'ontology_types': base.get('owl_evidence', {}).get('types', [])}
    for template in config['templates']:
        applicability = evaluate({'id': template['id'], 'role': 'insight', 'capability': 'template',
                                  'when': template['when'], 'source_refs': template['source_refs'],
                                  'blocking_issue_ids': template.get('blocking_issue_ids', [])}, {'task': task_facts})
        stats['templates'].append(applicability)
        if applicability['status'] != 'pass':
            if applicability['status'] == 'unknown':
                result['blockers'].append({'code': 'UNKNOWN_SEQUENCE_TEMPLATE', 'template': template['id']})
            continue
        applicable += 1
        domains, requirements = [], []
        union = {role: set() for role in ROLES}
        for step in template['steps']:
            node, caps = step_requirements(task, template, step, owl)
            union[step['role']].update(caps)
            requirements.append((node, caps))
        if any(set(required[role]) - union[role] for role in ROLES):
            pruned['UNMET_MISSION_CAPABILITY'] += 1
            continue
        for role in ROLES:
            result['required_capabilities'][role] = sorted(set(result['required_capabilities'][role]) | union[role])
        for step, (node, caps) in zip(template['steps'], requirements):
            domain = []
            for a in doc['artifacts']:
                if a['role'] != step['role']:
                    continue
                context = contexts.get((task['id'], a['id']), {})
                facts = {**a['attributes'], 'ontology_types': evidence[a['id']]['inferred_types'],
                         'ontology_capabilities': ontology_inference['relations']['capabilities'][a['id']]}
                checks = [evaluate(rule, {'task': task_facts, 'artifact': facts, 'context': context.get('attributes', {})})
                          for rule in doc['artifact_rules'] if rule['role'] == step['role'] and rule['capability'] in caps]
                cap_states = ['pass' if any(c['capability'] == cap and c['status'] == 'pass' for c in checks)
                              else 'unknown' if not any(c['capability'] == cap for c in checks) or any(c['capability'] == cap and c['status'] == 'unknown' for c in checks)
                              else 'fail' for cap in caps]
                level = a['attributes'].get('level_' + step['activity'])
                level_status = ('unknown' if not isinstance(level, dict) or level.get('unit') != '1' or
                                type(level.get('value')) not in (int, float) or not 0 <= level['value'] <= 1
                                else 'pass' if level['value'] >= step['min_level'] else 'fail')
                target_status = ('pass' if a['role'] == 'coordination' else
                                 'unknown' if not isinstance(task['attributes'].get('target_type'), str) or not isinstance(a['attributes'].get('target_types'), list)
                                 else 'pass' if task['attributes']['target_type'] in a['attributes']['target_types'] else 'fail')
                issues = a['blocking_issue_ids'] + context.get('blocking_issue_ids', [])
                issues += ['owl-mapping:' + item['mapping'] for item in owl.get('data_projection', [])
                           if item['kind'] == 'artifact' and item['owner'] == a['id'] and item['required'] and item['status'] == 'unknown']
                states = cap_states + [level_status, target_status]
                status = ('fail' if not a['available'] or 'fail' in states else
                          'unknown' if issues or 'unknown' in states or not caps else 'pass')
                result['selection'].append({'template': template['id'], 'step': step['id'], 'step_uri': node,
                    'artifact': a['id'], 'role': step['role'], 'required_capabilities': caps, 'accepted': status == 'pass',
                    'status': status, 'level_check': level_status, 'target_type_check': target_status,
                    'ontology_evidence': evidence[a['id']], 'rules': checks, 'blocking_issue_ids': issues})
                if status == 'unknown':
                    result['blockers'].append({'code': 'UNKNOWN_STEP_ARTIFACT', 'template': template['id'], 'step': step['id'], 'artifact': a['id']})
                if status == 'pass':
                    domain.append(a['id'])
            domains.append(domain)
        applicability['candidate_product_after_screening'] = math.prod(map(len, domains))
        if any(not domain for domain in domains):
            continue

        def walk(ids, edges):
            depth = len(ids)
            if depth < len(domains):
                for identifier in domains[depth]:
                    stats['nodes'] += 1
                    if stats['nodes'] > doc['policies'].get('max_sequence_search_nodes', 1000000):
                        raise SearchLimitError('序列生成节点超过显式上限；未返回不完整的可行轨迹集合')
                    if ids and identifier == ids[-1]:
                        pruned['ADJACENT_REUSE'] += 1
                        continue
                    if identifier in ids and not template['allow_nonadjacent_reuse']:
                        pruned['NONADJACENT_REUSE'] += 1
                        continue
                    edge = None
                    if ids:
                        status, reason, edge = connection(ids[-1], identifier, template['steps'][depth]['incoming_relation'], links, config['communication_model'])
                        if status != 'pass':
                            pruned[reason] += 1
                            if status == 'unknown':
                                result['blockers'].append({'code': 'UNKNOWN_COOPERATION', 'template': template['id'], 'from': ids[-1], 'to': identifier})
                            continue
                    walk(ids + [identifier], edges + ([edge] if edge else []))
                return
            ordered = []
            clock = task['start_s']
            metrics = []
            members = {role: [] for role in ROLES}
            usage = []
            for step, identifier, (node, caps) in zip(template['steps'], ids, requirements):
                a = artifacts[identifier]
                metric = a.get('activity_metrics', {}).get(step['activity']) if mode == 'sequential_product' else None
                record = {'id': step['id'], 'activity': step['activity'], 'role': step['role'], 'artifact': identifier,
                          'step_uri': node, 'required_capabilities': caps, 'min_level': step['min_level'],
                          'source_refs': template['source_refs'] + step['source_refs'], 'start_s': None, 'end_s': None}
                if metric is not None:
                    record.update(start_s=clock, end_s=clock + metric['duration_s'])
                    if not math.isfinite(record['end_s']) or record['end_s'] <= clock:
                        raise DesignError('环节时间溢出或无法以当前浮点精度表示')
                    clock = record['end_s']
                    if not math.isfinite(clock + a['cooldown_s']):
                        raise DesignError('环节冷却时间溢出')
                    usage.append({'artifact': identifier, 'capacity': 1, 'inventory': int(a['consumable']),
                                  'start_s': record['start_s'], 'end_s': clock + a['cooldown_s']})
                ordered.append(record)
                metrics.append(metric)
                if identifier not in members[step['role']]:
                    members[step['role']].append(identifier)
            chain = {'id': f'sequence-{task_number}-{len(result["chains"])+1}', 'task': task['id'], 'target': task['target'],
                     'artifacts': {step['id']: identifier for step, identifier in zip(template['steps'], ids)},
                     'closed_path': [task['target'], *ids, task['target']], 'link_evidence': edges,
                     'metrics': None, 'score': None, 'start_s': task['start_s'],
                     'plan': {'mode': 'ordered_steps', 'template': template['id'], 'steps': ordered, 'members': members,
                              'connections': [{'from': a, 'to': b, 'relation': step['incoming_relation']}
                                              for a, b, step in zip(ids, ids[1:], template['steps'][1:])], 'resource_usage': usage},
                     'evaluation': config['evaluation']}
            inventory = Counter(i for i in ids if artifacts[i]['consumable'])
            if any(n > artifacts[i]['inventory'] for i, n in inventory.items()):
                pruned['INVENTORY'] += 1
                return
            if mode == 'sequential_product' and all(m is not None for m in metrics):
                chain['metrics'] = {'time': sum(m['duration_s'] for m in metrics), 'accuracy': math.prod(m['accuracy'] for m in metrics),
                                    'cost': sum(m['cost']['value'] for m in metrics), 'cost_unit': doc['policies']['cost_unit']}
                chain['end_s'] = clock
                if not math.isfinite(clock) or not all(math.isfinite(chain['metrics'][k]) for k in ('time', 'accuracy', 'cost')):
                    raise DesignError('序列指标计算溢出')
                if resource_conflicts([chain], doc):
                    pruned['INTERNAL_RESOURCE_CONFLICT'] += 1
                    return
            elif mode == 'sequential_product':
                result['blockers'].append({'code': 'MISSING_ACTIVITY_METRICS', 'template': template['id'], 'artifacts': ids})
            result['chains'].append(chain)
            if len(result['chains']) > doc['policies']['max_chain_combinations']:
                raise SearchLimitError('序列可行候选数超过链组合上限；未截断后宣称集合完整')

        walk([], [])
    if not applicable and not result['blockers']:
        pruned['NO_APPLICABLE_TEMPLATE'] += 1
    stats['pruned'] = dict(pruned)
    stats['feasible_trajectories'] = len(result['chains'])
    stats['status'] = 'blocked' if result['blockers'] else 'complete'
    return result
