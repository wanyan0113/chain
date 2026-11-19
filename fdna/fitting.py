"""Bounded local SE regression and explicitly artificial method verification."""
from copy import deepcopy
from itertools import product
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .engine import MODEL, evaluate
from .additive import ADDITIVE_MODEL, evaluate_additive, validate_extension
from .model import ModelError, _finite, inspect_model, validate_model
from .structure import digest


def _libraries():
    try:
        import numpy as np
        import scipy
        from scipy import optimize
    except ImportError as exc:
        raise ModelError('参数拟合需要 requirements-fdna.txt 中的SciPy依赖') from exc
    return np, scipy, optimize


def _validate(kind, value):
    _finite(value)
    schema = json.loads(Path(__file__).with_name('fitting_schema.json').read_text(encoding='utf-8'))
    errors = list(Draft202012Validator({'$defs': schema['$defs'], '$ref': '#/$defs/' + kind}).iter_errors(value))
    if errors:
        raise ModelError('拟合配置或SE样本不符合数据契约：' + errors[0].message)


def generate_samples(model, config):
    """Only leaf SE is varied. Intermediate values come from the network evaluator."""
    structure = inspect_model(model)
    _validate('generation', config)
    extension = config.get('extension')
    if extension is not None:
        validate_extension(model, extension)
        if any(v is None for v in extension['delta_values'].values()):
            raise ModelError('人工样本生成须明确全部δ，不能填入未知值')
    leaves = structure['exogenous_nodes']
    if config['include_leaf_corners'] and len(leaves) > 10:
        raise ModelError('角点核验最多支持10个无入边节点')
    if any(e['alpha'] is None or e['beta'] is None for e in model['edges']):
        raise ModelError('生成已知参数样本需要全部α、β已知')
    np, _, _ = _libraries()
    rng = np.random.default_rng(config['seed'])
    inputs = []
    if config['include_leaf_corners']:
        inputs.extend(('train', values) for values in product((0.0, 100.0), repeat=len(leaves)))
    for split, count in (('train', config['random_train']), ('holdout', config['random_holdout'])):
        inputs.extend((split, row) for row in rng.uniform(0, 100, (count, len(leaves))).tolist())
    rows = []
    for index, (split, values) in enumerate(inputs):
        scenario = {'id': f'artificial:{index}', 'basis': config['basis'], 'node_values': dict(zip(leaves, values))}
        computed = evaluate_additive(model, scenario, extension) if extension is not None else evaluate(model, scenario)
        if any(n['value'] is None or not 0 <= n['value'] <= 100 for n in computed['nodes']):
            raise ModelError('人工扩展样本超出SE范围；不裁剪或自动更改δ')
        rows.append({'id': scenario['id'], 'split': split,
                     'node_values': {n['id']: n['value'] for n in computed['nodes']}})
    return {'format_version': '1.0', 'id': config['id'], 'data_kind': 'method_check',
            'basis': ('人工无噪声δ扩展样本' if extension is not None else '人工无噪声基础式样本')+
                     '，仅改变无入边节点SE；'+config['basis'],
            'case_sha256': digest(model), 'records': rows}


def fit_parameters(model, samples, settings):
    validate_model(model)
    _validate('samples', samples)
    _validate('settings', settings)
    if samples['case_sha256'] != digest(model):
        raise ModelError('拟合样本与案例版本不一致')
    nodes = {n['id'] for n in model['nodes']}
    identifiers = set()
    for row in samples['records']:
        if row['id'] in identifiers or set(row['node_values']) - nodes:
            raise ModelError('样本标识重复或引用未登记能力节点')
        identifiers.add(row['id'])
    np, scipy, optimize = _libraries()
    solver = settings.get('solver', 'least_squares')
    rng = np.random.default_rng(settings['seed'])
    reports = []
    # Use only endpoint topology here; reported alpha/beta never initialize the solver.
    receivers = sorted({e['receiver'] for e in model['edges']})
    additive = settings['model'] == ADDITIVE_MODEL
    if additive:
        bounds = settings['delta_bounds']
        if set(bounds) != set(receivers) or any(pair[0] >= pair[1] for pair in bounds.values()):
            raise ModelError('δ界限须逐接受节点登记，且下界严格小于上界')
    initialization = settings.get('initialization')
    pca_report = None
    if initialization is not None:
        if set(initialization['receivers']) != set(receivers):
            raise ModelError('显式起点须恰好覆盖全部接受节点')
        from .pca import analyze_pca
        pca_report = analyze_pca(model, samples, initialization['pca'])
    for receiver in receivers:
        feeders = [e['feeder'] for e in model['edges'] if e['receiver'] == receiver]
        n = len(feeders)
        parameter_count = 2*n + int(additive)
        delta_low, delta_high = settings['delta_bounds'][receiver] if additive else (0, 0)
        delta_span = delta_high-delta_low
        configured = None
        if initialization is not None:
            initial = initialization['receivers'][receiver]
            params = {p['feeder']: p for p in initial['parameters']}
            if len(params) != len(initial['parameters']) or set(params) != set(feeders):
                raise ModelError('起点参数的供给节点须与案例逐一对应且不重复')
            if additive:
                if 'delta' not in initial or not delta_low <= initial['delta'] <= delta_high:
                    raise ModelError('扩展模型起点须明确δ，且在声明界限内')
            elif 'delta' in initial:
                raise ModelError('基础式起点不能包含δ')
            configured = np.array([params[f]['alpha'] for f in feeders] +
                                  [params[f]['beta']/100 for f in feeders] +
                                  ([(initial['delta']-delta_low)/delta_span] if additive else []))
        data, omitted = {}, []
        for split in ('train', 'holdout'):
            rows = []
            for row in samples['records']:
                if row['split'] != split:
                    continue
                if any(row['node_values'].get(key) is None for key in [*feeders, receiver]):
                    omitted.append({'id': row['id'], 'reason': 'MISSING_LOCAL_SE'})
                else:
                    rows.append(row)
            data[split] = rows
        if len(data['train']) < parameter_count:
            reports.append({'receiver': receiver, 'status': 'insufficient_samples',
                            'train_rows': len(data['train']), 'required_minimum': parameter_count, 'omitted': omitted})
            continue
        x = np.array([[r['node_values'][f] for f in feeders] for r in data['train']])
        y = np.array([r['node_values'][receiver] for r in data['train']])
        def predict(p, inputs):
            sod = np.mean(100 - p[:n] * (100-inputs), axis=1)
            cod = np.min(inputs + 100*p[n:2*n], axis=1)
            return np.minimum(sod, cod) + (delta_low+delta_span*p[-1] if additive else 0)
        def objective_gradient(p):
            # MSE and a branch gradient; at ties choose one active branch.
            sod = np.mean(100-p[:n]*(100-x), axis=1)
            cod_terms = x+100*p[n:2*n]
            cod = np.min(cod_terms, axis=1)
            sod_branch = sod <= cod
            residual = predict(p, x)-y
            jac = np.zeros((len(x), parameter_count))
            jac[sod_branch, :n] = -(100-x[sod_branch])/n
            cod_rows = np.flatnonzero(~sod_branch)
            jac[cod_rows, n+np.argmin(cod_terms[cod_rows], axis=1)] = 100
            if additive:
                jac[:, -1] = delta_span
            return float(np.mean(residual**2)), 2*jac.T@residual/len(x)
        starts = ([configured] if configured is not None else []) + [np.full(parameter_count, .5)]
        start_sources = (['explicit_configuration'] if configured is not None else []) + ['neutral']
        random_count = settings['starts'] - len(starts)
        starts += [rng.uniform(.01, .99, parameter_count) for _ in range(random_count)]
        start_sources += ['seeded_random'] * random_count
        attempts = []
        for start, start_source in zip(starts, start_sources):
            if solver == 'L-BFGS-B':
                solved = optimize.minimize(objective_gradient, start, jac=True, method='L-BFGS-B',
                                           bounds=[(0, 1)]*parameter_count,
                                           options={'maxfun': settings['max_nfev'],
                                                    'maxiter': settings['max_nfev'],
                                                    'ftol': 1e-15, 'gtol': 1e-9, 'maxls': 50})
            else:
                solved = optimize.least_squares(lambda p: predict(p, x)-y, start, bounds=(0, 1),
                                                max_nfev=settings['max_nfev'], ftol=1e-11, xtol=1e-11, gtol=1e-11)
            rmse = float(np.sqrt(np.mean((predict(solved.x, x)-y)**2)))
            attempts.append((rmse, solved, start, start_source))
        attempts.sort(key=lambda item: item[0])
        best_rmse, best, _, _ = attempts[0]
        parameter_names = [f'alpha:{f}' for f in feeders] + [f'beta:{f}' for f in feeders]
        parameter_names += ['delta'] if additive else []
        scale = np.array([1.0]*n + [100.0]*n + ([delta_span] if additive else []))
        offset = np.array([0.0]*(2*n) + ([delta_low] if additive else []))
        physical = best.x*scale+offset
        sod = np.mean(100-best.x[:n]*(100-x), axis=1)
        cod_terms = x+100*best.x[n:2*n]
        cod = np.min(cod_terms, axis=1)
        margin = 1e-7
        sod_active = sod < cod-margin
        limiting = np.argmin(cod_terms, axis=1)
        cod_unique = (np.sort(cod_terms, axis=1)[:, 1]-cod > margin) if n > 1 else np.ones(len(x), dtype=bool)
        cod_active = (cod < sod-margin) & cod_unique
        # Ignore nonsmooth ties: numerical differences there can spuriously suggest sensitivity.
        jacobian = np.zeros((len(x), parameter_count))
        jacobian[sod_active, :n] = -(100-x[sod_active])/n
        for i in range(n):
            jacobian[cod_active & (limiting == i), n+i] = 100
        if additive:
            jacobian[sod_active | cod_active, -1] = delta_span
        # Rank is local sensitivity on smooth rows, not global uniqueness.
        singular = np.linalg.svd(jacobian, compute_uv=False)
        rank = int(np.sum(singular > (singular[0]*1e-7 if len(singular) and singular[0] else 1e-7)))
        inactive = [parameter_names[i] for i in range(parameter_count) if np.linalg.norm(jacobian[:, i]) < 1e-7]
        near = [sol.x*scale+offset for rmse, sol, _, _ in attempts if rmse <= best_rmse + max(1e-6, best_rmse*.01)]
        spread = np.ptp(np.array(near), axis=0)
        evaluations = {}
        for split, rows in data.items():
            if not rows:
                evaluations[split] = None
                continue
            inputs = np.array([[r['node_values'][f] for f in feeders] for r in rows])
            actual = np.array([r['node_values'][receiver] for r in rows])
            predicted = predict(best.x, inputs)
            residuals = predicted-actual
            evaluations[split] = {'count': len(rows), 'rmse': float(np.sqrt(np.mean(residuals**2))),
                                  'max_absolute_error': float(np.max(np.abs(residuals))),
                                  'rows': [{'id': row['id'], 'observed': float(a), 'predicted': float(p),
                                            'residual': float(p-a)} for row, a, p in zip(rows, actual, predicted)]}
        reports.append({'receiver': receiver, 'status': 'fitted' if best.success else 'iteration_limit',
                        'feeders': feeders, 'parameters': [
                            {'feeder': f, 'alpha': float(physical[i]), 'beta': float(physical[n+i])}
                            for i, f in enumerate(feeders)],
                        'delta': float(physical[-1]) if additive else None,
                        'delta_bounds': [delta_low, delta_high] if additive else None,
                        'metrics': evaluations, 'omitted': omitted,
                        'sensitivity': {'local_jacobian_rank': rank, 'parameter_count': parameter_count,
                                        'inactive_parameters': inactive, 'global_uniqueness_established': False,
                                        'method': 'analytic_scaled_jacobian_on_smooth_rows',
                                        'tie_tolerance': margin,
                                        'excluded_tie_rows': int(np.sum(~(sod_active | cod_active))),
                                        'sod_active_rows': int(np.sum(sod_active)),
                                        'cod_active_rows': {f: int(np.sum(cod_active & (limiting == i)))
                                                            for i, f in enumerate(feeders)}},
                        'near_best_start_count': len(near),
                        'near_best_parameter_spread': dict(zip(parameter_names, map(float, spread))),
                        'attempts': [{'rmse': rmse, 'success': bool(sol.success), 'status': int(sol.status),
                                      'nfev': int(sol.nfev), 'message': str(sol.message),
                                      'initial': list(map(float, start)),
                                      'initial_source': start_source,
                                      'parameters': list(map(float, sol.x*scale+offset))}
                                     for rmse, sol, start, start_source in attempts]})
    return {'format_version': '1.0', 'status': 'complete' if reports and all(r['status'] == 'fitted' for r in reports) else 'blocked',
            'case': model['id'], 'data_kind': samples['data_kind'],
            'scope': ('按显式界限逐接受节点拟合α、β、δ；不裁剪，不认定原文δ为逐节点常量。' if additive else
                      '基础式α、β的有界局部回归；δ明确排除，不代替原文扩展模型。'),
            'input_sha256': digest({'case': model, 'samples': samples, 'settings': settings}),
            'receivers': reports, 'samples_snapshot': deepcopy(samples), 'settings_snapshot': deepcopy(settings),
            'source_snapshot': deepcopy(model['sources']), 'case_reproduction_ready': False,
            'pca_diagnostics': pca_report,
            'solver': {'name': ('scipy.optimize.minimize' if solver == 'L-BFGS-B' else 'scipy.optimize.least_squares'),
                       'method': solver if solver == 'L-BFGS-B' else 'trf',
                       'objective': ('mean_squared_residual' if solver == 'L-BFGS-B' else 'half_sum_squared_residual'),
                       'gradient': ('analytic_active_branch_with_one_branch_at_ties' if solver == 'L-BFGS-B' else 'finite_difference_residual_jacobian'),
                       'scipy': scipy.__version__, 'numpy': np.__version__,
                       'parameterization': 'alpha=p[0:n], beta=100*p[n:2n]' +
                                           (', delta=lower+(upper-lower)*p[-1]' if additive else ''),
                       'bounds': {'alpha': [0, 1], 'beta': [0, 100],
                                  'delta': deepcopy(settings['delta_bounds']) if additive else None},
                       'initialization': ('explicit_physical_parameters_plus_neutral_and_seeded_random'
                                          if initialization is not None else 'neutral_and_seeded_random_not_reported_parameters')},
            'policies': {'model': settings['model'], 'delta': settings['delta_policy'], 'clipping': 'none',
                         'holdout_used_for_optimization': False, 'pseudo_r_squared': None},
            'limits': ['人工无噪声样本只检验求法，不代表原文六千余次推演',
                       '局部敏感度秩及多起点差异只能诊断可辨识性，不能证明全局唯一或全局最优',
                       'δ是否共用、界限及输出处理均未获原文确认；未复现原文PCA到起点的映射或伪R²，残差为预测值减观测值',
                       'PCA仅作训练供给变量诊断；显式起点来自配置，不由载荷自动换算，也不证明因果或参数唯一']}


def fit_check(model, generation, settings):
    _validate('generation', generation)
    _validate('settings', settings)
    expected = ADDITIVE_MODEL if generation.get('extension') is not None else MODEL
    if settings.get('model') != expected:
        raise ModelError('人工样本的生成模型与拟合模型不一致')
    if expected == ADDITIVE_MODEL:
        validate_extension(model, generation['extension'])
        for receiver, value in generation['extension']['delta_values'].items():
            if (value is None or receiver not in settings['delta_bounds'] or
                    not settings['delta_bounds'][receiver][0] <= value <= settings['delta_bounds'][receiver][1]):
                raise ModelError('人工设定δ须已知且在声明的拟合界限内')
    samples = generate_samples(model, generation)
    report = fit_parameters(model, samples, settings)
    truth = {(e['feeder'], e['receiver']): e for e in model['edges']}
    comparisons = []
    for receiver in report['receivers']:
        for p in receiver.get('parameters', []):
            original = truth[p['feeder'], receiver['receiver']]
            comparisons.append({'feeder': p['feeder'], 'receiver': receiver['receiver'],
                                'known_alpha': original['alpha'], 'known_beta': original['beta'],
                                'fitted_alpha': p['alpha'], 'fitted_beta': p['beta'],
                                'alpha_error': p['alpha']-original['alpha'], 'beta_error': p['beta']-original['beta']})
    report.update(generation_snapshot=deepcopy(generation), known_parameter_comparison=comparisons,
                  verification_sha256=digest({'fit_input_sha256': report['input_sha256'], 'generation': generation}))
    if expected == ADDITIVE_MODEL:
        report['known_delta_comparison'] = [
            {'receiver': r['receiver'], 'known_delta': generation['extension']['delta_values'][r['receiver']],
             'fitted_delta': r['delta'], 'delta_error': r['delta']-generation['extension']['delta_values'][r['receiver']]}
            for r in report['receivers'] if r['status'] in ('fitted', 'iteration_limit')]
    return report
