"""Replay 06 structural-review cases with an independent transitive-closure oracle."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fdna.flow_scenario import verify
from fdna.structure import digest
from fdna.topology_preview import canonical_network, preview, markdown
from fdna.workflow_common import csv_text, dumps, write_bundle


def oracle(network):
    nodes = network['nodes']; reach = {(a,b):False for a in nodes for b in nodes}
    for e in network['edges']: reach[e['feeder'],e['receiver']] = True
    for middle in nodes:
        for a in nodes:
            for b in nodes: reach[a,b] = reach[a,b] or (reach[a,middle] and reach[middle,b])
    assert not any(reach[n,n] for n in nodes)
    parents = {n:sorted(e['feeder'] for e in network['edges'] if e['receiver'] == n) for n in nodes}
    return dict(roots=sorted(n for n in nodes if not parents[n]), parents=parents,
                descendants={a:sorted(b for b in nodes if reach[a,b]) for a in nodes})


def run(source_dir, output_dir):
    source = Path(source_dir); out = Path(output_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())): raise ValueError('输出目录须不存在或为空')
    verified_files = verify(source)
    read = lambda name: json.loads((source/'04_FDNA拟合'/name).read_text(encoding='utf-8'))
    network = read('输入_拓扑.json'); side = read('衔接依据与审查.json')
    assert canonical_network(network) == canonical_network(read('workflow_report.json')['network_snapshot'])
    exported = sorted(c['id'] for c in side['candidates'] if c['exported'])
    shortcuts = sorted(c['id'] for c in side['candidates'] if c['id'].endswith('_shortcut'))
    assert len(exported) == 6 and len(shortcuts) == 3
    changes = [('remove_'+cid, [cid], []) for cid in exported]
    changes += [('add_'+cid, [], [cid]) for cid in shortcuts]
    changes += [('remove_all_exported', exported, [])]
    outputs = {'源拓扑.json':dumps(network), '源审计依据.json':dumps(side)}; rows = []
    original = oracle(network); checks = 0
    for cid, remove, add in changes:
        proposal = dict(id=cid, source_network_sha256=digest(canonical_network(network)), source_sidecar_sha256=digest(side),
            remove=[dict(candidate_id=n, rationale='只读删除影响审查，不作为新结构有效性的证据') for n in remove],
            add=[dict(candidate_id=n, rationale='验证间接可达捷径缺少产品消费时被阻断，不批准新增边') for n in add])
        result = preview(network, side, proposal); after = oracle(result['proposed_topology_preview'])
        assert result['roots_before'] == original['roots'] and result['roots_after'] == after['roots']
        assert result['added_roots'] == sorted(set(after['roots'])-set(original['roots']))
        assert result['requires_explicit_root_policy'] == result['added_roots']
        expected_affected = set(result['changed_receivers'])
        for entry in result['reachability']:
            n = entry['node']
            assert entry['descendants_before'] == original['descendants'][n]
            assert entry['descendants_after'] == after['descendants'][n]
            assert entry['incoming_before'] == original['parents'][n] and entry['incoming_after'] == after['parents'][n]
            expected_affected.update(original['descendants'][n]); expected_affected.update(after['descendants'][n]); checks += 1
        assert result['affected_nodes'] == sorted(expected_affected)
        assert result['original_evaluation_targets'] == sorted(n for n in network['nodes'] if original['parents'][n])
        assert result['local_equations_to_refit'] == sorted(n for n in result['changed_receivers'] if after['parents'][n])
        assert result['status'] == ('semantic_blocked' if add else 'review_required')
        assert all(not result['policies'][p] for p in ('topology_applied','mapping_approved','parameters_reused','fitted','numeric_effect_established'))
        prefix = '案例/'+cid+'/'
        outputs[prefix+'proposal.json'] = dumps(proposal); outputs[prefix+'preview_report.json'] = dumps(result)
        outputs[prefix+'结果说明.md'] = markdown(result)
        rows.append(dict(case=cid, status=result['status'], added_roots=result['added_roots'],
            affected_nodes=result['affected_nodes'], equations_to_refit=result['local_equations_to_refit'],
            equations_removed=result['local_equations_removed'], report=prefix+'结果说明.md'))
    audit = dict(status='passed', cases=len(rows), structural_review_required=sum(r['status']=='review_required' for r in rows),
        semantic_blocked=sum(r['status']=='semantic_blocked' for r in rows), independent_receiver_checks=checks,
        source_verified_files=verified_files, source_package_index_sha256=hashlib.sha256((source/'完整包校验.json').read_bytes()).hexdigest(),
        topology_applied=False, fitted=False, se_generated=False)
    lines = ['# 依赖边变更影响预览：06验证案例', '',
        '本步完成映射审计中的只读结构预览，不是T-Frozen/T-Refit实验，不改变正式图或参数。', '',
        '6个单边删除、3个无产品消费捷径及1个全边删除，共10个案例；7个保留待审、3个语义阻断。', '',
        '| 案例 | 状态 | 新增根 | 需重算节点 |', '| --- | --- | --- | --- |']
    for r in rows:
        lines.append(f"| [{r['case']}]({r['report']}) | {r['status']} | {', '.join(r['added_roots']) or '无'} | {', '.join(r['affected_nodes'])} |")
    lines += ['', '## 主要限制', '',
        '- 删除A1→B1后，B1成为新增根，B1与F1需重算；B1仍保留在原评价目标，不能直接读取其观测真值。',
        '- 删除B1→F1后，F1需另定无依赖预测或外部输入策略；不能把它移出接受节点误差表来制造改善。',
        '- 无产品消费的A→F捷径即使间接可达也被语义阻断，不批准导出。',
        '- 全部删边仍可审查结构，但现有FDNA拟合入口不接收空边图。',
        '- 独立传递闭包复核根、父节点和下游集合；全部案例保持正式源包、映射与参数不变。', '',
        '[结构复核JSON](案例复核.json) · [案例总表CSV](案例总表.csv)', '']
    outputs.update({'案例复核.json':dumps(audit),'案例总表.csv':csv_text(rows),'结果说明.md':'\n'.join(lines)})
    write_bundle(out, outputs); verify(source); print(dumps(audit)); return audit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='06依赖边只读预览案例与独立结构核验')
    parser.add_argument('--source-dir', type=Path, required=True); parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(); run(args.source_dir, args.output_dir)
