"""Explicit single-input scans of the base equations, not original figure data."""
from copy import deepcopy
import csv
import hashlib
import io
import json
import math

from .engine import MODEL, evaluate
from .model import ModelError, inspect_model


def validate_protocol(model, protocol):
    structure = inspect_model(model)
    fields = {'id', 'basis', 'model', 'baseline', 'drivers', 'observers', 'values'}
    if not isinstance(protocol, dict) or set(protocol) != fields:
        raise ModelError('扫描约定须包含id、basis、model、baseline、drivers、observers、values')
    if any(not isinstance(protocol[k], str) or not protocol[k].strip() for k in ('id', 'basis')):
        raise ModelError('扫描须说明名称与假设依据')
    if protocol['model'] != MODEL:
        raise ModelError('扫描仅支持明确选用基础式(1)—(2)')
    for key, allowed in (('drivers', set(structure['exogenous_nodes'])),
                         ('observers', {n['id'] for n in model['nodes']})):
        items = protocol[key]
        if (not isinstance(items, list) or not items or
                any(not isinstance(n, str) or n not in allowed for n in items) or
                len(items) != len(set(items))):
            raise ModelError('扫描节点须存在且不重复；只可改变无入边节点')
    grid = protocol['values']
    if (not isinstance(grid, list) or not 2 <= len(grid) <= 1001 or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or
                not 0 <= v <= 100 or not math.isfinite(v) for v in grid) or
            any(a >= b for a, b in zip(grid, grid[1:]))):
        raise ModelError('扫描值须为[0,100]内严格递增的2至1001个有限数值')
    base_scenario = {'id': protocol['id'] + ':baseline', 'basis': protocol['basis'],
                     'node_values': deepcopy(protocol['baseline'])}
    baseline = evaluate(model, base_scenario)
    if baseline['status'] != 'complete':
        raise ModelError('扫描参照须给齐六类无入边节点的效能，且依赖参数已知')
    return baseline


def sweep(model, protocol):
    baseline = validate_protocol(model, protocol)
    base_scenario = baseline['scenario']
    grid = protocol['values']
    base_values = {n['id']: n['value'] for n in baseline['nodes']}
    rows, traces = [], []
    for driver in protocol['drivers']:
        for index, value in enumerate(grid):
            scenario = deepcopy(base_scenario)
            scenario['id'] = f"{protocol['id']}:{driver}:{index}"
            scenario['node_values'][driver] = value
            result = evaluate(model, scenario)
            traces.append({'driver': driver, 'input': value, 'scenario': scenario,
                           'input_sha256': result['input_sha256'], 'nodes': result['nodes']})
            by_id = {n['id']: n for n in result['nodes']}
            for observer in protocol['observers']:
                node = by_id[observer]
                rows.append({'driver': driver, 'input': value, 'observer': observer,
                             'value': node['value'], 'baseline_value': base_values[observer],
                             'loss': base_values[observer] - node['value'],
                             'sod_mean': node['sod_mean'], 'cod_min': node['cod_min'],
                             'binding_constraints': node['binding_constraints']})
    snapshot = {'case': model, 'protocol': protocol, 'model': MODEL}
    return {
        'format_version': '1.0', 'status': 'complete', 'model': MODEL, 'case': model['id'],
        'scope': '基础式逐项扫描；其余无入边节点固定于参照，中间节点逐点重算。'
                 '取值与原图t的映射未核实，不属于图8—9的逐点复现。',
        'input_sha256': hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                                 allow_nan=False).encode('utf-8')).hexdigest(),
        'protocol': deepcopy(protocol), 'policies': deepcopy(baseline['policies']),
        'baseline': baseline, 'evaluations': len(traces), 'rows': rows, 'traces': traces,
        'case_reproduction_ready': False, 'pending': deepcopy(model['gaps']),
        'source_snapshot': deepcopy(model['sources']),
        'not_covered': ['图8中S5、S6的独立变化曲线：尚未指定中间节点变化方式',
                        'δ与原始拟合、残差及伪R²的重新核验'],
    }


def csv_report(report):
    stream = io.StringIO(newline='')
    fields = ['driver', 'input', 'observer', 'value', 'baseline_value', 'loss',
              'sod_mean', 'cod_min', 'binding_constraints']
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in report['rows']:
        writer.writerow({**row, 'binding_constraints': '|'.join(row['binding_constraints'])})
    return stream.getvalue()


def render_sweep_summary(report):
    protocol = report['protocol']
    lines = ['# 奥术网基础式逐项扫描', '', report['scope'], '', protocol['basis'], '',
             f"共{report['evaluations']}次求值，{len(report['rows'])}条观察记录。", '',
             '下表仅列扫描首末点；完整曲线见CSV，逐边计算依据见JSON。', '',
             '| 改变节点 | 观察节点 | 首点给定值 | 首点效能 | 末点给定值 | 末点效能 |',
             '| --- | --- | ---: | ---: | ---: | ---: |']
    for driver in protocol['drivers']:
        for observer in protocol['observers']:
            rows = [r for r in report['rows'] if r['driver'] == driver and r['observer'] == observer]
            first, last = rows[0], rows[-1]
            lines.append(f"| {driver} | {observer} | {first['input']:.10g} | {first['value']:.10g} | "
                         f"{last['input']:.10g} | {last['value']:.10g} |")
    lines += ['', '尚未覆盖：', ''] + [f'- {item}' for item in report['not_covered']]
    return '\n'.join(lines) + '\n'


def plot_sweep(report):
    """Optional matplotlib artifact; keep plotting out of numerical dependencies."""
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
    except ImportError as exc:
        raise ModelError('绘图需要matplotlib；可不加--plot先导出数值') from exc
    observers = report['protocol']['observers']
    fig = Figure(figsize=(12, 3.4 * ((len(observers) + 1) // 2)), layout='constrained')
    FigureCanvasAgg(fig)
    axes = fig.subplots((len(observers) + 1) // 2, 2, squeeze=False).ravel()
    for axis, observer in zip(axes, observers):
        for driver in report['protocol']['drivers']:
            rows = [r for r in report['rows'] if r['driver'] == driver and r['observer'] == observer]
            axis.plot([r['input'] for r in rows], [r['value'] for r in rows], label=driver)
        axis.set(title=observer, xlabel='Assigned input operability (not original t)',
                 ylabel='Computed operability', xlim=(0, 100), ylim=(0, 105))
        axis.grid(alpha=0.25)
        axis.legend(ncol=3, fontsize=8)
    for axis in axes[len(observers):]:
        axis.set_visible(False)
    fig.suptitle('Base equations only - assumed one-input scan - not a reproduction of Figures 8-9')
    stream = io.BytesIO()
    fig.savefig(stream, format='png', dpi=150)
    return stream.getvalue()
