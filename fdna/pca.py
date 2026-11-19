"""Training-only PCA diagnostics; no automatic PCA-to-FDNA parameter mapping."""
from copy import deepcopy

from .fitting import _libraries, _validate
from .model import ModelError, validate_model
from .structure import digest


def analyze_pca(model, samples, config):
    validate_model(model)
    _validate('samples', samples)
    _validate('pca', config)
    if samples['case_sha256'] != digest(model):
        raise ModelError('PCA样本与案例版本不一致')
    nodes = {n['id'] for n in model['nodes']}
    identifiers = set()
    for row in samples['records']:
        if row['id'] in identifiers or set(row['node_values']) - nodes:
            raise ModelError('PCA样本标识重复或引用未登记能力节点')
        identifiers.add(row['id'])
    np, _, _ = _libraries()
    training = [r for r in samples['records'] if r['split'] == 'train']
    topology = {r: [e['feeder'] for e in model['edges'] if e['receiver'] == r]
                for r in sorted({e['receiver'] for e in model['edges']})}
    reports = []
    for receiver, feeders in topology.items():
        rows = [r for r in training if all(r['node_values'].get(n) is not None for n in [receiver, *feeders])]
        selected_ids = {r['id'] for r in rows}
        report = dict(receiver=receiver, feeders=feeders, train_rows=len(rows),
                      train_ids=[r['id'] for r in rows],
                      omitted_train_ids=[r['id'] for r in training if r['id'] not in selected_ids],
                      components=[], retained_count=0)
        if len(rows) < 2:
            report['status'] = 'insufficient_samples'
            reports.append(report)
            continue
        x = np.array([[r['node_values'][f] for f in feeders] for r in rows], dtype=float)
        means = x.mean(axis=0)
        std = x.std(axis=0, ddof=1)
        varying = np.ptp(x, axis=0) > 0
        scale = np.where(varying, std, 1.) if config['scaling'] == 'unit_variance' else np.ones(len(feeders))
        z = (x - means) / scale
        report.update(means=dict(zip(feeders, map(float, means))),
                      scales=dict(zip(feeders, map(float, scale))),
                      constant_feeders=[f for f, varies in zip(feeders, varying) if not varies])
        if not varying.any():
            report.update(status='no_variation', rank=0, repeated_eigenvalues=False)
            reports.append(report)
            continue
        _, singular, vectors = np.linalg.svd(z[:, varying], full_matrices=False)
        tolerance = float(singular[0] * max(z[:, varying].shape) * np.finfo(float).eps)
        rank = int(np.sum(singular > tolerance))
        eigenvalues = singular[:rank] ** 2 / (len(rows) - 1)
        ratios = eigenvalues / eigenvalues.sum()
        cumulative = np.cumsum(ratios)
        retained = min(rank, int(np.searchsorted(cumulative, config['variance_target'])) + 1)
        for index in range(rank):
            vector = vectors[index].copy()
            # PCA signs are arbitrary; this convention stabilizes displayed signs.
            if vector[np.argmax(np.abs(vector))] < 0:
                vector *= -1
            loading = np.zeros(len(feeders))
            loading[varying] = vector
            report['components'].append(dict(component=index + 1,
                eigenvalue=float(eigenvalues[index]), variance_ratio=float(ratios[index]),
                cumulative_ratio=float(cumulative[index]), retained=index < retained,
                loadings=dict(zip(feeders, map(float, loading)))))
        repeated = any(abs(a-b) <= 1e-10 * eigenvalues[0] for a, b in zip(eigenvalues, eigenvalues[1:]))
        report.update(status='analyzed', rank=rank, rank_tolerance=tolerance,
                      retained_count=retained, repeated_eigenvalues=repeated)
        reports.append(report)
    return dict(format_version='1.0', case=model['id'], data_kind=samples['data_kind'],
        status='complete' if reports and all(r['status'] == 'analyzed' for r in reports) else 'blocked',
        scope='第5.3节PCA环节的方法实现：仅分析训练集中供给节点的共同变化；不自动反推出α、β。',
        train_sha256=digest(dict(topology=topology, training=training, config=config)),
        config_snapshot=deepcopy(config), receivers=reports, case_reproduction_ready=False,
        policies=dict(holdout_used=False, receiver_used_as_pca_feature=False,
                      local_complete_rows_only=True, covariance_denominator='n_minus_1',
                      pca_to_parameter_mapping='not_specified_by_article',
                      reported_parameters_used=False),
        limits=['PCA解释供给变量的共同变化，不单独证明因果影响或依赖强度',
                '标准化与保留方差阈值为显式配置，不能认定等同原文未公开细节',
                '重特征值对应子空间中的分量方向不唯一，固定符号不消除这种不唯一性',
                '原始推演数据及PCA到α、β起点的映射缺失，不能声称原文起点已复现'])
