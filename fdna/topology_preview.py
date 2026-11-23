"""Read-only structural impact previews of registered candidate changes; never apply or fit."""
import argparse
from copy import deepcopy
import heapq
import json
from pathlib import Path

from .model import ModelError
from .structure import digest
from .workflow_common import dumps, text, write_bundle


def canonical_network(network):
    if not isinstance(network, dict) or set(network) != {'id', 'nodes', 'edges'}:
        raise ModelError('网络须且仅含id、nodes、edges')
    text(network['id'], '网络id')
    nodes = network['nodes']
    if not isinstance(nodes, list) or not nodes:
        raise ModelError('节点须为非空数组')
    for node in nodes: text(node, '节点id')
    if len(nodes) != len(set(nodes)) or not isinstance(network['edges'], list):
        raise ModelError('节点重复或边不是数组')
    pairs = set()
    for edge in network['edges']:
        if not isinstance(edge, dict) or set(edge) != {'feeder', 'receiver'}:
            raise ModelError('边须且仅含feeder、receiver')
        for value in edge.values(): text(value, '边端点')
        a, b = edge['feeder'], edge['receiver']
        if a not in nodes or b not in nodes or a == b or (a, b) in pairs:
            raise ModelError('边端点未知、自指或重复')
        pairs.add((a, b))
    result = dict(id=network['id'], nodes=sorted(nodes),
                  edges=[dict(feeder=a, receiver=b) for a, b in sorted(pairs)])
    graph_info(result)  # Empty edge sets are meaningful previews, although the fitter rejects them.
    return result


def graph_info(network):
    incoming = {n: [] for n in network['nodes']}; outgoing = {n: [] for n in network['nodes']}
    for edge in network['edges']:
        incoming[edge['receiver']].append(edge['feeder']); outgoing[edge['feeder']].append(edge['receiver'])
    degrees = {n: len(v) for n, v in incoming.items()}; roots = sorted(n for n in degrees if not degrees[n])
    ready = list(roots); heapq.heapify(ready); order = []
    while ready:
        node = heapq.heappop(ready); order.append(node)
        for successor in sorted(outgoing[node]):
            degrees[successor] -= 1
            if not degrees[successor]: heapq.heappush(ready, successor)
    if len(order) != len(network['nodes']):
        raise ModelError('变更产生环路，当前DAG分析不支持')
    return dict(incoming=incoming, outgoing=outgoing, roots=roots, order=order)


def descendants(info, start):
    pending = list(info['outgoing'][start]); reached = set()
    while pending:
        node = pending.pop()
        if node not in reached:
            reached.add(node); pending.extend(info['outgoing'][node])
    return sorted(reached)


def paths(info, start, limit):
    stack = [[start]]; result = []
    while stack:
        path = stack.pop()
        if len(path) > 1:
            if len(result) == limit: return result, True
            result.append(path)
        stack.extend(path+[n] for n in sorted(info['outgoing'][path[-1]], reverse=True))
    return result, False


def candidate_registry(network, sidecar):
    if not isinstance(sidecar, dict) or not isinstance(sidecar.get('capability_instances'), dict) or not isinstance(sidecar.get('candidates'), list):
        raise ModelError('审计依据须包含能力实例对象与候选数组')
    activities = sidecar.get('capability_instances', {})
    if set(activities) != set(network['nodes']):
        raise ModelError('审计实例与拓扑节点不一致')
    for activity in activities.values():
        if not isinstance(activity, dict): raise ModelError('能力实例须为对象')
        for field in ('subject', 'chain_id'): text(activity.get(field), '能力实例'+field)
        for field in ('inputs', 'outputs'):
            tokens = activity.get(field)
            if not isinstance(tokens, list): raise ModelError('产品输入输出须为数组')
            for token in tokens: text(token, '产品标识')
            if len(tokens) != len(set(tokens)): raise ModelError('产品标识重复')
    registry = {}; exported = []
    for candidate in sidecar.get('candidates', []):
        if not isinstance(candidate, dict): raise ModelError('候选须为对象')
        cid = text(candidate.get('id'), '候选id')
        if cid in registry: raise ModelError('候选id重复')
        a, b = candidate.get('feeder'), candidate.get('receiver')
        text(a, '候选供给节点'); text(b, '候选接受节点')
        if a not in activities or b not in activities or a == b: raise ModelError('候选端点无效')
        product = candidate.get('product')
        if product is not None: text(product, '产品标识')
        matched = bool(product and product in activities[a]['outputs'] and product in activities[b]['inputs'])
        if candidate.get('product_matched') is not matched:
            raise ModelError('候选保存的产品匹配与消费定义不一致')
        if candidate.get('status') not in ('approved_assumption', 'pending', 'rejected'):
            raise ModelError('候选审查状态无效')
        evidence = candidate.get('equipment_link_evidence', [])
        if not isinstance(evidence, list) or any(not isinstance(e, dict) for e in evidence):
            raise ModelError('通道证据须为对象数组')
        links = [e for e in evidence if e.get('from') == activities[a]['subject']
                 and e.get('to') == activities[b]['subject'] and e.get('communicates') is True and not e.get('blocking_issue_ids')]
        reasons = []
        if not matched: reasons.append('NO_DIRECT_PRODUCT_CONSUMPTION')
        if not links: reasons.append('NO_DIRECT_CHANNEL_EVIDENCE')
        if activities[a]['chain_id'] != activities[b]['chain_id']: reasons.append('CROSS_CHAIN_NOT_SUPPORTED')
        if candidate['status'] == 'rejected': reasons.append('CANDIDATE_REJECTED')
        if not isinstance(candidate.get('exported'), bool): raise ModelError('候选导出标记须为布尔值')
        if candidate['exported']:
            if reasons or candidate['status'] != 'approved_assumption': raise ModelError('原导出边缺少合格登记依据')
            exported.append((a, b))
        registry[cid] = dict(candidate=candidate, blockers=reasons)
    expected = {(e['feeder'], e['receiver']) for e in network['edges']}
    if len(exported) != len(set(exported)) or set(exported) != expected:
        raise ModelError('审计导出边与源拓扑不一致')
    return registry


def preview(network, sidecar, proposal, max_paths=100):
    original = canonical_network(network); registry = candidate_registry(original, sidecar)
    if not isinstance(max_paths, int) or isinstance(max_paths, bool) or max_paths < 1:
        raise ModelError('路径上限须为正整数')
    if not isinstance(proposal, dict) or set(proposal) != {'id', 'source_network_sha256', 'source_sidecar_sha256', 'add', 'remove'}:
        raise ModelError('提案须包含id、源网络与审计摘要、add、remove')
    text(proposal['id'], '提案id')
    if proposal['source_network_sha256'] != digest(original): raise ModelError('提案绑定的源网络已变化')
    if proposal['source_sidecar_sha256'] != digest(sidecar): raise ModelError('提案绑定的审计依据已变化')
    actions = []; seen_ids = set(); seen_pairs = set()
    for action in ('remove', 'add'):
        if not isinstance(proposal[action], list): raise ModelError('变更须为数组')
        for change in proposal[action]:
            if not isinstance(change, dict) or set(change) != {'candidate_id', 'rationale'}:
                raise ModelError('变更须且仅含candidate_id和rationale')
            cid = text(change['candidate_id'], '候选id'); text(change['rationale'], '变更依据')
            if cid not in registry or cid in seen_ids: raise ModelError('候选未知或重复变更')
            candidate = registry[cid]['candidate']; pair = candidate['feeder'], candidate['receiver']
            if pair in seen_pairs: raise ModelError('同一端点对不能重复或同时增删')
            if (action == 'remove') != candidate['exported']:
                raise ModelError('只能删除已导出边或新增未导出候选，不能重复已有端点边')
            if action == 'add' and pair in {(e['feeder'], e['receiver']) for e in original['edges']}:
                raise ModelError('新增候选与已有端点边重复')
            seen_ids.add(cid); seen_pairs.add(pair)
            actions.append(dict(action=action, candidate_id=cid, feeder=pair[0], receiver=pair[1],
                rationale=change['rationale'], product=candidate.get('product'), review_status=candidate['status'],
                semantic_blockers=registry[cid]['blockers'] if action == 'add' else []))
    if not actions: raise ModelError('提案没有变更')
    pairs = {(e['feeder'], e['receiver']) for e in original['edges']}
    for action in actions:
        pair = action['feeder'], action['receiver']
        pairs.remove(pair) if action['action'] == 'remove' else pairs.add(pair)
    proposed = canonical_network(dict(id=original['id'], nodes=original['nodes'],
        edges=[dict(feeder=a, receiver=b) for a, b in sorted(pairs)]))
    before = graph_info(original); after = graph_info(proposed)
    changed = sorted({a['receiver'] for a in actions}); affected = set(changed); reachability = []; path_rows = []
    for node in changed:
        old = descendants(before, node); new = descendants(after, node); affected.update(old); affected.update(new)
        reachability.append(dict(node=node, incoming_before=sorted(before['incoming'][node]),
            incoming_after=sorted(after['incoming'][node]), descendants_before=old, descendants_after=new))
    for node in sorted({a['feeder'] for a in actions}):
        old, old_truncated = paths(before, node, max_paths); new, new_truncated = paths(after, node, max_paths)
        path_rows.append(dict(from_node=node, before_paths=old, after_paths=new,
            before_truncated=old_truncated, after_truncated=new_truncated, path_limit=max_paths))
    added_roots = sorted(set(after['roots'])-set(before['roots'])); lost_roots = sorted(set(before['roots'])-set(after['roots']))
    blockers = [dict(candidate_id=a['candidate_id'], code=code) for a in actions for code in a['semantic_blockers']]
    warnings = []
    if added_roots: warnings.append('新增根原先属于接受节点；不得自动读取其观测真值或从评价目标中删除。须明确训练侧无依赖预测或新增外部输入证据。')
    if lost_roots: warnings.append('原根变为接受节点，外部信息集和方程改变；必须重新登记输入与评价条件。')
    warnings.append('结构可达仅表示潜在影响，不保证数值变化；预览不批准依赖，不自动复用参数，不进行拟合或误差比较。')
    result = dict(stage='registered_edge_change_preview', preview_id='preview_'+digest([original, sidecar, proposal, max_paths])[:24],
        status='semantic_blocked' if blockers else 'review_required', source_network_sha256=digest(original),
        sidecar_sha256=digest(sidecar), proposal=deepcopy(proposal), actions=actions, semantic_blockers=blockers,
        original_topology=original, proposed_topology_preview=proposed, roots_before=before['roots'], roots_after=after['roots'],
        added_roots=added_roots, lost_roots=lost_roots, changed_receivers=changed, affected_nodes=sorted(affected),
        unchanged_nodes=sorted(set(original['nodes'])-affected), reachability=reachability, paths=path_rows,
        local_equations_to_refit=[n for n in changed if after['incoming'][n]],
        local_equations_removed=[n for n in changed if not after['incoming'][n]],
        parameter_reuse_forbidden_receivers=changed, downstream_prediction_recompute=sorted(affected),
        original_evaluation_targets=sorted(n for n in original['nodes'] if before['incoming'][n]),
        requires_explicit_root_policy=added_roots, empty_edge_graph=not bool(proposed['edges']), warnings=warnings,
        policies=dict(preview_only=True, topology_applied=False, mapping_approved=False, parameters_reused=False,
            fitted=False, numeric_effect_established=False, external_information_changed=bool(added_roots or lost_roots)))
    return result


def markdown(result):
    lines = ['# 依赖边变更影响预览', '', f"状态：{result['status']}；标识：{result['preview_id']}。", '',
        '**只读预览：不改变正式图、审查状态或参数，不运行拟合。**', '',
        '| 操作 | 候选 | 供给→接受 | 变更依据 | 语义阻断 |', '| --- | --- | --- | --- | --- |']
    safe = lambda v: str(v).replace('|', '/').replace('\n', ' ')
    for a in result['actions']:
        lines.append('| '+' | '.join(safe(v) for v in [a['action'], a['candidate_id'], a['feeder']+'→'+a['receiver'],
            a['rationale'], ', '.join(a['semantic_blockers']) or '无；仍须重新审查'])+' |')
    lines += ['', '## 结构变化', '']
    for key, label in [('added_roots','新增根'), ('lost_roots','不再作为根'), ('affected_nodes','需重算预测的节点'),
        ('local_equations_to_refit','入边改变且仍有输入：需重新拟合的局部方程'),
        ('local_equations_removed','失去全部输入：须另定预测策略'), ('original_evaluation_targets','必须保留的原评价目标')]:
        lines.append('- '+label+'：'+(', '.join(result[key]) or '无'))
    lines += ['', '下游局部方程的入边未变时，不因结构可达就自动要求重新拟合；在局部观测数据、分组和模型设置不变的条件下，'
        '其已有局部参数可另行核对。整网预测仍须重算；被直接改边的接受节点禁止直接套用旧参数。', '',
        '传播路径及截断标记见[完整预览JSON](preview_report.json)。空边图仅用于结构审查，不能直接交给现有拟合入口。', '',
        '## 待处理事项', '', *['- '+warning for warning in result['warnings']], '']
    return '\n'.join(lines)


def run(network_path, sidecar_path, proposal_path, output_dir, max_paths=100):
    out = Path(output_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())): raise ModelError('输出目录须不存在或为空')
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
    result = preview(read(network_path), read(sidecar_path), read(proposal_path), max_paths)
    write_bundle(out, {'preview_report.json':dumps(result), '结果说明.md':markdown(result)})
    print(dumps(dict(status=result['status'], preview_id=result['preview_id'], added_roots=result['added_roots'],
                     affected_nodes=result['affected_nodes'], policies=result['policies'])))
    return result


def main():
    parser = argparse.ArgumentParser(description='已登记候选边的只读结构影响预览，不批准映射或执行拟合')
    parser.add_argument('--network', type=Path, required=True); parser.add_argument('--sidecar', type=Path, required=True)
    parser.add_argument('--proposal', type=Path, required=True); parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--max-paths', type=int, default=100)
    args = parser.parse_args()
    result = run(args.network, args.sidecar, args.proposal, args.output_dir, args.max_paths)
    return 2 if result['status'] == 'semantic_blocked' else 0


if __name__ == '__main__': raise SystemExit(main())
