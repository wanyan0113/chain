"""Section 4.1 evidence review; does not infer effects from temporal order."""
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .model import ModelError
from .structure import digest, members, validate_structure

MAIN_TYPES = {('R', 'R'), ('R', 'D'), ('R', 'E'), ('D', 'R'), ('D', 'D'), ('D', 'E')}


def identify_dependencies(model, structure, records):
    validate_structure(model, structure)
    schema = json.loads(Path(__file__).with_name('identification_schema.json').read_text(encoding='utf-8'))
    errors = list(Draft202012Validator(schema).iter_errors(records))
    if errors:
        raise ModelError('依赖识别记录不符合数据契约：' + errors[0].message)
    if records['case_sha256'] != digest(model) or records['structure_sha256'] != digest(structure):
        raise ModelError('依赖识别记录与案例或活动结构版本不一致')
    nodes = {n['id'] for n in model['nodes']}
    if not set(records['focus_receivers']) <= nodes:
        raise ModelError('关注的接受能力不存在')
    activities = {a['id']: a for a in structure['activities']}
    refs = {f"{s['id']}:{loc}" for s in model['sources'] for loc in s['locators']}
    refs.update(e['source'] + ':' + e['id'] for e in structure['source_locators'])
    evidence = {e['id']: e for e in records['evidence']}
    if len(evidence) != len(records['evidence']):
        raise ModelError('证据标识重复')
    for item in evidence.values():
        if not set(item['source_refs']) <= refs:
            raise ModelError('证据引用未登记的来源位置')
    identifiers, groups = set(), {}
    for item in records['interactions']:
        if item['id'] in identifiers:
            raise ModelError('交互记录标识重复')
        identifiers.add(item['id'])
        if item['feeder'] == item['receiver']:
            raise ModelError('不能登记能力自身依赖')
        for endpoint in ('feeder', 'receiver'):
            activity = activities.get(item[endpoint + '_activity'])
            if activity is None or item[endpoint] not in set(members(activity['capability_group'])):
                raise ModelError('交互端点能力与活动槽位不对应')
        for field, kind in (('interaction_evidence', 'interaction'), ('effect_evidence', 'effect')):
            if any(ref not in evidence or evidence[ref]['kind'] != kind for ref in item[field]):
                raise ModelError('交互或效能影响证据不存在，或证据类别不一致')
        if item['effect'] != 'unknown' and not item['effect_evidence']:
            raise ModelError('效能影响判断须有单独的效能证据')
        if ((item['review'] == 'include' and item['effect'] != 'supported') or
                (item['review'] == 'exclude' and item['effect'] != 'not_obvious')):
            raise ModelError('纳入或排除意见与效能影响判断不一致')
        first, second = (activities[item[e + '_activity']]['type'] for e in ('feeder', 'receiver'))
        main = (first, second) in MAIN_TYPES
        # The six types are the paper's main scope, not a universal impossibility rule.
        if item['review'] == 'include' and not main:
            raise ModelError('非文稿主要活动关系须另行论证，不能在此流程直接纳入')
        pair = item['feeder'], item['receiver']
        groups.setdefault(pair, []).append({**deepcopy(item),
                                           'activity_types': [first, second], 'within_main_types': main})
    candidates = []
    for (feeder, receiver), items in sorted(groups.items()):
        reviews = {i['review'] for i in items}
        effects = {i['effect'] for i in items} - {'unknown'}
        if len(effects) > 1 or {'include', 'exclude'} <= reviews:
            status = 'conflicting'
        elif 'pending' in reviews:
            status = 'pending'
        elif reviews == {'include'}:
            status = 'included'
        elif reviews == {'exclude'}:
            status = 'excluded'
        else:
            status = 'pending'
        in_scope = receiver in records['focus_receivers']
        candidates.append({'feeder': feeder, 'receiver': receiver,
                           'status': status if in_scope else 'out_of_scope', 'review_status': status,
                           'records': items})
    included = {(c['feeder'], c['receiver']) for c in candidates if c['status'] == 'included'}
    reported = {(e['feeder'], e['receiver']) for e in model['edges']
                if e['receiver'] in records['focus_receivers']}
    def pairs(values):
        return [{'feeder': a, 'receiver': b} for a, b in sorted(values)]
    unresolved = any(c['status'] in ('pending', 'conflicting') for c in candidates)
    return {
        'format_version': '1.0', 'status': ('no_records' if not candidates else
                                         'needs_review' if unresolved else 'reviewed'),
        'scope': '第4.1节活动交互与效能影响的显式审查；不从活动顺序生成依赖，不拟合数值参数。',
        'case': model['id'], 'data_kind': records['data_kind'],
        'input_sha256': digest({'case': model, 'structure': structure, 'records': records}),
        'counts': {'interactions': len(identifiers), 'candidate_pairs': len(candidates),
                   **{s: sum(c['status'] == s for c in candidates)
                      for s in ('included', 'excluded', 'pending', 'conflicting', 'out_of_scope')}},
        'candidates': candidates,
        'comparison': {'matched': pairs(included & reported), 'additional': pairs(included - reported),
                       'reported_without_included_evidence': pairs(reported - included),
                       'meaning': '仅对照已纳入边；没有记录不等于原文边不存在。'},
        'records_snapshot': deepcopy(records), 'structure_snapshot': deepcopy(structure),
        'source_snapshot': deepcopy(model['sources']),
        'policies': {'record_provenance': 'declared_only', 'effect_judgment': 'explicit_review',
                     'activity_partition': 'interpretation', 'automatic_causal_inference': False,
                     'case_edges_modified': False, 'numeric_parameters_inferred': False},
        'case_reproduction_ready': False,
        'limits': ['证据位置与影响结论由输入声明，未自动验证记录真实性或因果关系',
                   '活动仍采用第三节解释性槽位；不据此确认实际划分或组内时序',
                   '核验样例及原文注释不等于原始推演记录，不能证明18条边已独立识别'],
    }
