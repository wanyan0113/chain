"""FDNA equations (1)-(2), with explicit exogenous values and per-edge evidence."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

from .model import ModelError, inspect_model, validate_model

MODEL = 'fdna_base_eq1_eq2'


def evaluate(model, scenario):
    validate_model(model)
    if not isinstance(scenario, dict) or set(scenario) != {'id', 'basis', 'node_values'}:
        raise ModelError('推演须且仅含id、basis与node_values')
    if any(not isinstance(scenario[key], str) or not scenario[key].strip() for key in ('id', 'basis')):
        raise ModelError('须说明推演名称与取值依据')
    values = scenario['node_values']
    structure = inspect_model(model)
    if not isinstance(values, dict) or set(values) - set(structure['exogenous_nodes']):
        raise ModelError('只可为无入边节点指定效能；本阶段不覆盖中间节点')
    for node, value in values.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not 0 <= value <= 100 or not math.isfinite(value)):
            raise ModelError(f'{node}效能须在[0,100]内，未知时填null或留缺')
    nodes = {node['id']: node for node in model['nodes']}
    incoming = {node: [] for node in nodes}
    for edge in model['edges']:
        incoming[edge['receiver']].append(edge)
    results = {}
    for node in structure['topological_order']:
        blockers, terms = [], []
        if not incoming[node]:
            value = values.get(node)
            if value is None:
                blockers.append({'code': 'UNKNOWN_EXOGENOUS', 'node': node})
            sod = cod = None
        else:
            for edge in incoming[node]:
                feeder_value = results[edge['feeder']]['value']
                term = {**deepcopy(edge), 'operability': feeder_value, 'sod': None, 'cod': None}
                if feeder_value is None:
                    blockers.append({'code': 'UNKNOWN_FEEDER', 'feeder': edge['feeder']})
                if edge['alpha'] is None or edge['beta'] is None:
                    blockers.append({'code': 'UNKNOWN_PARAMETER', 'feeder': edge['feeder'], 'receiver': node})
                if feeder_value is not None:
                    if edge['alpha'] is not None:
                        # Equivalent to alpha*O + 100*(1-alpha), avoiding overshoot at O=100.
                        term['sod'] = 100 - edge['alpha'] * (100 - feeder_value)
                    if edge['beta'] is not None:
                        term['cod'] = feeder_value + edge['beta']
                terms.append(term)
            if blockers:
                sod = cod = value = None
            else:
                sod = math.fsum(term['sod'] for term in terms) / len(terms)
                cod = min(term['cod'] for term in terms)
                value = min(sod, cod)
        results[node] = {
            'id': node, 'label': nodes[node]['label'], 'subject': nodes[node]['subject'],
            'kind': 'derived' if incoming[node] else 'exogenous',
            'status': 'unknown' if blockers else 'computed' if incoming[node] else 'supplied',
            'value': value, 'sod_mean': sod, 'cod_min': cod,
            'binding_constraints': [] if value is None or not terms else
                (['sod_mean'] if sod == value else []) +
                [f"cod:{term['feeder']}" for term in terms if term['cod'] == value],
            'terms': terms, 'blockers': blockers, 'source_refs': deepcopy(nodes[node]['source_refs']),
        }
    snapshot = {'case': model, 'scenario': scenario, 'model': MODEL}
    return {
        'format_version': '1.0', 'model': MODEL, 'case': model['id'], 'scenario': deepcopy(scenario),
        'input_sha256': hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                                 allow_nan=False).encode('utf-8')).hexdigest(),
        'status': 'blocked' if any(item['value'] is None for item in results.values()) else 'complete',
        'scope': '依基础式(1)—(2)及表7已报告参数重算；未加入δ，不表示完整案例已重现。',
        'policies': {'direction': 'feeder_to_receiver', 'sod_aggregation': 'mean_of_complete_edge_terms',
                     'alpha_renormalized': False, 'delta': 'not_in_base_equations',
                     'output_clipping': 'none', 'intermediate_override': 'unsupported'},
        'topological_order': structure['topological_order'], 'nodes': list(results.values()),
        'case_reproduction_ready': False, 'pending': deepcopy(model['gaps']),
        'reported_fit': deepcopy(model['reported_fit']), 'case_extension': deepcopy(model['case_extension']),
        'source_snapshot': deepcopy(model['sources']),
    }
