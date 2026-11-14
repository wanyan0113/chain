"""Supplementary artificial checks, separate from the article's requirements."""
from copy import deepcopy
import math

from .fitting import _libraries, _validate, fit_parameters, generate_samples
from .model import ModelError
from .structure import digest


def perturb_training(samples, std, seed):
    """Conditional Gaussian perturbation of training SE; holdout remains clean."""
    if isinstance(std, bool) or not isinstance(std, (int, float)) or not math.isfinite(std) or not 0 <= std <= 10:
        raise ModelError('人工扰动标准差须为[0,10]内的有限数值')
    np, _, _ = _libraries()
    rng = np.random.default_rng(seed)
    result = deepcopy(samples)
    rejected, count, squared = 0, 0, 0.0
    for row in result['records']:
        if row['split'] != 'train':
            continue
        for node, value in row['node_values'].items():
            if value is None or std == 0:
                continue
            for _ in range(100000):
                candidate = value+float(rng.normal(0, std))
                if 0 <= candidate <= 100:
                    break
                rejected += 1
            else:
                raise ModelError('扰动重采样未能产生SE范围内的值')
            row['node_values'][node] = candidate
            squared += (candidate-value)**2
            count += 1
    result['basis'] += f'; artificial conditional Gaussian training-SE perturbation std={std}'
    return result, {'perturbed_values': count, 'rejected_draws': rejected,
                    'actual_rms_perturbation': (squared/count)**.5 if count else 0.0}


def robustness_check(model, generation, settings, protocol):
    _validate('robustness', protocol)
    _validate('generation', generation)
    _validate('settings', settings)
    if 0 not in protocol['noise_std']:
        raise ModelError('稳定性检查须包含无扰动对照')
    if settings['model'] != (generation.get('extension') or {}).get('model', 'fdna_base_eq1_eq2'):
        raise ModelError('稳定性核验的生成模型与拟合模型不一致')
    truth = {(e['feeder'], e['receiver']): e for e in model['edges']}
    trials = []
    for seed in protocol['seeds']:
        gen = {**deepcopy(generation), 'seed': seed, 'random_train': protocol['random_train'],
               'random_holdout': protocol['random_holdout']}
        clean = generate_samples(model, gen)
        chosen_settings = {**deepcopy(settings), 'seed': seed, 'starts': protocol['starts']}
        for level_index, std in enumerate(protocol['noise_std']):
            noise_seed = [seed, level_index, 20261114]
            samples, perturbation = perturb_training(clean, std, noise_seed)
            fit = fit_parameters(model, samples, chosen_settings)
            receivers = []
            for receiver in fit['receivers']:
                row = deepcopy(receiver)
                if 'parameters' in receiver:
                    row['max_alpha_error'] = max(abs(p['alpha']-truth[p['feeder'], receiver['receiver']]['alpha'])
                                                 for p in receiver['parameters'])
                    known_delta = (gen.get('extension') or {}).get('delta_values', {}).get(receiver['receiver'])
                    row['delta_error'] = None if known_delta is None else receiver['delta']-known_delta
                receivers.append(row)
            trials.append({'seed': seed, 'noise_std': std, 'noise_seed': noise_seed,
                           'status': fit['status'], 'perturbation': perturbation,
                           'input_sha256': fit['input_sha256'], 'samples_snapshot': samples,
                           'generation_snapshot': gen, 'settings_snapshot': chosen_settings,
                           'receivers': receivers})
    aggregate = []
    for std in protocol['noise_std']:
        rows = [r for t in trials if t['noise_std'] == std for r in t['receivers'] if 'parameters' in r]
        errors = [r['metrics']['holdout']['rmse'] for r in rows if r['metrics']['holdout']]
        delta_errors = [abs(r['delta_error']) for r in rows if r['delta_error'] is not None]
        ranges = []
        for receiver in sorted({r['receiver'] for r in rows}):
            group = [r for r in rows if r['receiver'] == receiver]
            for i, p in enumerate(group[0]['parameters']):
                for field in ('alpha', 'beta'):
                    values = [r['parameters'][i][field] for r in group]
                    ranges.append({'receiver': receiver, 'parameter': field+':'+p['feeder'],
                                   'minimum': min(values), 'maximum': max(values)})
            if group[0]['delta'] is not None:
                values = [r['delta'] for r in group]
                ranges.append({'receiver': receiver, 'parameter': 'delta', 'minimum': min(values), 'maximum': max(values)})
        aggregate.append({'noise_std': std, 'trial_count': sum(t['noise_std'] == std for t in trials),
                          'max_holdout_rmse': max(errors) if errors else None,
                          'mean_holdout_rmse': sum(errors)/len(errors) if errors else None,
                          'max_alpha_error': max((r['max_alpha_error'] for r in rows), default=None),
                          'max_absolute_delta_error': max(delta_errors) if delta_errors else None,
                          'parameter_ranges': ranges})
    return {'format_version': '1.0', 'status': 'complete' if all(t['status'] == 'complete' for t in trials) else 'blocked',
            'scope': '额外人工稳定性诊断，不属于原文已记载的必需实验，也不提供原文误差分布。',
            'data_kind': 'method_check', 'case': model['id'], 'case_reproduction_ready': False,
            'input_sha256': digest({'case': model, 'generation': generation, 'settings': settings, 'protocol': protocol}),
            'protocol_snapshot': deepcopy(protocol), 'solver': fit['solver'],
            'trials': trials, 'aggregate': aggregate,
            'policies': {'holdout': 'clean_same_seed_samples_paired_across_noise_levels',
                         'noise': 'training_all_node_se_conditional_gaussian_resampled_to_0_100',
                         'clipping': 'none', 'parameter_update_to_case': False, 'acceptance_threshold': None},
            'limits': ['扰动同时加在训练集的供给及接受节点SE上，属于人为测量误差检查',
                       '边界外的扰动重新抽取，因此边界附近分布有偏；声明标准差不是实际扰动RMS',
                       '少量种子及扰动条件不能证明普遍稳健，不据此自动改参数或给能力排重要程度']}
