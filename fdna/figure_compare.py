"""Conditional checks against uncertain raster readings, never fitted samples."""
from copy import deepcopy
import hashlib
import io
import json
import math

from .model import ModelError
from .sweep import sweep, validate_protocol


def _numbers(value, count):
    return (isinstance(value, list) and len(value) == count and
            all(not isinstance(v, bool) and isinstance(v, (int, float)) and
                abs(v) <= 10**9 and math.isfinite(v) for v in value))


def compare_readings(model, protocol, readings):
    baseline_check = validate_protocol(model, protocol)
    if (not isinstance(readings, dict) or set(readings) !=
            {'id', 'data_kind', 'source_sha256', 'method', 'axis_mapping', 'panels', 'observations'}):
        raise ModelError('读图资料须登记来源、方法、坐标标定与观察点')
    if readings['data_kind'] != 'uncertain_raster_readings':
        raise ModelError('仅接纳带误差的读图资料，不能标作原始推演记录')
    if any(not isinstance(readings[k], str) or not readings[k].strip() for k in ('id', 'method')):
        raise ModelError('读图资料须说明名称与方法')
    if (not isinstance(readings['source_sha256'], str) or
            readings['source_sha256'] not in {s['sha256'] for s in model['sources']}):
        raise ModelError('读图来源指纹须与案例来源对应')
    mapping = readings['axis_mapping']
    if (not isinstance(mapping, dict) or set(mapping) != {'kind', 'basis'} or
            mapping['kind'] != 'candidate_identity' or not isinstance(mapping['basis'], str) or
            not mapping['basis'].strip()):
        raise ModelError('须明确登记待核实的t与给定效能对应假设')
    panels = readings['panels']
    if not isinstance(panels, list) or not panels:
        raise ModelError('须登记读图面板')
    by_id = {}
    node_ids = {n['id'] for n in model['nodes']}
    for panel in panels:
        if (not isinstance(panel, dict) or set(panel) != {'id', 'observer', 'pdf_page', 'image_xref',
                'image_sha256', 'size', 'x_axis', 'y_axis', 'legend'} or
                not isinstance(panel['id'], str) or not panel['id'] or panel['id'] in by_id):
            raise ModelError('面板标识及坐标资料无效或重复')
        if (not isinstance(panel['image_sha256'], str) or len(panel['image_sha256']) != 64 or
                any(c not in '0123456789abcdef' for c in panel['image_sha256']) or
                any(isinstance(panel[k], bool) or not isinstance(panel[k], int) or panel[k] < 1
                    for k in ('pdf_page', 'image_xref')) or
                not _numbers(panel['size'], 2) or min(panel['size']) <= 0):
            raise ModelError('面板须登记有效的图像位置、尺寸与指纹')
        if panel['observer'] not in protocol['observers']:
            raise ModelError('面板观察节点未包含在扫描约定中')
        if (not isinstance(panel['legend'], list) or not panel['legend'] or
                any(not isinstance(n, str) or n not in node_ids for n in panel['legend']) or
                len(panel['legend']) != len(set(panel['legend']))):
            raise ModelError('面板须保留不重复的图例节点')
        for axis, limit in (('x_axis', panel['size'][0]), ('y_axis', panel['size'][1])):
            anchors = panel[axis]
            if (not isinstance(anchors, list) or len(anchors) != 2 or
                    any(not _numbers(a, 2) or not 0 <= a[0] < limit or not 0 <= a[1] <= 100
                        for a in anchors) or anchors[0][0] == anchors[1][0] or
                    anchors[0][1] == anchors[1][1]):
                raise ModelError('坐标标定须给出两个不同像素及对应刻度')
        by_id[panel['id']] = panel
    observations = readings['observations']
    if not isinstance(observations, list) or not observations:
        raise ModelError('读图观察点不可为空')
    comparisons, keys = [], set()
    for observation in observations:
        if (not isinstance(observation, dict) or set(observation) !=
                {'panel', 'driver', 'pixel', 'pixel_error'} or
                not isinstance(observation['panel'], str) or observation['panel'] not in by_id):
            raise ModelError('观察点须引用已登记面板')
        panel = by_id[observation['panel']]
        driver = observation['driver']
        if not isinstance(driver, str) or driver not in panel['legend'] or driver not in protocol['drivers']:
            raise ModelError('读图对照只支持已登记图例中的无入边变化节点')
        pixel, error = observation['pixel'], observation['pixel_error']
        if (not _numbers(pixel, 2) or not _numbers(error, 2) or min(error) <= 0 or
                any(not 0 <= v < bound for v, bound in zip(pixel, panel['size']))):
            raise ModelError('读图像素须在图像内，并给出正的误差界限')
        key = observation['panel'], driver, tuple(pixel)
        if key in keys:
            raise ModelError('观察点不可重复')
        keys.add(key)

        def convert(axis, coordinate):
            (p0, v0), (p1, v1) = panel[axis]
            return v0 + (coordinate - p0) * (v1 - v0) / (p1 - p0)

        x, y = convert('x_axis', pixel[0]), convert('y_axis', pixel[1])
        dx = abs(convert('x_axis', pixel[0] + error[0]) - x)
        dy = abs(convert('y_axis', pixel[1] + error[1]) - y)
        if not 0 <= x <= 100 or not 0 <= y <= 100:
            raise ModelError('标定后的观察点须在[0,100]内')
        local = deepcopy(protocol)
        local.update(drivers=[driver], observers=[panel['observer']],
                     values=sorted({max(0, x-dx), x, min(100, x+dx)}))
        predicted = sweep(model, local)
        rows = predicted['rows']
        center = next(row for row in rows if row['input'] == x)
        low, high = min(r['value'] for r in rows), max(r['value'] for r in rows)
        overlaps = low <= y+dy and high >= y-dy
        comparisons.append({**deepcopy(observation), 'observer': panel['observer'],
                            'axis_t': x, 'read_value': y, 't_error': dx, 'read_error': dy,
                            'predicted': center['value'], 'predicted_interval': [low, high],
                            'read_interval': [y-dy, y+dy], 'residual': center['value']-y,
                            'status': 'overlap' if overlaps else 'separated',
                            'trace': predicted['traces']})
    snapshot = {'case': model, 'protocol': protocol, 'readings': readings}
    return {'format_version': '1.0', 'status': 'complete', 'model': protocol['model'],
            'scope': '带误差读图值与基础式的条件对照；暂令给定效能等于原图t。'
                     '区间相交不证明原图求法已还原；区间分离表示此条件下存在差异。',
            'data_kind': readings['data_kind'], 'case_reproduction_ready': False,
            'input_sha256': hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                                     allow_nan=False).encode('utf-8')).hexdigest(),
            'protocol': deepcopy(protocol), 'readings_snapshot': deepcopy(readings),
            'source_snapshot': deepcopy(model['sources']), 'comparisons': comparisons,
            'counts': {status: sum(r['status'] == status for r in comparisons)
                       for status in ('overlap', 'separated')},
            'not_covered': [{'panel': p['id'], 'driver': n} for p in panels for n in p['legend']
                            if n not in protocol['drivers']],
            'not_sampled': [{'panel': p['id'], 'driver': n} for p in panels for n in p['legend']
                            if n in protocol['drivers'] and not any(
                                r['panel'] == p['id'] and r['driver'] == n for r in comparisons)],
            'policies': baseline_check['policies']}


def render_comparison(report):
    lines = ['# 原图读数与基础式条件对照', '', report['scope'], '',
             report['readings_snapshot']['method'], '',
             f"区间相交{report['counts']['overlap']}点；分离{report['counts']['separated']}点。", '',
             '| 面板 | 改变节点 | 原图t约值 | 读图效能 | 读图误差 | 基础式效能 | 区间关系 |',
             '| --- | --- | ---: | ---: | ---: | ---: | --- |']
    for r in report['comparisons']:
        status = '相交' if r['status'] == 'overlap' else '分离'
        lines.append(f"| {r['panel']} | {r['driver']} | {r['axis_t']:.3f} | {r['read_value']:.3f} | "
                     f"±{r['read_error']:.3f} | {r['predicted']:.3f} | {status} |")
    lines += ['', '误差界限为像素选取与标定的保守估计，不是统计置信区间。'
              '对照同时考虑横轴读数误差引起的预测范围。未据此拟合或修改α、β、δ。', '',
              '图8中S5、S6作为变化对象的曲线未纳入基础式对照；中间节点变化方式仍待核实。']
    return '\n'.join(lines) + '\n'


def plot_comparison(report, model):
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
    except ImportError as exc:
        raise ModelError('绘图需要matplotlib；可不加--plot先导出对照记录') from exc
    panels = report['readings_snapshot']['panels']
    fig = Figure(figsize=(12, 3.6*((len(panels)+1)//2)), layout='constrained')
    FigureCanvasAgg(fig)
    axes = fig.subplots((len(panels)+1)//2, 2, squeeze=False).ravel()
    protocol = deepcopy(report['protocol'])
    protocol['values'] = list(range(101))
    curves = sweep(model, protocol)
    for axis, panel in zip(axes, panels):
        for index, driver in enumerate(protocol['drivers']):
            points = [r for r in report['comparisons'] if r['panel'] == panel['id'] and r['driver'] == driver]
            if not points:
                continue
            rows = [r for r in curves['rows'] if r['observer'] == panel['observer'] and r['driver'] == driver]
            color = f'C{index}'
            axis.plot([r['input'] for r in rows], [r['value'] for r in rows], '--', color=color, label=driver)
            axis.errorbar([r['axis_t'] for r in points], [r['read_value'] for r in points],
                          xerr=[r['t_error'] for r in points], yerr=[r['read_error'] for r in points],
                          fmt='o', capsize=3, color=color)
        axis.set(title=panel['id']+' / '+panel['observer'], xlabel='Candidate input = original t (unverified)',
                 ylabel='Operability', xlim=(0, 100), ylim=(0, 105))
        axis.grid(alpha=.25)
        axis.legend(ncol=3, fontsize=8)
    for axis in axes[len(panels):]:
        axis.set_visible(False)
    fig.suptitle('Raster readings with estimated error vs base equations (dashed) - no fitting')
    stream = io.BytesIO()
    fig.savefig(stream, format='png', dpi=150)
    return stream.getvalue()
