"""Explicit additive-delta model; keeps the base evaluator unchanged."""
from copy import deepcopy
import math

from .engine import evaluate
from .model import ModelError, _finite
from .structure import digest

ADDITIVE_MODEL = 'fdna_additive_delta'


def validate_extension(model, extension):
    _finite(extension)
    if (not isinstance(extension, dict) or
            set(extension) != {'model', 'basis', 'delta_values', 'output_policy'} or
            extension['model'] != ADDITIVE_MODEL or extension['output_policy'] != 'no_clipping' or
            not isinstance(extension['basis'], str) or not extension['basis'].strip()):
        raise ModelError('δ扩展须明确模型、依据、逐接受节点δ与不裁剪约定')
    receivers = {e['receiver'] for e in model['edges']}
    values = extension['delta_values']
    if not isinstance(values, dict) or set(values) != receivers:
        raise ModelError('δ须逐一登记所有接受节点，未知时填null')
    if any(v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))) for v in values.values()):
        raise ModelError('δ须为有限数值或null')


def evaluate_additive(model, scenario, extension):
    base = evaluate(model, scenario)
    validate_extension(model, extension)
    values, results = {}, []
    for original in base['nodes']:
        node = deepcopy(original)
        node['delta'] = None
        node['base_before_delta'] = None
        if node['kind'] == 'exogenous':
            values[node['id']] = node['value']
            results.append(node)
            continue
        delta = extension['delta_values'][node['id']]
        terms, blockers = [], []
        for old in node['terms']:
            term = deepcopy(old)
            value = values[term['feeder']]
            term.update(operability=value, sod=None, cod=None)
            if value is None:
                blockers.append({'code': 'UNKNOWN_FEEDER', 'feeder': term['feeder']})
            if term['alpha'] is None or term['beta'] is None:
                blockers.append({'code': 'UNKNOWN_PARAMETER', 'feeder': term['feeder'], 'receiver': node['id']})
            if value is not None:
                term['sod'] = None if term['alpha'] is None else 100-term['alpha']*(100-value)
                term['cod'] = None if term['beta'] is None else value+term['beta']
            terms.append(term)
        if delta is None:
            blockers.append({'code': 'UNKNOWN_DELTA', 'node': node['id']})
        sod = None if any(t['sod'] is None for t in terms) else math.fsum(t['sod'] for t in terms)/len(terms)
        cod = None if any(t['cod'] is None for t in terms) else min(t['cod'] for t in terms)
        before = None if sod is None or cod is None else min(sod, cod)
        value = None if blockers else before+delta
        if value is not None and not math.isfinite(value):
            raise ModelError('δ扩展计算超出有限数值范围')
        node.update(value=value, status='unknown' if blockers else 'computed', delta=delta,
                    base_before_delta=before, sod_mean=sod, cod_min=cod, terms=terms, blockers=blockers,
                    binding_constraints=[] if before is None else
                    (['sod_mean'] if sod == before else []) +
                    [f"cod:{t['feeder']}" for t in terms if t['cod'] == before])
        values[node['id']] = value
        results.append(node)
    return {**base, 'model': ADDITIVE_MODEL, 'nodes': results,
            'status': 'blocked' if any(v is None for v in values.values()) else 'complete',
            'input_sha256': digest({'case': model, 'scenario': scenario, 'extension': extension}),
            'scope': '按显式逐接受节点δ求取min(SoD,CoD)+δ；不裁剪效能，不代表原文δ已知。',
            'extension_snapshot': deepcopy(extension),
            'outside_se_range': [key for key, v in values.items() if v is not None and not 0 <= v <= 100],
            'policies': {**base['policies'], 'delta': 'explicit_per_receiver', 'output_clipping': 'none'}}
