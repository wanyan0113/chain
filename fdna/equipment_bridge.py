"""Explicit, reviewed activity-flow adapter; equipment links alone are insufficient."""
from copy import deepcopy
from collections import defaultdict
from .model import ModelError
from .structure import digest
from .workflow_common import make_model, text


def selected_chain_records(report):
    """Resolve only the final selected chains; candidate chains never leak in."""
    if not isinstance(report,dict):raise ModelError('上游报告须为JSON对象')
    if report.get('status') != 'complete':
        raise ModelError('上游报告未完成')
    selected = report.get('network',{}).get('chains')
    if not isinstance(selected,list) or not selected or any(not isinstance(c,str) or not c.strip() for c in selected):
        raise ModelError('上游已选链须为非空编号数组')
    if len(selected) != len(set(selected)):
        raise ModelError('上游已选链重复')
    chains = {}
    if not isinstance(report.get('tasks'),list):raise ModelError('上游任务须为数组')
    for task in report['tasks']:
        if not isinstance(task,dict) or not isinstance(task.get('chains'),list):raise ModelError('上游任务缺少候选链数组')
        for chain in task['chains']:
            if not isinstance(chain,dict) or not isinstance(chain.get('id'),str):raise ModelError('候选链缺少编号')
            if chain['id'] in selected:
                if chain['id'] in chains:
                    raise ModelError('已选链编号不唯一')
                text(task.get('task'),'任务编号')
                if chain.get('plan',{}).get('mode')!='single_unit_per_role':raise ModelError('首版仅支持显式单装备角色计划')
                artifacts=chain.get('artifacts',{})
                if not isinstance(artifacts,dict) or set(artifacts)!={'insight','coordination','manifestation'}:
                    raise ModelError('已选链须完整声明三类角色装备')
                for role,subject in artifacts.items():
                    text(subject,'装备主体')
                    if chain['plan'].get('members',{}).get(role)!=[subject]:raise ModelError('链角色装备与活动计划成员不一致')
                chains[chain['id']] = (task['task'], chain)
    if set(chains) != set(selected):
        raise ModelError('已选链引用不存在')
    return chains


def bridge(report, config):
    if not isinstance(config,dict):raise ModelError('能力映射须为JSON对象')
    version=config.get('format_version')
    if version is not None and version!='bridge-review-1':raise ModelError('未知桥接审查格式')
    if 'source_report_sha256' in config and config['source_report_sha256']!=digest(report):
        raise ModelError('映射绑定的上游报告已变化，请重新生成或重新审查映射')
    if version=='bridge-review-1':
        if not config.get('source_report_sha256'):raise ModelError('审查映射必须绑定源报告摘要')
        gate=config.get('review_gate',{})
        if gate.get('status')!='approved_assumption':raise ModelError('映射草稿尚未完成模拟工作假设审查')
        for field in ('reviewed_by','note'):text(gate.get(field),'审查登记'+field)
        for item in config.get('activities',[])+config.get('state_sharing',[]):
            if item.get('review_status')!='approved_assumption':raise ModelError('能力实例或共享状态尚未完成审查')
            text(item.get('review_note'),'实例审查说明')
        for item in config.get('activities',[]):
            if item.get('metric',{}).get('definition','').startswith('待填写：'):raise ModelError('SE指标定义仍为占位文本')
            if any(item.get(f,'').startswith('待确认：') for f in ('context','state_identity_reason')):raise ModelError('能力场景或状态身份仍为待确认占位文本')
        for item in config.get('state_sharing',[]):
            if any(item.get(f,'').startswith('待确认：') for f in ('rationale','coupling','resource_scope')):raise ModelError('共享状态方案仍为待确认占位文本')
    chains=selected_chain_records(report)
    selected=list(chains)
    scope = config['chain_ids']
    if not scope or len(scope) != len(set(scope)) or not set(scope) <= set(chains):
        raise ModelError('分析范围须为已选链的非空子集')
    activities = {}; bindings = set()
    for item in config['activities']:
        n = text(item['id'], '能力实例id')
        if n in activities or item['chain_id'] not in scope:
            raise ModelError('能力实例重复或不在分析范围内')
        task, chain = chains[item['chain_id']]
        if chain['plan']['mode'] != 'single_unit_per_role':
            raise ModelError('首版仅支持显式单装备角色计划')
        if chain['artifacts'].get(item['role']) != item['subject']:
            raise ModelError('能力主体不是该已选链对应角色装备')
        key = (item['chain_id'], item['role'])
        if key in bindings:
            raise ModelError('同一链角色重复绑定；共享状态须另行审查')
        bindings.add(key)
        for field in ('context', 'state_identity_reason'):
            text(item[field], field)
        metric = item['metric']
        if metric.get('direction') != 'higher_is_better' or metric.get('normalization') != '100*x' or metric.get('raw_range') != [0, 1]:
            raise ModelError('首版SE须声明[0,1]正向质量指标乘100；不得使用链排名分数')
        text(metric['definition'], '指标定义')
        for field in ('inputs', 'outputs'):
            if not isinstance(item[field], list) or len(item[field]) != len(set(item[field])):
                raise ModelError('输入输出标识须为不重复数组')
            for token in item[field]: text(token, '数据产品标识')
        activities[n] = deepcopy(item)
    expected = {(c, r) for c in scope for r in chains[c][1]['artifacts']}
    if bindings != expected:
        raise ModelError('范围内链角色必须完整映射')
    # Reusing physical subjects across tasks requires an explicit state contract.
    shared=defaultdict(list)
    for n,item in activities.items(): shared[item['subject'],item['role']].append(n)
    required={key:set(value) for key,value in shared.items() if len(value)>1}
    registry={}
    for policy in config.get('state_sharing',[]):
        key=policy['subject'],policy['role']
        if key in registry or key not in required or set(policy['instance_ids'])!=required[key] or len(policy['instance_ids'])!=len(required[key]):
            raise ModelError('共享主体登记重复或实例覆盖不一致')
        if policy['mode']!='task_conditioned_outputs': raise ModelError('首版共享主体须显式采用不同任务产品状态')
        for field in ('rationale','coupling','resource_scope'):text(policy[field],field)
        registry[key]=deepcopy(policy)
    if set(registry)!=set(required): raise ModelError('共享装备跨任务状态尚未登记，禁止自动复制或合并')
    # Product matching proposes candidates. Missing reviews remain pending.
    rules = deepcopy(config['reviews'])
    reviewed = {(r['feeder'], r['receiver'], r.get('product')) for r in rules}
    for a in sorted(activities):
        for b in sorted(activities):
            if a == b or activities[a]['chain_id'] != activities[b]['chain_id']: continue
            for token in sorted(set(activities[a]['outputs']) & set(activities[b]['inputs'])):
                if (a,b,token) not in reviewed:
                    rules.append(dict(id=f'auto_{a}_{b}_{token}', feeder=a, receiver=b, product=token,
                        status='pending', rationale='输入输出名称匹配自动提出；尚无人审查，不导出'))
    candidates = []; edges = []; pairs = set(); ids = set(); claims=set()
    for rule in rules:
        rid = text(rule['id'], '审查id')
        if rid in ids: raise ModelError('审查id重复')
        ids.add(rid)
        a, b = rule['feeder'], rule['receiver']
        if a not in activities or b not in activities or a == b:
            raise ModelError('候选边引用未知实例或自环')
        left, right = activities[a], activities[b]
        token = rule.get('product')
        if token is not None:text(token,'产品标识')
        claim=(a,b,token)
        if claim in claims:raise ModelError('同一产品依赖有重复或冲突审查记录')
        claims.add(claim)
        matched = bool(token and token in left['outputs'] and token in right['inputs'])
        if left['chain_id'] != right['chain_id']:
            raise ModelError('首版跨链状态关系须另行建模')
        chain = chains[left['chain_id']][1]
        links = [e for e in chain['link_evidence'] if e['from'] == left['subject'] and e['to'] == right['subject'] and e.get('communicates') is True and not e.get('blocking_issue_ids')]
        status = rule['status']
        if status not in ('approved_assumption', 'pending', 'rejected'):
            raise ModelError('未知审查状态')
        text(rule['rationale'], '审查依据')
        export = status == 'approved_assumption'
        if export and (not matched or not links):
            raise ModelError('批准边必须同时具有直接设备连接与显式输入输出消费依据')
        if export:
            if (a, b) in pairs: raise ModelError('重复导出依赖边')
            pairs.add((a, b)); edges.append(dict(feeder=a, receiver=b))
        candidates.append(dict(**deepcopy(rule), product_matched=matched,
                               equipment_link_evidence=deepcopy(links), exported=export))
    network = dict(id=config['id'], nodes=sorted(activities), edges=sorted(edges, key=lambda e:(e['feeder'],e['receiver'])))
    make_model(network)  # rejects cycles, unknown references and empty edge sets
    return network, dict(source_report_sha256=digest(report), scope_chain_ids=sorted(scope),
        excluded_selected_chain_ids=sorted(set(selected)-set(scope)), capability_instances=activities,
        candidates=candidates, state_sharing=list(registry.values()),
        review_gate=deepcopy(config.get('review_gate')),
        upstream_relations_not_exported=deepcopy(report['network'].get('relations', [])),
        policies=dict(topology_from_reviewed_assumptions=True, topology_learned_from_se=False,
                      transitive_edges_added=False, score_used_as_se=False, real_dependencies_verified=False))
