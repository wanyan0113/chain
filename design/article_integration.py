"""Adapters from full design inputs to the source-backed table10 evaluators."""
from collections import Counter


def validate_counts(doc):
    from .engine import DesignError
    for objects, fields in [(doc['tasks'], ['target_count']),
                             (doc['artifacts'], ['max_tracks', 'max_processing_targets'])]:
        for obj in objects:
            for field in fields:
                value = obj['attributes'].get(field)
                if value is not None and (not isinstance(value, dict) or value.get('unit') != '1'
                        or type(value.get('value')) is not int or value['value'] < 1):
                    raise DesignError(f"{obj['id']}.{field}必须为正整数且单位为1，未知请留空")


def screening(task, artifact, context):
    from .article_rules import screen_table10
    if artifact['role'] == 'coordination':
        return None
    return screen_table10({'role':artifact['role'], 'task':task['attributes'],
                           'artifact':artifact['attributes'], 'context':context.get('attributes',{})})


def cooperation(chosen, doc):
    """Sweep actual [start,end) intervals; do not treat overlap as transitive.

    Cooldown reservations remain enforced by the engine's resource checks.
    The source's single-chain manifestation rule applies to the whole plan,
    including serial chains, independently of inventory and cooldown.
    """
    from .article_cooperation import check_cooperation, SOURCES
    tasks = {task['id']:task for task in doc['tasks']}
    artifacts = {artifact['id']:artifact for artifact in doc['artifacts']}
    def count(attributes, field):
        value = attributes.get(field)
        return None if value is None else value['value']
    groups, seen = [], set()
    for start in sorted({chain['start_s'] for chain in chosen}):
        active = [chain for chain in chosen if chain['start_s'] <= start < chain['end_s']]
        identity = tuple(sorted(chain['id'] for chain in active))
        if identity in seen:
            continue
        seen.add(identity)
        task_ids = {chain['task'] for chain in active}
        artifact_ids = {a for chain in active for a in chain['artifacts'].values()}
        used_edges = {(chain['artifacts'][left],chain['artifacts'][right]) for chain in active
                      for left,right in [('insight','coordination'),('coordination','manifestation')]}
        used_contexts = {(chain['task'],a) for chain in active for a in chain['artifacts'].values()}
        pending = [issue for task in doc['tasks'] if task['id'] in task_ids for issue in task['blocking_issue_ids']]
        pending += [issue for a in doc['artifacts'] if a['id'] in artifact_ids for issue in a['blocking_issue_ids']]
        data = {'scope':doc['scope'], 'mode':'declared_parallel_group', 'blocking_issue_ids':list(dict.fromkeys(pending)),
                'chains':[dict(id=c['id'],task=c['task'],target_count=count(tasks[c['task']]['attributes'],'target_count'),
                               source_refs=tasks[c['task']]['source_refs'],**c['artifacts']) for c in active],
                'artifacts':[], 'links':[], 'contexts':[]}
        for identifier in sorted(artifact_ids):
            artifact = artifacts[identifier]
            data['artifacts'].append(dict(id=identifier,role=artifact['role'],source_refs=artifact['source_refs'],
                detection_range=artifact['attributes'].get('detection_range'),
                max_tracks=count(artifact['attributes'],'max_tracks'),
                max_processing_targets=count(artifact['attributes'],'max_processing_targets')))
        for edge in doc['links']:
            if (edge['from'],edge['to']) in used_edges:
                # Reasoning adds provenance to links. The standalone checker
                # accepts physical/cooperation facts only; evidence stays in
                # the full design report and must not leak into its schema.
                data['links'].append({key:value for key,value in edge.items()
                                      if key in ('from', 'to', 'communicates', 'controls',
                                                 'distance', 'max_distance', 'source_refs')})
                data['blocking_issue_ids'] += edge['blocking_issue_ids']
        for context in doc.get('contexts',[]):
            if (context['task'],context['artifact']) in used_contexts:
                data['contexts'].append(dict(task=context['task'],artifact=context['artifact'],
                    target_distance=context['attributes'].get('target_distance'),source_refs=context['source_refs']))
                data['blocking_issue_ids'] += context['blocking_issue_ids']
        data['blocking_issue_ids'] = list(dict.fromkeys(data['blocking_issue_ids']))
        result = check_cooperation(data)
        groups.append(dict(start_s=start,chains=list(identity),status=result['status'],checks=result['checks']))
    usage = Counter(chain['artifacts']['manifestation'] for chain in chosen)
    unique_checks = [dict(id='single-manifestation-plan',quote='具现法器只能在一条法术链中',
                         source=SOURCES['network'],status='pass' if n==1 else 'fail',artifact=a,uses=n)
                     for a,n in sorted(usage.items())]
    statuses = {group['status'] for group in groups} | {check['status'] for check in unique_checks}
    return {'status':'fail' if 'fail' in statuses else 'unknown' if 'unknown' in statuses else 'pass',
            'groups':groups,'plan_checks':unique_checks,
            'scope':'表10条件按实际重叠的整链执行区间分组；具现法器全计划不重复，冷却和库存另由工程约束检查'}
