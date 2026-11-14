"""Explicit raw-fact extraction for table 5; no implicit cross-closure pooling."""
from copy import deepcopy
from fractions import Fraction
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from .experiments import _finite, inspect_experiments
from .model import ModelError
from .self_effectiveness import normalize_measurements
from .structure import digest


def extract_measurements(model, structure, plan, batch, config, calibration=None):
    check = inspect_experiments(model, structure, plan, batch)
    _finite(config)
    schema = json.loads(Path(__file__).with_name('extraction_schema.json').read_text(encoding='utf-8'))
    errors = list(Draft202012Validator(schema).iter_errors(config))
    if errors:
        raise ModelError('指标提取配置不符合数据契约：' + errors[0].message)
    if config['batch_sha256'] != digest(batch):
        raise ModelError('指标提取配置与记录批次版本不一致')
    nodes = {n['id']: n for n in model['nodes']}
    if set(config['rules']) != set(nodes):
        raise ModelError('指标提取须逐节点登记求法，未知项填null')
    for node, rule in config['rules'].items():
        if rule is None:
            continue
        interval = rule['method'] in ('union_intervals', 'single_interval')
        if len(rule['fields']) != (2 if interval else 1) or rule['indicator'] != nodes[node]['indicator']['label']:
            raise ModelError('指标名称或所需事实字段数与求法不一致')
    checked = {c['closure']: c for c in check['closures']}
    runs = {r['id']: r for r in batch['runs']}
    identifiers, records, traces = set(), [], []
    used_closures = set()
    for group in config['groups']:
        if group['id'] in identifiers:
            raise ModelError('指标记录分组标识重复')
        identifiers.add(group['id'])
        run = runs.get(group['run'])
        if run is None:
            raise ModelError('指标分组引用不存在的推演')
        closures = {c['id']: c for c in run['closures']}
        if not set(group['closures']) <= set(closures):
            raise ModelError('指标分组引用不存在或不属于同一推演的闭合记录')
        used_closures.update(group['closures'])
        selected = [closures[c] for c in group['closures']]
        observations = {}
        for node in nodes:
            rule = config['rules'][node]
            events = [(c, e) for c in selected for e in c['events'] if e['capability'] == node]
            reasons, values, evidence = [], [], []
            if rule is None:
                reasons.append('UNKNOWN_EXTRACTION_RULE')
            if not events:
                reasons.append('MISSING_CAPABILITY_EVENT')
            contexts = {c['id'] for c, _ in events}
            if len(contexts) > 1:
                reasons.append('AMBIGUOUS_CAPABILITY_CONTEXT')
            for closure, event in events:
                if checked[closure['id']]['status'] != 'structurally_complete':
                    reasons.append('CLOSURE_NEEDS_REVIEW:' + closure['id'])
                facts = {f['name']: f for f in event['facts']}
                selected_facts = []
                row = []
                for field in rule['fields'] if rule else []:
                    fact = facts.get(field)
                    selected_facts.append({'field': field, 'fact': deepcopy(fact)})
                    if fact is None or fact['value'] is None:
                        reasons.append('MISSING_FACT:' + event['id'] + ':' + field)
                    else:
                        value = fact['value']
                        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                                value < 0):
                            raise ModelError('效能指标事实须为非负数值')
                        if fact['unit'] != rule['unit']:
                            raise ModelError('指标事实单位与提取配置不一致')
                        row.append(Fraction(str(value)))
                evidence.append({'closure': closure['id'], 'task': closure['task'], 'target': closure['target'],
                                 'event': event['id'], 'source_locator': event['source_locator'],
                                 'selected_facts': selected_facts})
                if rule and len(row) == len(rule['fields']):
                    if len(row) == 2 and row[1] < row[0]:
                        raise ModelError('指标事实的结束刻度早于开始刻度')
                    if len(row) == 2 or rule['method'] == 'first_value':
                        for value in row:
                            if ((closure['start'] is not None and value < closure['start']) or
                                    (closure['end'] is not None and value > closure['end'])):
                                raise ModelError('指标事实刻度超出闭合记录区间')
                        if rule['unit'] != batch['time_unit']:
                            raise ModelError('时刻及区间指标须使用批次时间单位')
                    if rule['method'] == 'sum_counts' and row[0].denominator != 1:
                        raise ModelError('次数指标须为非负整数')
                    values.append(row)
            raw = None
            if rule and not reasons:
                method = rule['method']
                if method in ('single_value', 'single_interval') and len(values) != 1:
                    reasons.append('AMBIGUOUS_MULTIPLE_VALUES')
                elif method == 'single_interval':
                    raw = {'start': _number(values[0][0]), 'end': _number(values[0][1]), 'unit': rule['unit']}
                else:
                    if method == 'first_value':
                        value = min(v[0] for v in values)
                    elif method == 'sum_counts':
                        value = sum(v[0] for v in values)
                    elif method == 'single_value':
                        value = values[0][0]
                    else:
                        # Union, rather than a sum, avoids counting overlapping tracking intervals twice.
                        merged = []
                        for start, end in sorted(values):
                            if merged and start <= merged[-1][1]:
                                merged[-1][1] = max(end, merged[-1][1])
                            else:
                                merged.append([start, end])
                        value = sum(end - start for start, end in merged)
                    raw = {'value': _number(value), 'unit': rule['unit']}
            observations[node] = raw
            traces.append({'record': group['id'], 'node': node, 'rule': deepcopy(rule),
                           'status': 'unknown' if reasons else 'extracted', 'blockers': sorted(set(reasons)),
                           'observation': deepcopy(raw), 'evidence': evidence})
        records.append({'id': group['id'], 'observations': observations})
    artificial = any(value['data_kind'] == 'method_check' for value in (plan, batch, config))
    measurements = {'format_version': '1.0', 'id': config['id'],
                    'data_kind': 'method_check' if artificial else 'user_supplied_measurements',
                    'reference': config['basis'], 'records': records}
    normalized = normalize_measurements(model, calibration, measurements) if calibration is not None and records else None
    known = sum(t['status'] == 'extracted' for t in traces)
    return {'format_version': '1.0', 'case': model['id'],
            'status': 'no_records' if not records else 'blocked' if known != len(traces) or
                      (normalized is not None and normalized['status'] == 'blocked') else 'complete',
            'scope': '依显式求法从闭合记录提取指标，可衔接既有SE转换；不是原文推演数据复现。',
            'input_sha256': digest({'case': model, 'structure': structure, 'plan': plan, 'batch': batch,
                                    'config': config, 'calibration': calibration}),
            'counts': {'records': len(records), 'known_indicators': known, 'unknown_indicators': len(traces) - known},
            'measurements': measurements, 'traces': traces, 'normalization': normalized,
            'unselected_closures': sorted(set(checked) - used_closures),
            'experiment_check': check, 'config_snapshot': deepcopy(config), 'case_reproduction_ready': False,
            'policies': {'grouping': 'explicit_same_run_closures', 'cross_context_capability': 'unknown',
                         'missing_values': 'unknown', 'fact_truth': 'declared_only',
                         'event_duration_as_indicator': False, 'automatic_normalization_bounds': False}}


def _number(value):
    try:
        result = float(value)
    except OverflowError as exc:
        raise ModelError('提取指标超出有限数值范围') from exc
    if not math.isfinite(result):
        raise ModelError('提取指标超出有限数值范围')
    return result
