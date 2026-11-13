"""Necessary-condition diagnostics; never estimates or inserts original delta."""


def diagnose_comparisons(model, comparisons):
    """Consume the validated model and comparison rows from compare_readings."""
    incoming = {n['id']: [] for n in model['nodes']}
    for edge in model['edges']:
        incoming[edge['receiver']].append(edge)
    exogenous = {node for node, edges in incoming.items() if not edges}
    edge_bounds = []
    for point in comparisons:
        edge = next((e for e in incoming[point['observer']] if e['feeder'] == point['driver']), None)
        if edge is None:
            continue
        # Every receiver satisfies O_j <= O_i + beta_ij in the base equations,
        # regardless of the other feeder values or their correlation.
        input_high = min(100, point['axis_t'] + point['t_error'])
        bound = input_high + edge['beta']
        gap = point['read_interval'][0] - bound
        edge_bounds.append({
            'panel': point['panel'], 'driver': point['driver'], 'observer': point['observer'],
            'axis_t': point['axis_t'], 'beta': edge['beta'], 'source_refs': list(edge['source_refs']),
            'input_upper': input_high, 'cod_upper': bound, 'read_lower': point['read_interval'][0],
            'status': 'exceeds_base_bound' if gap > 0 else 'not_excluded',
            'minimum_positive_offset_to_avoid_this_bound': max(0, gap),
        })
    delta_tests = []
    for node, edges in incoming.items():
        # Only these receivers have no upstream delta to confound the calculation.
        if not edges or any(e['feeder'] not in exogenous for e in edges):
            continue
        points = [p for p in comparisons if p['observer'] == node]
        if len(points) < 2:
            continue
        intervals = []
        for point in points:
            predicted_low, predicted_high = point['predicted_interval']
            read_low, read_high = point['read_interval']
            intervals.append({'panel': point['panel'], 'driver': point['driver'],
                              'axis_t': point['axis_t'],
                              'required_offset': [read_low-predicted_high, read_high-predicted_low]})
        largest_lower = max(intervals, key=lambda p: p['required_offset'][0])
        smallest_upper = min(intervals, key=lambda p: p['required_offset'][1])
        lower, upper = largest_lower['required_offset'][0], smallest_upper['required_offset'][1]
        incompatible = lower > upper
        delta_tests.append({
            'receiver': node, 'points': len(points), 'status': 'incompatible' if incompatible else 'not_excluded',
            'intersection': None if incompatible else [lower, upper],
            'required_lower': lower, 'required_upper': upper,
            'witnesses': [largest_lower, smallest_upper], 'point_intervals': intervals,
        })
    return {
        'scope': '条件诊断，不求取或写入原文δ。仍采用给定效能=原图t的待核实对应。',
        'cod_bound_meaning': '若读图区间下界超过给定节点上界加β，则在无δ的基础式中，'
                             '即使其他供给节点改取[0,100]内任意值，也不能达到该读数。',
        'constant_delta_meaning': '仅对全部供给节点均无入边的接受节点检查单个固定加项。'
                                 '其余无入边节点仍按参照固定；取各点所需加项区间的交集。'
                                 '空交集排除该条件下的固定δ解释；非空不代表原文δ已求得。',
        'edge_bounds': edge_bounds, 'constant_delta_tests': delta_tests,
        'counts': {'exceeds_base_bound': sum(p['status'] == 'exceeds_base_bound' for p in edge_bounds),
                   'incompatible_fixed_delta_receivers': sum(
                       p['status'] == 'incompatible' for p in delta_tests)},
        'limits': ['不排除t对应方式、变化条件、图文参数或其他尚未核实因素的差异',
                   '未检验随取值变化的δ，也未把读图值拟合为原文参数',
                   'F7、F9、F11有中间供给节点，不能据此排除它们与上游δ共同变化的解释'],
    }
