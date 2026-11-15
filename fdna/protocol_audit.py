"""Finite, explicit scan hypotheses against uncertain figure readings; no fitting."""
from copy import deepcopy

from .engine import evaluate
from .figure_compare import compare_readings
from .model import ModelError
from .precision import rounding_models
from .structure import digest


CANDIDATES = (
    {'id': 'single_propagated', 'driver_distinguished': True,
     'assignment': '固定图例节点SE=t，其余无入边节点100，下游重算；中间节点暂停入边方程'},
    {'id': 'single_direct_local', 'driver_distinguished': True,
     'assignment': '接受节点的直接供给节点固定100，再将图例节点固定t；间接路径被固定的直接供给节点截断'},
    {'id': 'synchronous_leaves', 'driver_distinguished': False,
     'assignment': '全部无入边节点SE=t，中间节点重算；图例节点标识不参与赋值'},
    {'id': 'synchronous_direct_local', 'driver_distinguished': False,
     'assignment': '接受节点的全部直接供给节点SE=t，接受节点重算；图例节点标识不参与赋值'},
)


def _prediction(model, baseline, candidate, driver, observer, value):
    scenario = {'id': 'protocol-audit', 'basis': candidate['assignment'], 'node_values': deepcopy(baseline)}
    incoming = [e['feeder'] for e in model['edges'] if e['receiver'] == observer]
    kind = candidate['id']
    if kind == 'synchronous_leaves':
        scenario['node_values'] = {n: value for n in baseline}
        result = evaluate(model, scenario)
    else:
        forced = ({n: value if kind == 'synchronous_direct_local' else 100 for n in incoming}
                  if kind != 'single_propagated' else {})
        if kind != 'synchronous_direct_local':
            forced[driver] = value
        result = evaluate(model, scenario, intervention={'basis': candidate['assignment'], 'node_values': forced})
    return next(n['value'] for n in result['nodes'] if n['id'] == observer)


def _gap(predicted, read):
    return max(0, read[0]-predicted[1], predicted[0]-read[1])


def audit_protocols(model, protocol, readings):
    # Reuse the complete source/pixel/axis validation and original calibration.
    original = compare_readings(model, protocol, readings)
    if any(value != 100 for value in protocol['baseline'].values()):
        raise ModelError('协议对照的共同参照须为全部无入边节点100')
    points = original['comparisons']
    receivers = {e['receiver'] for e in model['edges']}
    if any(p['observer'] not in receivers or p['observer'] == p['driver'] for p in points):
        raise ModelError('协议对照须观察接受节点，且观察节点与变化节点不同')
    lower, upper, bounds = rounding_models(model)
    candidates = []
    for candidate in CANDIDATES:
        comparisons = []
        for index, point in enumerate(points):
            t = point['axis_t']
            lo, hi = max(0, t-point['t_error']), min(100, t+point['t_error'])
            args = (protocol['baseline'], candidate, point['driver'], point['observer'])
            center = _prediction(model, *args, t)
            nominal = [_prediction(model, *args, lo), _prediction(model, *args, hi)]
            rounded = [_prediction(lower, *args, lo), _prediction(upper, *args, hi)]
            if not rounded[0]-1e-9 <= nominal[0] <= center <= nominal[1] <= rounded[1]+1e-9:
                raise ModelError('候选预测不满足基础式的单调包络')
            read = point['read_interval']
            gap, rounded_gap = _gap(nominal, read), _gap(rounded, read)
            comparisons.append({
                'point_index': index, 'panel': point['panel'], 'driver': point['driver'], 'observer': point['observer'],
                'axis_t': t, 't_interval': [lo, hi], 'read_value': point['read_value'], 'read_interval': deepcopy(read),
                'nominal_prediction': center, 'nominal_interval': nominal, 'rounding_interval': rounded,
                'nominal_status': 'overlap' if gap == 0 else 'separated',
                'rounding_status': 'overlap' if rounded_gap == 0 else 'separated',
                'nominal_interval_gap': gap, 'rounding_interval_gap': rounded_gap,
            })
        def counts(rows, key):
            return {status: sum(r[key] == status for r in rows) for status in ('overlap', 'separated')}
        candidates.append({**deepcopy(candidate), 'comparisons': comparisons,
                           'nominal_counts': counts(comparisons, 'nominal_status'),
                           'rounding_counts': counts(comparisons, 'rounding_status'),
                           'nominal_coverage': sum(r['nominal_status'] == 'overlap' for r in comparisons)/len(comparisons),
                           'rounding_coverage': sum(r['rounding_status'] == 'overlap' for r in comparisons)/len(comparisons),
                           'rounding_separated_point_indices': [r['point_index'] for r in comparisons if r['rounding_status'] == 'separated'],
                           'by_panel': {panel: {
                               'nominal_counts': counts([r for r in comparisons if r['panel'] == panel], 'nominal_status'),
                               'rounding_counts': counts([r for r in comparisons if r['panel'] == panel], 'rounding_status')}
                               for panel in sorted({p['panel'] for p in comparisons})}})
    remaining = [p for p in candidates[0]['comparisons'] if p['rounding_status'] == 'separated']
    return {
        'format_version': '1.0', 'status': 'complete', 'case': model['id'],
        'scope': '图8—9有限候选协议对照，逐点保留读图误差与表7舍入范围；不拟合参数。',
        'case_reproduction_ready': False,
        'input_sha256': digest({'case': model, 'protocol': protocol, 'readings': readings,
                                'candidates': CANDIDATES, 'alpha_half_unit': .005, 'beta_half_unit': .5}),
        'assumptions': {'time_to_se': 'candidate_identity_unverified', 'delta': 'excluded',
                        'other_exogenous_SE': 100, 'rounding': 'nearest_reported_unit_closed_independent_box',
                        'alpha_half_unit': .005, 'beta_half_unit': .5, 'clipping': 'none',
                        'clipping_equivalence': '基础式与给定输入均在[0,100]内，此处加裁剪不会改变结果'},
        'summary': {'points': len(points), 'candidate_count': len(candidates),
                    'single_propagated_nominal_overlap': candidates[0]['nominal_counts']['overlap'],
                    'single_propagated_rounding_overlap': candidates[0]['rounding_counts']['overlap'],
                    'single_propagated_still_separated': len(remaining)},
        'candidate_results': candidates, 'parameter_bounds': bounds,
        'protocol_snapshot': deepcopy(protocol), 'readings_snapshot': deepcopy(readings),
        'source_snapshot': deepcopy(model['sources']),
        'limits': ['覆盖率仅指所选读图点的区间相交比例，不是拟合优度或统计置信度',
                   '每点的区间相交是必要相容性检查，不保证同一套参数可同时覆盖所有点',
                   '同步候选忽略图例节点标识，无法解释同一面板内不同节点曲线的差别，不能仅凭覆盖率选定原文协议',
                   '舍入后仍分离表示当前候选下，读图误差与报告精度不足以解释差异；不据此判定原图有误',
                   '未枚举未知初始SE、未知时间映射、共享或逐节点δ；没有用读图点反求这些未知项',
                   '局部候选是显式固定供给节点的隔离比较，不声称等同完整网络的自然传播',
                   '图5主体交互不逐条等同图6数值依赖；目标归属沿用已确认的表格映射'],
    }
