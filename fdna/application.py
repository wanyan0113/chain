"""Conditional application analysis, without inventing a chain-level SE formula."""
from collections import deque
from copy import deepcopy
import csv
import io

from .model import ModelError
from .structure import digest
from .sweep import sweep


def _shortest_path(model, source, target):
    queue = deque([[source]])
    seen = {source}
    while queue:
        path = queue.popleft()
        if path[-1] == target:
            return path
        for edge in model['edges']:
            if edge['feeder'] == path[-1] and edge['receiver'] not in seen:
                seen.add(edge['receiver'])
                queue.append(path+[edge['receiver']])
    return None


def analyze_application(model, protocol):
    scan = sweep(model, protocol)
    if any(v != 100 for v in protocol['baseline'].values()) or protocol['values'][0] != 0 or protocol['values'][-1] != 100:
        raise ModelError('应用比较须以全部无入边节点100为参照，扫描覆盖0与100')
    tolerance = 1e-9
    incoming = {n['id']: [] for n in model['nodes']}
    for edge in model['edges']:
        incoming[edge['receiver']].append(edge)
    parameter_comparisons = []
    for receiver, edges in incoming.items():
        if not edges:
            continue
        parameter_comparisons.append({
            'receiver': receiver,
            'sod_sensitivity_descending': [
                {**deepcopy(e), 'sod_partial_derivative': e['alpha']/len(edges)}
                for e in sorted(edges, key=lambda e: -e['alpha'])],
            'cod_offset_ascending': sorted(deepcopy(edges), key=lambda e: e['beta']),
            'meaning': '同一接受节点内比较；α较大时SoD对供给效能变化的斜率较大，β较小时CoD限制较紧；均不保证对应约束实际起作用。',
        })
    # Target attribution follows the final member group of table4, without
    # turning a terminal node's SE into target achievement or chain closure.
    def flatten(value):
        if isinstance(value, list):
            return [n for item in value for n in flatten(item)]
        return [value]
    terminal_links = [
        {'chain': c['id'], 'target': c['target'], 'terminal_nodes': flatten(c['members'][-1]),
         'source_refs': deepcopy(c['source_refs']), 'basis': '表4末组能力归属，仅作索引，不聚合效能'}
        for c in model['chains']]
    comparisons = []
    for observer in protocol['observers']:
        for driver in protocol['drivers']:
            path = _shortest_path(model, driver, observer)
            direct = next((e for e in incoming[observer] if e['feeder'] == driver), None)
            rows = [r for r in scan['rows'] if r['driver'] == driver and r['observer'] == observer]
            comparisons.append({
                'driver': driver, 'observer': observer,
                'relation': 'self' if driver == observer else 'direct' if direct else 'indirect' if path else 'no_path',
                'included_in_ranking': driver != observer,
                'shortest_dependency_path': path,
                'shortest_path_edges': [deepcopy(next(e for e in model['edges'] if
                    e['feeder'] == a and e['receiver'] == b)) for a,b in zip(path or [], (path or [])[1:])],
                'direct_edge': deepcopy(direct),
                'baseline_value': rows[0]['baseline_value'],
                'value_at_input_0': rows[0]['value'], 'loss_at_input_0': rows[0]['loss'],
                'value_at_input_100': rows[-1]['value'],
                'nondecreasing_on_grid': all(a['value'] <= b['value']+tolerance for a,b in zip(rows,rows[1:])),
                'binding_constraint_grid_counts': {
                    key: sum(key in r['binding_constraints'] for r in rows)
                    for key in sorted({k for r in rows for k in r['binding_constraints']})},
                'target_links': [deepcopy(link) for link in terminal_links if observer in link['terminal_nodes']],
            })
    rankings = []
    for observer in protocol['observers']:
        for value in protocol['values']:
            rows = [r for r in scan['rows'] if r['observer'] == observer and r['input'] == value and r['driver'] != observer]
            entries = []
            for row in sorted(rows, key=lambda r: -r['loss']):
                score = row['loss']
                entries.append({'driver': row['driver'], 'value': row['value'], 'loss': score,
                                'rank_min': 1+sum(r['loss'] > score+tolerance for r in rows),
                                'rank_max': sum(r['loss'] >= score-tolerance for r in rows)})
            rankings.append({'observer': observer, 'input': value, 'entries': entries})
    return {
        'format_version': '1.0', 'status': 'complete', 'case': model['id'],
        'scope': '第5.4.2节应用方向的条件分析：比较表7参数、指定扫描输入下的接受节点效能下降及传播路径；下降量指标为本实现的显式约定。',
        'case_reproduction_ready': False,
        'input_sha256': digest({'case': model, 'protocol': protocol, 'metric': 'baseline_minus_observer_se_at_same_driver_input', 'tie_tolerance': tolerance}),
        'metric': {'formula': 'loss = baseline_observer_SE - scanned_observer_SE',
                   'larger_means': '同一接受节点、相同输入效能条件下，下降量较大',
                   'tie_tolerance': tolerance, 'self_interventions_ranked': False,
                   'rank_ranges_meaning': '仅表示数值并列，不是参数不确定性下的排名区间'},
        'parameter_comparisons': parameter_comparisons, 'comparisons': comparisons,
        'rankings': rankings, 'terminal_links': terminal_links,
        'sweep_snapshot': scan, 'source_snapshot': deepcopy(model['sources']),
        'policies': {**deepcopy(scan['policies']), 'cross_observer_aggregation': 'none',
                     'chain_closure_inferred_from_se': False, 'parameter_update_to_case': False},
        'limits': ['排序依赖扫描约定及输入效能；不同条件下可变化，不据此给出统一重要度',
                   '图8—9时间映射及δ仍未确定，本结果不是原文曲线逐点复现',
                   '依赖路径表示计算传播，不直接表示传讯关系；最短路径不是唯一影响路径',
                   '接受节点SE不等于目标达成率、法术链闭合概率或体系效能；未补造串并联聚合公式',
                   '参数报告精度扰动与排名稳健性尚未由本分析检验'],
    }


def application_csv(report):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=['observer','input','driver','value','loss','rank_min','rank_max'])
    writer.writeheader()
    for group in report['rankings']:
        for entry in group['entries']:
            writer.writerow({'observer': group['observer'], 'input': group['input'], **entry})
    return stream.getvalue()
