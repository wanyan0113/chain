"""Paired base-case resampling of frozen prediction errors; no model fitting."""
import argparse
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .model import ModelError
from .structure import digest
from .workflow_common import csv_text, dumps, write_bundle


PROTOCOL = dict(id='flow06_frozen_prediction_cluster_resampling_v1', resamples=2000, seed=20261123,
    resampling_unit='holdout_base_case_id', pairing='same_draw_across_environments_nodes_methods_and_prediction_levels',
    replacement=True, model_refit=False, missing_panel='reject', quantiles=[.025, .5, .975], quantile_method='linear',
    numerical_tie_tolerance=1e-8, comparisons=[['ols_intercept', 'fdna:free_delta'], ['fdna:free_delta', 'fdna:base']],
    interpretation='conditional_descriptive_prediction_stability_not_parameter_confidence_or_significance')
METHODS = ['fdna:base', 'fdna:free_delta', 'ols_intercept', 'training_mean']
KINDS = ['local', 'recursive']


def prepare_panel(report):
    """Require a complete paired panel; never silently turn repeated records into units."""
    points = report.get('points')
    if not isinstance(points, list) or not points:
        raise ModelError('冻结预测记录为空')
    splits = {}; identities = set(); observations = {}; targets = {}; held = []
    for p in points:
        if p['split'] not in ('train', 'holdout'):
            raise ModelError('非法划分')
        base = p['base_case_id']; split = p['split']
        if base in splits and splits[base] != split:
            raise ModelError('基础样本跨训练与留出')
        splits[base] = split
        key = (p['fit_group'], p['sample_id'], p['node'], p['method'])
        if key in identities:
            raise ModelError('冻结预测主键重复')
        identities.add(key)
        observation = p['fit_group'], base
        identity = p['sample_id'], p['scenario_id'], split
        if observation in observations and observations[observation] != identity:
            raise ModelError('同一环境基础样本有多个网络观测或元数据冲突')
        observations[observation] = identity
        target_key = (*observation, p['node'])
        if target_key in targets and targets[target_key] != p['target']:
            raise ModelError('方法间的对应目标不一致')
        targets[target_key] = p['target']
        if split == 'holdout':
            held.append(p)
    cases = sorted(b for b, split in splits.items() if split == 'holdout')
    if len(cases) < 2:
        raise ModelError('成组重采样至少需要两个留出基础样本')
    groups = sorted({p['fit_group'] for p in points}); nodes = sorted({p['node'] for p in points})
    if {p['method'] for p in points} != set(METHODS):
        raise ModelError('须包含登记的四种预测方法')
    lookup = {(p['fit_group'], p['node'], p['method'], p['base_case_id']): p for p in held}
    expected = {(g, n, m, b) for g in groups for n in nodes for m in METHODS for b in cases}
    if len(lookup) != len(held) or set(lookup) != expected:
        raise ModelError('留出配对面板缺失或重复，拒绝静默删除')
    errors = np.empty((len(groups), len(nodes), len(KINDS), len(METHODS), len(cases)))
    for gi, group in enumerate(groups):
        for ni, node in enumerate(nodes):
            for mi, method in enumerate(METHODS):
                for bi, base in enumerate(cases):
                    point = lookup[group, node, method, base]
                    for ki, kind in enumerate(KINDS):
                        values = point['target'], point[kind + '_prediction']
                        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                            raise ModelError('留出目标或预测缺失、受阻或非有限，拒绝不完整面板')
                        errors[gi, ni, ki, mi, bi] = values[1] - values[0]
    return dict(base_cases=cases, groups=groups, nodes=nodes, methods=list(METHODS), prediction_levels=list(KINDS), errors=errors)


def resampled_losses(errors, indices):
    """Return draw×group×node×prediction×method×metric, using one joint index draw."""
    if (indices.ndim != 2 or indices.shape[1] != errors.shape[-1]
            or not np.issubdtype(indices.dtype, np.integer) or not len(indices)
            or np.any(indices < 0) or np.any(indices >= errors.shape[-1])):
        raise ModelError('成组抽样索引不符合面板')
    losses = np.empty((len(indices), *errors.shape[:-1], 2))
    # Batching each draw avoids a draw×all-errors×sample temporary array.
    for i, draw in enumerate(indices):
        sampled = np.take(errors, draw, axis=-1)
        losses[i, ..., 0] = np.sqrt(np.mean(sampled**2, axis=-1))
        losses[i, ..., 1] = np.mean(np.abs(sampled), axis=-1)
    return losses


def quantile_summary(values):
    q = np.quantile(values, PROTOCOL['quantiles'], method=PROTOCOL['quantile_method'])
    return dict(q025=float(q[0]), median=float(q[1]), q975=float(q[2]),
                resample_sd=float(np.std(values, ddof=1)))


def analyze(report, protocol):
    if protocol != PROTOCOL:
        raise ModelError('稳定性方案与登记协议不一致')
    panel = prepare_panel(report); count = len(panel['base_cases'])
    rng = np.random.Generator(np.random.PCG64(protocol['seed']))
    indices = rng.integers(0, count, size=(protocol['resamples'], count))
    errors = panel.pop('errors')
    losses = resampled_losses(errors, indices)
    original = np.stack((np.sqrt(np.mean(errors**2, axis=-1)), np.mean(np.abs(errors), axis=-1)), axis=-1)
    metrics = []; contrasts = []; difference_arrays = []
    for left, right in protocol['comparisons']:
        difference_arrays.append(losses[..., METHODS.index(left), :] - losses[..., METHODS.index(right), :])
    differences = np.stack(difference_arrays, axis=-2)
    for gi, group in enumerate(panel['groups']):
        for ni, node in enumerate(panel['nodes']):
            for ki, kind in enumerate(KINDS):
                for mi, method in enumerate(METHODS):
                    for si, score in enumerate(('rmse', 'mae')):
                        values = losses[:, gi, ni, ki, mi, si]
                        metrics.append(dict(fit_group=group, node=node, prediction=kind, method=method, metric=score,
                            original=float(original[gi, ni, ki, mi, si]), base_cases=count, resamples=len(indices),
                            **quantile_summary(values)))
                for ci, (left, right) in enumerate(protocol['comparisons']):
                    for si, score in enumerate(('rmse', 'mae')):
                        values = differences[:, gi, ni, ki, ci, si]; tolerance = protocol['numerical_tie_tolerance']
                        summary = quantile_summary(values)
                        contrasts.append(dict(fit_group=group, node=node, prediction=kind, left=left, right=right, metric=score,
                            original=float(original[gi, ni, ki, METHODS.index(left), si] - original[gi, ni, ki, METHODS.index(right), si]),
                            base_cases=count, resamples=len(indices), negative_count=int(np.sum(values < -tolerance)),
                            numerical_tie_count=int(np.sum(np.abs(values) <= tolerance)), positive_count=int(np.sum(values > tolerance)),
                            central_range_contains_zero=summary['q025'] <= 0 <= summary['q975'], **summary))
    metadata = dict(stage='frozen_prediction_base_case_stability', protocol=deepcopy(protocol), protocol_sha256=digest(protocol),
        panel=panel, metrics=metrics, contrasts=contrasts, numpy_version=np.__version__, bit_generator='PCG64',
        source_data_sha256=report.get('data_sha256'), frozen_input_sha256=report.get('frozen_input_sha256'),
        array_axes=dict(losses=['resample', 'group', 'node', 'prediction', 'method', 'metric'],
            differences=['resample', 'group', 'node', 'prediction', 'comparison', 'metric'], metric=['rmse', 'mae']),
        policies=dict(model_refitted=False, se_generated=False, training_resampled=False, model_selected=False,
            parameter_confidence_interval=False, propagation_uncertainty_quantified=False,
            simultaneous_intervals=False, independent_new_test=False),
        limits=['仅描述当前冻结模型和留出基础样本的构成波动；不包括训练或求解器不确定性。',
                '中央95%经验范围不是参数置信区间，也不作为显著性或真实因果证据。',
                '负值重采样频率不是方法优越的概率；不据此选择模型。'])
    return metadata, indices, losses, differences


def markdown(report):
    lines = ['# 冻结预测成组稳定性：结果说明', '',
        f"按{len(report['panel']['base_cases'])}个留出基础样本成组抽样，每次同时保留全部环境、节点和方法；"
        f"有放回抽样{report['protocol']['resamples']}次，种子{report['protocol']['seed']}。", '',
        '**本步只描述冻结预测的样本构成波动，不是参数置信区间、显著性检验或新的独立测试。**', '',
        '差值为左方法RMSE−右方法RMSE，负值表示左方法误差更低；范围为重采样差值的2.5%和97.5%经验分位数。'
        '范围含零时保留方向不确定；范围不含零也不作统计显著性判断。', '',
        '[逐方法误差范围CSV](预测误差稳定性.csv) · [配对差值CSV](配对差值稳定性.csv) · [完整摘要JSON](stability_report.json) · '
        '[抽样索引](成组抽样索引.json) · [全部逐次结果NPZ](逐次指标与差值.npz)', '']
    for kind in KINDS:
        lines += ['## ' + ('局部留出' if kind == 'local' else '整网递推留出'), '',
            '| 环境 | 节点 | 比较 | 原RMSE差 | 中央95%经验范围 | 范围含零 | 负/近零/正次数 |',
            '| --- | --- | --- | ---: | --- | --- | --- |']
        for row in report['contrasts']:
            if row['prediction'] == kind and row['metric'] == 'rmse':
                lines.append(f"| {row['fit_group']} | {row['node']} | {row['left']} − {row['right']} | "
                    f"{row['original']:.6g} | [{row['q025']:.6g}, {row['q975']:.6g}] | "
                    f"{'是' if row['central_range_contains_zero'] else '否'} | "
                    f"{row['negative_count']}/{row['numerical_tie_count']}/{row['positive_count']} |")
        lines.append('')
    lines += ['## 解释限制', '', *['- ' + value for value in report['limits']],
        '- 参数成组重拟合、退化传播稳定性、拓扑敏感性和真实数据校准仍未完成。', '']
    return '\n'.join(lines)


def run(source_dir, output_dir, protocol_path):
    source = Path(source_dir); out = Path(output_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ModelError('输出目录须不存在或为空')
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    protocol = read(Path(protocol_path))
    if protocol != PROTOCOL:
        raise ModelError('稳定性方案与登记协议不一致')
    # Independently verify all frozen source predictions and prior package hashes first.
    from workflow_tools.verify_prediction_baselines import verify
    validation = verify(source)
    report, indices, losses, differences = analyze(read(source/'baseline_report.json'), protocol)
    report['source'] = dict(directory=str(source.resolve()), independent_verification=validation,
        baseline_report_sha256=hashlib.sha256((source/'baseline_report.json').read_bytes()).hexdigest())
    buffer = BytesIO(); np.savez_compressed(buffer, losses=losses, differences=differences)
    outputs = {'stability_report.json': dumps(report), '固定稳定性方案.json': dumps(protocol),
        '预测误差稳定性.csv': csv_text(report['metrics']), '配对差值稳定性.csv': csv_text(report['contrasts']),
        '成组抽样索引.json': dumps(dict(base_cases=report['panel']['base_cases'], indices=indices.tolist())),
        '逐次指标与差值.npz': buffer.getvalue(), '结果说明.md': markdown(report)}
    write_bundle(out, outputs)
    print(dumps(dict(base_cases=len(report['panel']['base_cases']), resamples=len(indices),
        metric_summaries=len(report['metrics']), contrast_summaries=len(report['contrasts']), policies=report['policies'])))
    return report


def main():
    parser = argparse.ArgumentParser(description='按留出基础样本成组重采样冻结预测，不重新拟合')
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(); run(args.source_dir, args.output_dir, args.protocol)


if __name__ == '__main__':
    main()
