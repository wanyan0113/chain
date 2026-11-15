"""Reporting-rounding bounds and coherent ranking-reversal witnesses."""
from copy import deepcopy
from itertools import combinations
from math import fsum

from .model import ModelError, inspect_model
from .structure import digest
from .sweep import sweep


TOLERANCE = 1e-9


def _loss_grid(model, protocol):
    """Validated numeric-only witness scan, without thousands of trace copies.

    Cross-checked against every engine row for nominal and both global corners.
    Only called after sweep has validated the protocol and complete parameters.
    """
    order = inspect_model(model)['topological_order']
    incoming = {n: [] for n in order}
    for edge in model['edges']:
        incoming[edge['receiver']].append(edge)
    losses = {}
    for driver in protocol['drivers']:
        for value in protocol['values']:
            values = dict(protocol['baseline'])
            values[driver] = value
            for node in order:
                if node == driver or not incoming[node]:
                    continue
                edges = incoming[node]
                sod = fsum(100-e['alpha']*(100-values[e['feeder']]) for e in edges)/len(edges)
                cod = min(values[e['feeder']]+e['beta'] for e in edges)
                values[node] = min(sod, cod)
            for observer in protocol['observers']:
                losses[observer, value, driver] = 100-values[observer]
    return losses


def check_precision(model, protocol):
    """Exact output envelopes; conservative ranks; finite, shared-parameter witnesses.

    On this base DAG, SE is increasing in feeders and beta, decreasing in alpha.
    Thus two global corners attain output extrema, including through propagation.
    Differences between two scenarios need not share this monotonicity: overlapping
    envelopes cannot establish a reversal, or an exact attainable rank range.
    """
    nominal = sweep(model, protocol)
    if (any(v != 100 for v in protocol['baseline'].values()) or
            protocol['values'][0] != 0 or protocol['values'][-1] != 100):
        raise ModelError('精度检查须以全部无入边节点100为参照，扫描覆盖0与100')
    bounds = []
    for edge in model['edges']:
        bounds.append({'feeder': edge['feeder'], 'receiver': edge['receiver'],
                       **{p: {'reported': edge[p], 'lower': max(0, edge[p]-half),
                              'upper': min(cap, edge[p]+half)}
                          for p, half, cap in [('alpha', .005, 1), ('beta', .5, 100)]}})

    def corner(output_lower):
        candidate = deepcopy(model)
        for edge, bound in zip(candidate['edges'], bounds):
            edge['alpha'] = bound['alpha']['upper' if output_lower else 'lower']
            edge['beta'] = bound['beta']['lower' if output_lower else 'upper']
        return candidate

    lower_model, upper_model = corner(True), corner(False)
    low_scan, high_scan = sweep(lower_model, protocol), sweep(upper_model, protocol)
    for candidate, scan in ((model, nominal), (lower_model, low_scan), (upper_model, high_scan)):
        numeric = _loss_grid(candidate, protocol)
        if any(abs(numeric[r['observer'], r['input'], r['driver']]-r['loss']) > TOLERANCE for r in scan['rows']):
            raise ModelError('精度搜索数值求值与逐边引擎不一致')
    rows = []
    grouped = {}
    for mid, low, high in zip(nominal['rows'], low_scan['rows'], high_scan['rows']):
        row = {'driver': mid['driver'], 'observer': mid['observer'], 'input': mid['input'],
               'nominal_value': mid['value'], 'value_lower': low['value'], 'value_upper': high['value'],
               'nominal_loss': mid['loss'], 'loss_lower': 100-high['value'], 'loss_upper': 100-low['value']}
        if not row['value_lower']-TOLERANCE <= row['nominal_value'] <= row['value_upper']+TOLERANCE:
            raise ModelError('名义输出不在精度包络内')
        rows.append(row)
        if mid['driver'] != mid['observer']:
            grouped.setdefault((mid['observer'], mid['input']), []).append(row)
    rankings, ambiguous = [], {}
    for (observer, value), entries in grouped.items():
        entries = sorted(entries, key=lambda r: -r['nominal_loss'])
        ranks = []
        for entry in entries:
            others = [r for r in entries if r is not entry]
            ranks.append({'driver': entry['driver'],
                          'conservative_rank_min': 1+sum(r['loss_lower'] > entry['loss_upper']+TOLERANCE for r in others),
                          'conservative_rank_max': len(entries)-sum(r['loss_upper'] < entry['loss_lower']-TOLERANCE for r in others)})
        pairs = []
        for a, b in combinations(entries, 2):
            gap = a['nominal_loss']-b['nominal_loss']
            if a['loss_lower'] > b['loss_upper']+TOLERANCE:
                status = 'certified_a_above_b'
            elif b['loss_lower'] > a['loss_upper']+TOLERANCE:
                status = 'certified_b_above_a'
            elif (max(a['loss_upper'], b['loss_upper'])-min(a['loss_lower'], b['loss_lower']) <= TOLERANCE):
                status = 'certified_tie_within_tolerance'
            else:
                status = 'not_certified'
            pair = {'a': a['driver'], 'b': b['driver'], 'status': status, 'nominal_loss_gap_a_minus_b': gap}
            pairs.append(pair)
            if status == 'not_certified':
                ambiguous[(observer, value, a['driver'], b['driver'])] = pair
        rankings.append({'observer': observer, 'input': value, 'entries': ranks, 'pairs': pairs,
                         'unique_top_certified': bool(entries) and all(entries[0]['loss_lower'] > e['loss_upper']+TOLERANCE for e in entries[1:])})

    # No random sampling. These settings are witnesses only, not an exhaustive
    # search of the 36-dimensional box. Both compared drivers share every parameter.
    settings, witnesses = [], []
    def check_setting(setting_id, candidate, scan=None):
        losses = (_loss_grid(candidate, protocol) if scan is None else
                  {(r['observer'], r['input'], r['driver']): r['loss'] for r in scan['rows']})
        parameters = [{k: edge[k] for k in ('feeder', 'receiver', 'alpha', 'beta')} for edge in candidate['edges']]
        settings.append({'id': setting_id, 'parameters': parameters})
        for (observer, value, a, b), pair in ambiguous.items():
            gap = losses[observer, value, a]-losses[observer, value, b]
            original = pair['nominal_loss_gap_a_minus_b']
            reversal = abs(original) > TOLERANCE and abs(gap) > TOLERANCE and original*gap < 0
            split_tie = abs(original) <= TOLERANCE and abs(gap) > TOLERANCE
            key = 'reversal_witness' if reversal else 'tie_split_witness' if split_tie else None
            if key and key not in pair:
                pair[key] = len(witnesses)
                witnesses.append({'setting_id': setting_id, 'observer': observer, 'input': value,
                                  'a': a, 'b': b, 'kind': 'strict_reversal' if reversal else 'nominal_tie_split',
                                  'nominal_loss_gap_a_minus_b': original,
                                  'loss_a': losses[observer, value, a], 'loss_b': losses[observer, value, b],
                                  'loss_gap_a_minus_b': gap})
    check_setting('all_output_lower', lower_model, low_scan)
    check_setting('all_output_upper', upper_model, high_scan)
    for index, bound in enumerate(bounds):
        for parameter in ('alpha', 'beta'):
            for side in ('lower', 'upper'):
                candidate = deepcopy(model)
                candidate['edges'][index][parameter] = bound[parameter][side]
                check_setting(f'edge_{index}_{parameter}_{side}', candidate)
    reversal_pairs = sum('reversal_witness' in p for p in ambiguous.values())
    return {
        'format_version': '1.0', 'status': 'complete', 'case': model['id'],
        'scope': '表7报告精度下的基础式条件敏感性；不改变案例参数，不重拟合。',
        'case_reproduction_ready': False,
        'input_sha256': digest({'case': model, 'protocol': protocol, 'alpha_half_unit': .005,
                                'beta_half_unit': .5, 'tolerance': TOLERANCE,
                                'witness_search': 'two_global_corners_and_each_coordinate_endpoint'}),
        'assumptions': {'rounding': 'nearest_reported_unit', 'alpha_half_unit': .005, 'beta_half_unit': .5,
                        'bounds': 'closed_outer_box_intersect_physical_ranges',
                        'parameter_dependence': 'independent_box_assumption_not_inferred_from_data',
                        'baseline': 'all_exogenous_SE_100', 'delta': 'excluded', 'clipping': 'none',
                        'tie_tolerance': TOLERANCE, 'self_interventions_ranked': False},
        'summary': {'output_rows': len(rows), 'ranking_groups': len(rankings),
                    'max_output_interval_width': max(r['value_upper']-r['value_lower'] for r in rows),
                    'uncertified_pairs': len(ambiguous), 'witnessed_reversal_pairs': reversal_pairs,
                    'witnessed_tie_split_pairs': sum('tie_split_witness' in p for p in ambiguous.values()),
                    'tested_parameter_settings': len(settings)},
        'parameter_bounds': bounds, 'rows': rows, 'rankings': rankings,
        'witnesses': witnesses, 'tested_settings': settings, 'protocol_snapshot': deepcopy(protocol),
        'source_snapshot': deepcopy(model['sources']),
        'limits': ['这是舍入假设下的范围，不是统计置信区间，也不能确认参数估计正确',
                   '输出端点在独立闭参数盒内可达；排名区间为保守外包络，不保证每个名次可达',
                   '区间重叠仅表示顺序未获认证；实际翻转须有同一参数设置下的见证',
                   '有限端点搜索未发现翻转不证明稳定；见证仅覆盖给定扫描网格',
                   '未知δ、原图时间映射、参数相关性及原始样本误差均未纳入'],
    }
