"""Explicit measured-indicator to SE conversion for section 4.2."""
from copy import deepcopy
import csv
from fractions import Fraction
import hashlib
import io
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from .model import ModelError, validate_model


def _finite(value):
    if isinstance(value, (int, float)):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ModelError('测量值与归一化界限须为有限数值')
    elif isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def normalize_measurements(model, calibration, measurements):
    validate_model(model)
    schema = json.loads(Path(__file__).with_name('se_schema.json').read_text(encoding='utf-8'))
    validator = Draft202012Validator(schema)
    for data, required in ((calibration, 'rules'), (measurements, 'records')):
        _finite(data)
        if not validator.is_valid(data) or required not in data:
            raise ModelError('效能测量或归一化约定不符合数据契约')
    nodes = {n['id']: n for n in model['nodes']}
    rules = calibration['rules']
    if set(rules) != set(nodes):
        raise ModelError('归一化约定须逐一登记所有能力节点；未知项填null')
    for node, rule in rules.items():
        if rule is None:
            continue
        if rule['lower'] >= rule['upper']:
            raise ModelError('归一化下界须严格小于上界')
        direction = nodes[node]['indicator']['direction']
        if direction != 'unspecified' and direction != rule['direction']:
            raise ModelError(f'{node}的成本型或效益型方向与已登记指标不一致')
    identifiers = [r['id'] for r in measurements['records']]
    if len(identifiers) != len(set(identifiers)):
        raise ModelError('测量记录标识不可重复')
    records = []
    for record in measurements['records']:
        observations = record['observations']
        if set(observations) - set(nodes):
            raise ModelError('测量记录引用未登记的能力节点')
        results = []
        for node, info in nodes.items():
            raw, rule = observations.get(node), rules[node]
            reasons, value, se = [], None, None
            if raw is not None and 'start' in raw and raw['start'] is not None and raw['end'] is not None:
                if raw['end'] < raw['start']:
                    raise ModelError(f'{node}的结束刻度早于开始刻度')
            if rule is None:
                reasons.append('UNKNOWN_CALIBRATION')
            if raw is None:
                reasons.append('MISSING_OBSERVATION')
            if rule is not None and raw is not None:
                if raw['unit'] != rule['unit']:
                    raise ModelError(f'{node}的测量单位与归一化约定不一致')
                expected = {'value', 'unit'} if rule['measure'] == 'value' else {'start', 'end', 'unit'}
                if set(raw) != expected:
                    raise ModelError(f'{node}的测量形式与明确选用的指标求法不一致')
                fields = expected - {'unit'}
                if any(raw[k] is None for k in fields):
                    reasons.append('MISSING_MEASUREMENT')
                else:
                    # Rational arithmetic avoids overflow in subtraction or a very wide span.
                    value = (Fraction(str(raw['value'])) if rule['measure'] == 'value' else
                             Fraction(str(raw['end']))-Fraction(str(raw['start'])))
                    low, high = Fraction(str(rule['lower'])), Fraction(str(rule['upper']))
                    if not low <= value <= high:
                        raise ModelError(f'{node}指标超出已声明界限；不自动裁剪或重估上下界')
                    normalized = (value-low)/(high-low) if rule['direction'] == 'benefit' else (high-value)/(high-low)
                    se = float(100*normalized)
            results.append({'id': node, 'indicator': info['indicator']['label'],
                            'raw': deepcopy(raw), 'indicator_value': None if value is None else float(value),
                            'se': se, 'status': 'unknown' if reasons else 'normalized',
                            'blockers': reasons, 'rule': deepcopy(rule), 'source_refs': deepcopy(info['source_refs'])})
        records.append({'id': record['id'], 'nodes': results})
    # Keep measured intermediate SE distinct from propagated node operability.
    incoming = {e['receiver']: [] for e in model['edges']}
    for edge in model['edges']:
        incoming[edge['receiver']].append(edge['feeder'])
    fit_inputs = {node: [] for node in incoming}
    for record in records:
        values = {n['id']: n['se'] for n in record['nodes']}
        for receiver, feeders in incoming.items():
            if all(values[n] is not None for n in [receiver, *feeders]):
                fit_inputs[receiver].append({'record_id': record['id'],
                                             'feeder_values': {n: values[n] for n in feeders},
                                             'receiver_value': values[receiver]})
    snapshot = {'case': model, 'calibration': calibration, 'measurements': measurements}
    return {'format_version': '1.0', 'case': model['id'],
            'status': 'blocked' if any(n['se'] is None for r in records for n in r['nodes']) else 'complete',
            'scope': '依4.2节接收指标并按显式约定转为实测SE；归一化公式及界限是所选约定，'
                     '不是原文已报告配置。中间节点的实测SE不覆盖依赖传播结果。',
            'data_kind': measurements['data_kind'], 'measurement_provenance': 'declared_only',
            'input_sha256': hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                                     allow_nan=False).encode('utf-8')).hexdigest(),
            'calibration_snapshot': deepcopy(calibration), 'measurements_snapshot': deepcopy(measurements),
            'source_snapshot': deepcopy(model['sources']), 'records': records, 'fit_inputs': fit_inputs,
            'counts': {'records': len(records), 'known_se': sum(n['se'] is not None for r in records for n in r['nodes']),
                       'unknown_se': sum(n['se'] is None for r in records for n in r['nodes'])},
            'complete_local_rows': {n: len(rows) for n, rows in fit_inputs.items()},
            'case_reproduction_ready': False, 'parameter_fit_ready': False,
            'limits': ['完整测量行仅代表供给及接受节点有已知SE，不保证拟合可辨识性或样本充分性',
                       '未重新拟合α、β、δ，未计算残差或伪R²',
                       '输入来源仅登记声明，未确认其为原文推演记录'],
            'policies': {'method': 'explicit_linear_minmax', 'bounds_from_sample_extrema': False,
                         'clipping': 'none', 'missing_values': 'unknown', 'fusion_duration': 'explicit_choice_only'}}


def se_csv(report):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['record_id', 'node', 'indicator_value', 'se', 'status', 'blockers'])
    for record in report['records']:
        for node in record['nodes']:
            writer.writerow([record['id'], node['id'], node['indicator_value'], node['se'],
                             node['status'], '|'.join(node['blockers'])])
    return stream.getvalue()


def render_se_summary(report):
    lines = ['# 奥术网指标转SE记录', '', report['scope'], '',
             '取值来源：'+report['measurements_snapshot']['reference'], '',
             '归一化约定：'+report['calibration_snapshot']['basis'], '',
             f"记录{report['counts']['records']}组，已知SE {report['counts']['known_se']}项，"
             f"未知{report['counts']['unknown_se']}项。数据类别：{report['data_kind']}。", '',
             '| 记录 | 能力节点 | 指标值 | SE | 状态 |', '| --- | --- | ---: | ---: | --- |']
    def display(value):
        return '未知' if value is None else f'{value:.10g}'
    for record in report['records']:
        for node in record['nodes']:
            lines.append(f"| {record['id']} | {node['id']} | {display(node['indicator_value'])} | "
                         f"{display(node['se'])} | {node['status']} |")
    lines += ['', '后续求解所需的供给及接受SE完整行数：', '']
    lines.extend(f'- {node}：{count}行' for node, count in report['complete_local_rows'].items())
    lines += ['', *report['limits']]
    return '\n'.join(lines)+'\n'
