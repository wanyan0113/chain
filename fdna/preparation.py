"""Traceable handoff from closure facts through measured SE to fitting samples."""
from copy import deepcopy

from .extraction import extract_measurements
from .fitting import _validate
from .model import ModelError
from .structure import digest


def prepare_fit_samples(model, structure, plan, batch, extraction, calibration, partition):
    _validate('partition', partition)
    if not isinstance(calibration, dict):
        raise ModelError('准备拟合样本须显式提供SE归一化配置；未知求法应逐节点填null')
    if partition['batch_sha256'] != digest(batch) or partition['extraction_sha256'] != digest(extraction):
        raise ModelError('训练与留出划分须对应当前记录批次及指标分组版本')
    extracted = extract_measurements(model, structure, plan, batch, extraction, calibration)
    selected_runs = {g['run'] for g in extraction['groups']}
    if set(partition['run_splits']) != selected_runs:
        raise ModelError('须为每个已选推演恰好登记一次训练或留出归属；不能自动按记录拆分')
    used = set()
    for group in extraction['groups']:
        for closure in group['closures']:
            key = group['run'], closure
            if key in used:
                raise ModelError('同一闭合记录不能重复用于多个拟合样本组')
            used.add(key)
    fingerprint = digest({'case': model, 'structure': structure, 'plan': plan, 'batch': batch,
                          'extraction': extraction, 'calibration': calibration, 'partition': partition})
    normalized = extracted['normalization']
    normalized_rows = {} if normalized is None else {r['id']: r for r in normalized['records']}
    samples = {'format_version': '1.0', 'id': partition['id'],
               'data_kind': 'method_check' if extracted['measurements']['data_kind'] == 'method_check' or
                            partition['data_kind'] == 'method_check' else 'user_supplied_se',
               'basis': partition['basis']+'; preparation_input_sha256='+fingerprint,
               'case_sha256': digest(model), 'records': []}
    lineage = []
    for group in extraction['groups']:
        row = normalized_rows[group['id']]
        split = partition['run_splits'][group['run']]
        samples['records'].append({'id': row['id'], 'split': split,
                                   'node_values': {n['id']: n['se'] for n in row['nodes']}})
        lineage.append({'record': row['id'], 'run': group['run'], 'closures': deepcopy(group['closures']),
                        'split': split, 'grouping_basis': group['basis'],
                        'unknown_nodes': [{'node': n['id'], 'blockers': deepcopy(n['blockers'])}
                                          for n in row['nodes'] if n['se'] is None]})
    _validate('samples', samples)
    local_counts = []
    for receiver in sorted({e['receiver'] for e in model['edges']}):
        feeders = [e['feeder'] for e in model['edges'] if e['receiver'] == receiver]
        counts = {split: sum(r['split'] == split and all(r['node_values'][n] is not None
                         for n in [receiver, *feeders]) for r in samples['records']) for split in ('train','holdout')}
        minimum = 2*len(feeders)
        local_counts.append({'receiver': receiver, 'feeders': feeders, 'complete_local_rows': counts,
                             'base_parameter_count': minimum, 'additive_parameter_count': minimum+1,
                             'base_solver_sample_minimum_met': counts['train'] >= minimum,
                             'additive_solver_sample_minimum_met': counts['train'] >= minimum+1})
    return {'format_version': '1.0', 'status': extracted['status'], 'case': model['id'],
            'scope': '衔接第4.2—4.3节：闭合事实→显式指标→实测SE→整次推演划分的拟合样本；不从表7或传播值补齐缺测。',
            'case_reproduction_ready': False, 'input_sha256': fingerprint,
            'samples': samples, 'samples_sha256': digest(samples), 'lineage': lineage,
            'partition_snapshot': deepcopy(partition), 'extraction_snapshot': extracted,
            'local_sample_counts': local_counts,
            'counts': {'records': len(samples['records']),
                       'train_runs': sum(v == 'train' for v in partition['run_splits'].values()),
                       'holdout_runs': sum(v == 'holdout' for v in partition['run_splits'].values()),
                       'train_records': sum(r['split'] == 'train' for r in samples['records']),
                       'holdout_records': sum(r['split'] == 'holdout' for r in samples['records'])},
            'policies': {'split_unit': 'whole_run', 'automatic_split': False,
                         'reused_closures': 'reject', 'missing_se': 'retain_null',
                         'se_source': 'normalized_measurements_only', 'normalization_bounds': 'explicit_fixed',
                         'source_authenticity': 'declared_only', 'parameters_fitted': False},
            'limits': ['样本准备完成不表示样本充分、参数可辨识或原文实验已复现',
                       '按推演划分避免同次推演跨集合，但不能证明不同推演独立；重复实验需自行说明',
                       '归一化界限必须预先说明；本接口不从训练或留出数据重估界限，也不推定其等同原文',
                       '仍须通过fit显式选择模型、δ及求解设置；样本不足时不复制、合成或补零']}
