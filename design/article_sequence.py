"""Explicit table-10 adaptation for ordered activity plans.

Sharing means the same artifact executing activities for distinct chains in
overlapping [start,end) intervals. Each chain's target count contributes once,
even if it repeats an activity. Cooldown is an engineering reservation, not an
extra target-processing activity. Manifestation exclusivity is plan-wide and
counts distinct chains, not repeated executions within one chain. These timing
and counting conventions extend the source rule; they are not a reported
multistep scheduling protocol from the article.
"""
from collections import defaultdict

from .article_cooperation import SOURCES
from .engine import DesignError, condition_result
from .sequences import SEQUENCES, connection


def check_sequence_cooperation(chosen, doc):
    tasks = {task['id']: task for task in doc['tasks']}
    artifacts = {artifact['id']: artifact for artifact in doc['artifacts']}
    contexts = {(c['task'], c['artifact']): c for c in doc.get('contexts', [])}
    links = {(e['from'], e['to']): e for e in doc['links']}
    communication_model = (SEQUENCES.get() or {}).get('communication_model', 'directed')
    executions, manifestation = defaultdict(list), defaultdict(set)
    plan_checks, groups = [], []

    def status(checks):
        states = {check['status'] for check in checks}
        return 'fail' if 'fail' in states else 'unknown' if 'unknown' in states else 'pass'

    def compare(identifier, quote, left, right, evidence):
        state, reason = condition_result(
            {'field': 'artifact.left', 'op': 'gt', 'ref': 'task.right'},
            {'artifact': {'left': left}, 'task': {'right': right}})
        return dict(id=identifier, quote=quote, source=SOURCES['network'], status=state,
                    evidence=dict(left=left, right=right, reason=reason, **evidence))

    for chain in chosen:
        plan = chain.get('plan', {})
        if plan.get('mode') != 'ordered_steps':
            raise DesignError('表10协同检查不能混用整链占用和逐活动计划')
        for step in plan['steps']:
            start, end = step['start_s'], step['end_s']
            if start is None or end is None or end <= start:
                raise DesignError('多环节表10协同检查需要有效的逐活动起止时间')
            identifier = step['artifact']
            executions[identifier].append((start, end, chain, step['id']))
            if step['role'] == 'manifestation':
                manifestation[identifier].add(chain['id'])
        for edge in plan['connections']:
            state, reason, physical = connection(edge['from'], edge['to'], edge['relation'],
                                                 links, communication_model)
            plan_checks.append(dict(id='sequence-link', quote='相邻活动满足传讯距离及所需统筹关系',
                source=SOURCES['link'], status=state,
                evidence=dict(chain=chain['id'], connection=edge, reason=reason, physical=physical)))

    for identifier, chain_ids in sorted(manifestation.items()):
        plan_checks.append(dict(id='single-manifestation-plan', quote='具现法器只能在一条法术链中',
            source=SOURCES['network'], status='pass' if len(chain_ids) == 1 else 'fail',
            artifact=identifier, uses=len(chain_ids), chains=sorted(chain_ids)))

    for identifier, intervals in sorted(executions.items()):
        artifact = artifacts[identifier]
        if artifact['role'] == 'manifestation':
            continue
        # Endpoints are evaluated too: [start,end) permits immediate handoff,
        # and an ending unknown participant must not hide a later known group.
        boundaries = sorted({t for start, end, _, _ in intervals for t in (start, end)})
        seen = set()
        for start in boundaries:
            active = [(chain, step) for a, b, chain, step in intervals if a <= start < b]
            identity = tuple(sorted((chain['id'], step) for chain, step in active))
            if identity in seen:
                continue
            seen.add(identity)
            chains = {chain['id']: chain for chain, _ in active}
            if len(chains) < 2:
                continue
            ordered = [chains[key] for key in sorted(chains)]
            counts = [tasks[c['task']]['attributes'].get('target_count') for c in ordered]
            total = None if any(c is None for c in counts) else {
                'value': sum(c['value'] for c in counts), 'unit': '1'}
            evidence = dict(artifact=identifier, chains=sorted(chains),
                            activities=[dict(chain=c['id'], step=s) for c, s in active],
                            source_refs=artifact['source_refs'])
            checks = []
            if artifact['role'] == 'insight':
                for chain in ordered:
                    context = contexts.get((chain['task'], identifier), {})
                    checks.append(compare('shared-detection-range', '共享探知距离需要同时大于所有任务目标距离',
                        artifact['attributes'].get('detection_range'),
                        context.get('attributes', {}).get('target_distance'),
                        dict(evidence, task=chain['task'], context_source_refs=context.get('source_refs', []))))
                field, rule, quote = 'max_tracks', 'shared-max-tracks', '最大标记数需要大于所有任务目标数量总和'
            else:
                field, rule, quote = 'max_processing_targets', 'shared-max-processing', '最大解析目标数需要大于所有任务目标数量总和'
            checks.append(compare(rule, quote, artifact['attributes'].get(field), total, evidence))
            groups.append(dict(start_s=start, artifact=identifier, chains=sorted(chains),
                               status=status(checks), checks=checks))

    return dict(status=status(plan_checks + [c for g in groups for c in g['checks']]),
                groups=groups, plan_checks=plan_checks,
                scope='表10多环节适配：按实际活动[start,end)检查跨链共享；每链目标数只计一次；'
                      '具现法器全计划只归属一条链；冷却容量另由资源约束检查。此时间与计数约定为显式扩展。')
