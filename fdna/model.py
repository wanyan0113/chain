"""Source-aware model contract; never infers edges from chain order."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from source_data.common import read_json


class ModelError(ValueError):
    pass


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ModelError('效能与参数须为有限数值')
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def _unique(items, collection, key='id'):
    ids = [item[key] for item in items]
    if len(ids) != len(set(ids)):
        raise ModelError(f'{collection}存在重复标识')
    return set(ids)


def _members(group):
    for member in group:
        if isinstance(member, list):
            yield from _members(member)
        else:
            yield member


def topology(model):
    """Stable Kahn ordering; a cycle has no defined semantics in this version."""
    order = [node['id'] for node in model['nodes']]
    pending, result = set(order), []
    while pending:
        ready = [node for node in order if node in pending and not any(
            edge['receiver'] == node and edge['feeder'] in pending for edge in model['edges'])]
        if not ready:
            raise ModelError('依赖关系成环，基础模型未规定环路的求法')
        result.extend(ready)
        pending.difference_update(ready)
    return result


def validate_model(model):
    _finite(model)
    schema = json.loads(Path(__file__).with_name('schema.json').read_text(encoding='utf-8'))
    errors = sorted(Draft202012Validator(schema).iter_errors(model), key=lambda e: str(list(e.path)))
    if errors:
        raise ModelError('; '.join(f'{list(e.path)}: {e.message}' for e in errors[:6]))
    ids = {name: _unique(model[name], name) for name in
           ('sources', 'subjects', 'targets', 'nodes', 'chains', 'gaps')}
    entities = ids['subjects'] | ids['targets'] | ids['nodes'] | ids['chains']
    if len(entities) != sum(len(ids[name]) for name in ('subjects', 'targets', 'nodes', 'chains')):
        raise ModelError('主体、目标、能力与法术链须使用不同标识')
    if any(':' in source['id'] or any(':' in locator for locator in source['locators'])
           for source in model['sources']):
        raise ModelError('来源及位置标识不能包含冒号')
    refs = {f"{source['id']}:{locator}" for source in model['sources'] for locator in source['locators']}

    def check_refs(value):
        if isinstance(value, dict):
            if 'source_refs' in value and not set(value['source_refs']) <= refs:
                raise ModelError('来源位置未登记：' + ', '.join(sorted(set(value['source_refs']) - refs)))
            for item in value.values():
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)

    check_refs(model)
    for node in model['nodes']:
        if node['subject'] not in ids['subjects']:
            raise ModelError(f"能力{node['id']}所属主体不存在")
    pairs = set()
    for edge in model['edges']:
        pair = edge['feeder'], edge['receiver']
        if pair in pairs or pair[0] == pair[1] or not set(pair) <= ids['nodes']:
            raise ModelError('依赖边重复、自指或端点不存在')
        pairs.add(pair)
    receivers = {edge['receiver'] for edge in model['edges']}
    if set(model['case_extension']['delta']) != receivers:
        raise ModelError('δ未知记录须与接受节点逐一对应')
    _unique(model['reported_fit'], '拟合报告', key='receiver')
    if any(item['receiver'] not in receivers for item in model['reported_fit']):
        raise ModelError('拟合报告引用了不存在的接受节点')
    for chain in model['chains']:
        if chain['target'] not in ids['targets'] or not set(_members(chain['members'])) <= ids['nodes']:
            raise ModelError('法术链引用了不存在的目标或能力')
    topology(model)


def inspect_model(model):
    validate_model(model)
    incoming = {node['id']: [] for node in model['nodes']}
    for edge in model['edges']:
        incoming[edge['receiver']].append(edge['feeder'])
    return {
        'status': 'valid', 'model': model['id'],
        'counts': {name: len(model[name]) for name in ('subjects', 'targets', 'nodes', 'edges', 'chains')},
        'topological_order': topology(model),
        'exogenous_nodes': [node for node, feeders in incoming.items() if not feeders],
        'incoming': incoming,
        'base_formula_ready': all(edge['alpha'] is not None and edge['beta'] is not None for edge in model['edges']),
        'case_reproduction_ready': False,
        'pending': deepcopy(model['gaps']),
        'reported_fit': deepcopy(model['reported_fit']),
        'scope': '基础式可单独核验；δ与推演约定尚未齐备，完整案例仍待核实。',
    }


def load_model(path):
    model = read_json(path)
    validate_model(model)
    return model


def verify_sources(model, model_path):
    """Verify only locally supplied files; no lookup or implicit source substitution."""
    validate_model(model)
    results = []
    for source in model['sources']:
        result = {'source': source['id'], 'expected_sha256': source['sha256']}
        if 'path' not in source:
            result.update(status='not_checked', reason='仅登记文献指纹，未指定本地副本')
        else:
            path = (Path(model_path).resolve().parent / source['path']).resolve()
            try:
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                result.update(status='unavailable', reason='来源文件不可读取')
            else:
                result.update(status='matched' if actual == source['sha256'] else 'changed', actual_sha256=actual)
        results.append(result)
    return results
