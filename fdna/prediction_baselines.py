"""Training-only simple baselines against frozen FDNA, with equal-information recursion."""
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .generation_fitting import _stats
from .model import ModelError, topology
from .network_workflow import predictors, recursive, validate_samples
from .structure import digest
from .workflow_common import csv_text, dumps, make_model, predict_receiver, write_bundle


PROTOCOL = dict(id='flow06_simple_prediction_v1', methods=['training_mean', 'ols_intercept'],
    training_rows='complete_local_training_rows', ols_solver='numpy.linalg.lstsq', ols_rcond=None,
    ols_rank_deficiency='retain_minimum_norm_and_flag', prediction_clipping=False,
    evaluation=['all_available', 'common_records'], holdout_role='descriptive_comparison_only_no_selection',
    recursive_inputs='original_roots_only', frozen_fdna='reuse_and_recompute_predictions_no_refit')


def fit_baselines(network, rows, config):
    """Only complete local training rows enter either estimator."""
    validate_samples(network, rows, config)
    incoming = {n: sorted(e['feeder'] for e in network['edges'] if e['receiver'] == n)
                for n in network['nodes']}
    fits = []
    for group in sorted({r['fit_group'] for r in rows}):
        selected = sorted((r for r in rows if r['fit_group'] == group), key=lambda r: r['sample_id'])
        for node, parents in sorted(incoming.items()):
            if not parents:
                continue
            train = [r for r in selected if r['split'] == 'train' and r['node_values'].get(node) is not None
                     and all(predictors(r, config).get(p) is not None for p in parents)]
            for method in PROTOCOL['methods']:
                fit = dict(fit_group=group, method=method, node=node, feeders=parents,
                    parameter_count=1 if method == 'training_mean' else len(parents) + 1,
                    train_count=len(train), train_sample_ids=[r['sample_id'] for r in train],
                    train_base_cases=len({r['base_case_id'] for r in train}), status='insufficient_samples',
                    coefficients=None, design_rank=None, flags=[])
                if train:
                    y = np.array([r['node_values'][node] for r in train], dtype=float)
                    if method == 'training_mean':
                        fit.update(coefficients=[float(np.mean(y))], design_rank=1, status='fitted')
                    else:
                        x = np.array([[1.] + [predictors(r, config)[p] for p in parents] for r in train])
                        coefficients, _, rank, singular = np.linalg.lstsq(x, y, rcond=None)
                        if not np.all(np.isfinite(coefficients)):
                            raise ModelError('线性基线产生非有限系数')
                        fit.update(coefficients=coefficients.tolist(), design_rank=int(rank),
                                   singular_values=singular.tolist(), status='fitted')
                        if rank < x.shape[1]:
                            fit['flags'].append('RANK_DEFICIENT_MINIMUM_NORM')
                fits.append(fit)
    return fits


def baseline_value(fit, values):
    if fit['method'] == 'training_mean':
        value = fit['coefficients'][0]
    else:
        value = math.fsum([fit['coefficients'][0]] +
            [c * values[p] for c, p in zip(fit['coefficients'][1:], fit['feeders'])])
    if not math.isfinite(value):
        raise ModelError('基线预测产生非有限数值')
    return value


def baseline_recursive(network, fits, roots):
    """Internal observed targets cannot enter this interface."""
    order = topology(make_model(network))
    incoming = {n: sorted(e['feeder'] for e in network['edges'] if e['receiver'] == n)
                for n in network['nodes']}
    values = {}
    for node in order:
        if not incoming[node]:
            values[node] = roots.get(node)
        elif node not in fits or fits[node]['status'] != 'fitted' or any(values[p] is None for p in incoming[node]):
            values[node] = None
        else:
            values[node] = baseline_value(fits[node], values)
    return values


def compare(report, rows, protocol):
    if protocol != PROTOCOL:
        raise ModelError('基线方案与已登记固定协议不一致')
    network = report['network_snapshot']; config = report['config_snapshot']
    groups = validate_samples(network, rows, config)
    if digest(rows) != report['data_sha256'] or digest(dict(network=network, rows=rows, config=config)) != report['input_sha256']:
        raise ModelError('基线数据、拓扑或配置与冻结报告摘要不一致')
    jobs = {(j['fit_group'], j['model']): j for j in report['jobs']}
    if len(jobs) != len(report['jobs']) or set(jobs) != {(g, m) for g in groups for m in config['models']}:
        raise ModelError('冻结模型分组不完整或重复')
    incoming = {n: sorted(e['feeder'] for e in network['edges'] if e['receiver'] == n)
                for n in network['nodes']}
    receivers = sorted(n for n in incoming if incoming[n]); roots = sorted(set(incoming) - set(receivers))
    terminals = set(network['nodes']) - {e['feeder'] for e in network['edges']}
    fits = fit_baselines(network, rows, config)
    complexity = [dict(fit_group=f['fit_group'], method=f['method'], node=f['node'],
        parameter_count=f['parameter_count'], train_count=f['train_count'], status=f['status'],
        local_rank=f['design_rank'], rank_meaning='training_design_matrix', flags=f['flags']) for f in fits]
    for job in report['jobs']:
        setting = config['models'][job['model']]
        for entry in job['local_fits']:
            node = entry['node']
            fit = (entry.get('fit') or {}).get('receivers', [{}])[0]
            complexity.append(dict(fit_group=job['fit_group'], method='fdna:' + job['model'], node=node,
                parameter_count=2*len(incoming[node])+int(setting['delta_policy'] == 'per_receiver_bounded'),
                train_count=(fit.get('metrics', {}).get('train') or {}).get('count', fit.get('train_rows')),
                status=fit.get('status'), local_rank=fit.get('sensitivity', {}).get('local_jacobian_rank'),
                rank_meaning='local_smooth_parameter_jacobian', flags=entry.get('flags', [])))
    points = []; max_difference = 0.; checks = 0
    for group in groups:
        selected = sorted((r for r in rows if r['fit_group'] == group), key=lambda r: r['sample_id'])
        methods = {method: {f['node']: f for f in fits if f['fit_group'] == group and f['method'] == method}
                   for method in PROTOCOL['methods']}
        frozen = {}
        for mode in config['models']:
            job = jobs[group, mode]
            saved = {(p['sample_id'], p['node']): p for p in job['points']}
            if len(saved) != len(job['points']) or set(saved) != {(r['sample_id'], n) for r in selected for n in receivers}:
                raise ModelError('冻结预测记录缺失、重复或多余')
            local = {f['node']: (f.get('fit') or {}).get('receivers', [{}])[0] for f in job['local_fits']}
            frozen['fdna:' + mode] = (local, saved)
        for row in selected:
            inputs = predictors(row, config); root_values = {n: inputs.get(n) for n in roots}
            for method in [*methods, *frozen]:
                is_fdna = method in frozen
                local = frozen[method][0] if is_fdna else methods[method]
                values = recursive(network, local, root_values)[0] if is_fdna else baseline_recursive(network, local, root_values)
                for node in receivers:
                    fit = local.get(node, {})
                    ready = fit.get('status') == 'fitted' and all(inputs.get(p) is not None for p in incoming[node])
                    lp = (predict_receiver(fit, inputs) if is_fdna else baseline_value(fit, inputs)) if ready else None
                    point = dict(fit_group=group, method=method, sample_id=row['sample_id'],
                        base_case_id=row['base_case_id'], scenario_id=row['scenario_id'], split=row['split'], node=node,
                        node_level='terminal' if node in terminals else 'intermediate',
                        target=row['node_values'].get(node), local_prediction=lp, recursive_prediction=values[node])
                    if is_fdna:
                        original = frozen[method][1][row['sample_id'], node]
                        if any(original[k] != point[k] for k in ('base_case_id', 'scenario_id', 'split', 'target')):
                            raise ModelError('冻结预测的目标或元数据与输入不一致')
                        for key in ('local_prediction', 'recursive_prediction'):
                            a, b = original[key], point[key]
                            if (a is None) != (b is None):
                                raise ModelError('冻结预测与原参数重算的受阻状态不一致')
                            if a is not None:
                                if not math.isfinite(a) or not math.isfinite(b):
                                    raise ModelError('冻结预测包含非有限数值')
                                difference = abs(a - b); max_difference = max(max_difference, difference); checks += 1
                                if difference > 1e-8:
                                    raise ModelError('冻结预测与原参数重算不一致')
                    points.append(point)
    metrics = evaluate_points(points)
    return dict(stage='frozen_fdna_simple_prediction_comparison', protocol=deepcopy(protocol),
        protocol_sha256=digest(protocol), data_sha256=report['data_sha256'], frozen_input_sha256=report['input_sha256'],
        network_snapshot=deepcopy(network), baseline_fits=fits, model_complexity=complexity, points=points, metrics=metrics,
        frozen_prediction_checks=checks, frozen_max_difference=max_difference, numpy_version=np.__version__,
        policies=dict(fdna_refitted=False, se_generated=False, holdout_used_for_training=False,
            holdout_used_for_method_selection=False, prediction_clipping=False, causal_effect_established=False),
        limits=['已有留出曾被查看，本次是追加描述性比较，不能当成新独立测试。',
                '基线系数不具有FDNA的αβ语义；不比较依赖参数准确性。',
                '不按本次留出选择方法，不据此宣布真实任务泛化优势。'])


def evaluate_points(points):
    buckets = defaultdict(list)
    for p in points:
        buckets[p['fit_group'], p['node'], p['split']].append(p)
    metrics = []
    for (group, node, split), own in sorted(buckets.items()):
        methods = sorted({p['method'] for p in own})
        for kind in ('local', 'recursive'):
            key = kind + '_prediction'
            valid_ids = [{p['sample_id'] for p in own if p['method'] == m and p['target'] is not None and p[key] is not None}
                         for m in methods]
            common = set.intersection(*valid_ids)
            for method in methods:
                candidates = [p for p in own if p['method'] == method]
                for scope in PROTOCOL['evaluation']:
                    valid = [p for p in candidates if p['target'] is not None and p[key] is not None
                             and (scope != 'common_records' or p['sample_id'] in common)]
                    stats = _stats([p['target'] for p in valid], [p[key] for p in valid]) if valid else dict(
                        count=0, rmse=None, mae=None, max_absolute_error=None, mean_residual=None, r_squared=None)
                    metrics.append(dict(fit_group=group, method=method, node=node, node_level=candidates[0]['node_level'],
                        split=split, prediction=kind, evaluation_scope=scope, available=len(candidates), evaluated=len(valid),
                        omitted=len(candidates)-len(valid), outside=sum(not 0 <= p[key] <= 100 for p in valid),
                        evaluated_sample_ids=[p['sample_id'] for p in valid], **stats))
    return metrics


def summary_markdown(report):
    lines = ['# 同条件简单预测基线：结果说明', '',
        '使用06冻结数据和原基础样本划分，FDNA参数未重新估计。训练均值和带截距线性回归只使用完整局部训练记录。', '',
        '**本次为追加的描述性对照，不是新的独立测试，也不按留出表现选择方法。**', '',
        '表中使用所有方法均可评估的共同留出记录；原始可用记录和共同记录的全部指标均保存在CSV/JSON中。'
        '局部用相同供给观测，整网仅用相同根输入。预测不裁剪。', '',
        f"冻结预测重算核验{report['frozen_prediction_checks']}个数值，最大差{report['frozen_max_difference']:.6g}。", '',
        '[预测指标CSV](预测指标.csv) · [逐条预测CSV](逐条预测.csv) · [基线系数CSV](基线系数.csv) · [模型复杂度CSV](模型复杂度.csv) · [完整报告JSON](baseline_report.json)', '',
        '复杂度表保存每个模型的名义待估参数数和局部秩；线性设计矩阵秩与FDNA局部Jacobian秩含义不同，不能直接当作同一种可信度。', '']
    metrics = report['metrics']
    methods = sorted({m['method'] for m in metrics})
    for kind in ('local', 'recursive'):
        lines += ['## ' + ('局部留出预测' if kind == 'local' else '整网递推留出预测'), '',
            '每个单元格为RMSE / MAE / 有效记录数 / 越界条数。', '',
            '| 环境 | 节点 | 层级 | ' + ' | '.join(methods) + ' |',
            '| --- | --- | --- | ' + ' | '.join('---' for _ in methods) + ' |']
        selected = [m for m in metrics if m['split'] == 'holdout' and m['prediction'] == kind and m['evaluation_scope'] == 'common_records']
        for group, node in sorted({(m['fit_group'], m['node']) for m in selected}):
            own = {m['method']: m for m in selected if m['fit_group'] == group and m['node'] == node}
            values = []
            for method in methods:
                m = own[method]
                fmt = lambda v: '—' if v is None else f'{v:.6g}'
                values.append(f"{fmt(m['rmse'])} / {fmt(m['mae'])} / {m['evaluated']} / {m['outside']}")
            lines.append('| ' + ' | '.join([group, node, own[methods[0]]['node_level'], *values]) + ' |')
        lines.append('')
    lines += ['## 解释范围', '', *['- ' + v for v in report['limits']],
        '- 训练均值只作为不使用供给特征的简单对照；线性回归不自动提供依赖语义。',
        '- 参数约束、模型表达、预测误差和真实依赖分别解释；某种方法误差更低不自动解决参数不可辨识。', '']
    return '\n'.join(lines)


def run(source_dir, output_dir, protocol_path):
    source = Path(source_dir); out = Path(output_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ModelError('输出目录须不存在或为空')
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    protocol = read(Path(protocol_path))
    if protocol != PROTOCOL:
        raise ModelError('基线方案与已登记固定协议不一致')
    from .flow_scenario import verify
    verified = verify(source)
    fitdir = source / '04_FDNA拟合'
    report = compare(read(fitdir / 'workflow_report.json'), read(fitdir / '输入_SE记录.json'), protocol)
    report['source'] = dict(directory=str(source.resolve()), verified_files=verified,
        package_index_sha256=hashlib.sha256((source / '完整包校验.json').read_bytes()).hexdigest(),
        role='synthetic_flow06_frozen_results')
    outputs = {'baseline_report.json': dumps(report), '固定比较方案.json': dumps(protocol),
        '预测指标.csv': csv_text(report['metrics']), '逐条预测.csv': csv_text(report['points']),
        '基线系数.csv': csv_text(report['baseline_fits']), '模型复杂度.csv': csv_text(report['model_complexity']),
        '结果说明.md': summary_markdown(report)}
    write_bundle(out, outputs)
    summary = dict(baseline_fits=len(report['baseline_fits']), prediction_rows=len(report['points']),
        metric_rows=len(report['metrics']), frozen_prediction_checks=report['frozen_prediction_checks'],
        frozen_max_difference=report['frozen_max_difference'], policies=report['policies'])
    print(dumps(summary))
    return report


def main():
    parser = argparse.ArgumentParser(description='同条件训练均值/线性基线与冻结FDNA比较，不重拟合FDNA')
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    run(args.source_dir, args.output_dir, args.protocol)


if __name__ == '__main__':
    main()
