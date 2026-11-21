"""Paired, prespecified coefficient sensitivity; preserves frozen controls."""
from copy import deepcopy
import math

from .generation_network import NetworkGenerator
from .generation_varying import run_varying_network_check
from .model import ModelError, _finite
from .structure import digest


def coefficient_groups(config):
    kinds = {'weight_groups', 'support_groups', 'condition_groups'} & set(config)
    if len(kinds) != 1:
        raise ModelError('一次实验须且仅指定一种生成系数组')
    key = next(iter(kinds))
    return key, config[key]


def affected_nodes(network, node):
    affected={node}
    while True:
        expanded=affected|{e['receiver'] for e in network['edges'] if e['feeder'] in affected}
        if expanded==affected:return affected
        affected=expanded


def run_coefficient_check(network, nodes, config):
    _finite(config)
    if not isinstance(config, dict): raise ModelError('系数实验配置须为对象')
    kind, groups = coefficient_groups(config)
    support = kind == 'support_groups'
    condition = kind == 'condition_groups'
    keys = {'format_version', 'id', 'basis', 'reference_experiment', kind}
    if not isinstance(config, dict) or set(config) != keys or config['format_version'] != '1.0':
        raise ModelError('系数敏感性配置字段或版本无效')
    if not isinstance(groups, dict) or not groups:
        raise ModelError('须预先指定权重组')
    report = run_varying_network_check(network, nodes, config['reference_experiment'])
    source = config['reference_experiment']['condition_sampling']['group_id']
    source_sha = digest(report)
    old_hashes = {k: digest(report[k]) for k in ('root_inputs', 'clean_runs', 'observations')}
    if set(groups) & set(report['config_snapshot']['groups']):
        raise ModelError('新权重组不得与冻结对照重复')
    generator = NetworkGenerator(network, nodes)
    receiver = 'T' if support else 'R'
    field = 'condition_function' if condition else ('support_coefficients' if support else 'weights')
    parents = {'A','B'} if support else {'Q','D_aux'}
    original_coefficients = nodes['nodes'][receiver].get(field)
    if support and (nodes['nodes']['T']['mechanism_type'] != 'complementary'
                    or not isinstance(original_coefficients, dict) or set(original_coefficients) != parents):
        raise ModelError('互补系数实验仅支持T的A/B供给')
    if not support and (nodes['nodes']['R']['mechanism_type'] != 'weighted'
            or set(nodes['nodes']['R']['weights']) != {'Q', 'D_aux'}
            or any(e['feeder'] == 'R' for e in network['edges'])):
        raise ModelError('本批仅支持末端R的Q/D_aux归一化权重敏感性')
    old_runs = {r['base_case_id']: r for r in report['clean_runs'] if r['group'] == source}
    old_obs = {(r['base_case_id'], r['noise_sigma']): r for r in report['observations'] if r['group'] == source}
    variants = {}; runs = []; observations = []; structural = []; shifts = []
    signatures = set()
    for group, weights in sorted(groups.items()):
        expected_keys = {'node','factor','value'} if condition else parents
        if not isinstance(group, str) or not group or not isinstance(weights, dict) or set(weights) != expected_keys:
            raise ModelError('系数组须完整指定对应供给节点')
        values = [weights['value']] if condition else weights.values()
        if (any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in values)
                or (not support and not condition and not math.isclose(sum(weights.values()), 1, abs_tol=1e-12, rel_tol=0))):
            raise ModelError('系数须在0到1；贡献权重之和须为1，不自动归一化')
        if support and sum(weights[p] != original_coefficients[p] for p in parents) != 1:
            raise ModelError('互补系数实验每组须且仅改变一个系数')
        if condition:
            if weights['node'] not in generator.receivers or weights['factor'] not in ('capability_gain','task_gain','environment_penalty'):
                raise ModelError('条件函数实验须指定已配置接受节点及能力增益、任务增益或环境惩罚')
            original_coefficients=nodes['nodes'][weights['node']]['condition_function']
        signature = (weights['node'],weights['factor'],weights['value']) if condition else tuple(weights[p] for p in sorted(parents))
        unchanged = weights['value']==original_coefficients[weights['factor']] if condition else weights==original_coefficients
        if signature in signatures or unchanged:
            raise ModelError('权重组不可重复或复制基线')
        signatures.add(signature)
        variant = deepcopy(nodes)
        if condition:
            f=variant['nodes'][weights['node']]['condition_function'];f[weights['factor']]=weights['value']
            if weights['factor'] in ('task_gain','capability_gain'):
                f[weights['factor'].replace('_gain','_base')]=1-weights['value']
        else:
            variant['nodes'][receiver][field] = deepcopy(weights)
        variants[group] = variant
        changed = NetworkGenerator(network, variant)
        for root in report['root_inputs']:
            original = old_runs[root['base_case_id']]; conditions = original['conditions_by_node']
            result = changed.evaluate(root['root_values_clean'], condition_values_by_node=conditions)
            trace = {n: {k: v for k, v in d.items() if k not in
                     ('observation_noise', 'receiver_se_observed', 'observation_clipped')}
                     for n, d in result['node_details'].items()}
            run_id = group + ':' + root['base_case_id']
            runs.append(dict(network_run_id=run_id, base_case_id=root['base_case_id'], repetition=root['repetition'],
                group=group, split=root['split'], conditions_by_node=deepcopy(conditions),
                node_values_clean=result['node_values_clean'], node_details=trace))
            for n in generator.receivers:
                shifts.append(dict(group=group, base_case_id=root['base_case_id'], repetition=root['repetition'],
                    split=root['split'], node=n, baseline=original['node_values_clean'][n],
                    changed=result['node_values_clean'][n], shift=result['node_values_clean'][n]-original['node_values_clean'][n]))
            for sigma in report['config_snapshot']['noise_std']:
                original_obs = old_obs[root['base_case_id'], sigma]; noise = original_obs['observation_noise'].copy()
                clean = result['node_values_clean']
                observations.append(dict(observation_id=run_id+':sigma:'+str(sigma), network_run_id=run_id,
                    base_case_id=root['base_case_id'], repetition=root['repetition'], group=group, split=root['split'],
                    noise_sigma=sigma, conditions_by_node=deepcopy(conditions), node_values_clean=clean.copy(),
                    node_values_observed={n: min(100, max(0, v+noise[n])) for n, v in clean.items()}, observation_noise=noise))
        for rep in range(1, report['config_snapshot']['repetitions']+1):
            for split in ('train', 'holdout'):
                selected = [r for r in runs if r['group']==group and r['repetition']==rep and r['split']==split]
                for n in generator.receivers:
                    deficits = [max(0, min(r['node_details'][n]['parent_clean'].values())-r['node_values_clean'][n]) for r in selected]
                    structural.append(dict(group=group, repetition=rep, split=split, node=n, count=len(deficits),
                        below_min_parent_fraction=sum(v>1e-10 for v in deficits)/len(deficits),
                        clean_local_base_rmse_lower_bound=math.sqrt(math.fsum(v*v for v in deficits)/len(deficits))))
    checks = report['checks']
    extended_condition=condition and any(g['node']!='R' or g['factor']=='capability_gain' for g in groups.values())
    invariant_nodes = generator.roots if support else [n for n in generator.nodes if n!='R']
    invariant_key = 'coefficient_root_values_unchanged' if support else 'coefficient_nonterminal_values_unchanged'
    if extended_condition:
        invariant_key='coefficient_unaffected_values_unchanged'
        unaffected={g:set(generator.nodes)-affected_nodes(network,spec['node']) for g,spec in groups.items()}
        checks[invariant_key]=all(all(r['node_values_clean'][n]==old_runs[r['base_case_id']]['node_values_clean'][n]
            for n in unaffected[r['group']]) for r in runs)
    else:
        checks[invariant_key] = all(all(r['node_values_clean'][n]==old_runs[r['base_case_id']]['node_values_clean'][n]
            for n in invariant_nodes) for r in runs)
    checks.update(coefficient_frozen_rows_unchanged=all(digest(report[k])==v for k,v in old_hashes.items()),
        coefficient_conditions_and_split_paired=all(r['conditions_by_node']==old_runs[r['base_case_id']]['conditions_by_node']
            and r['split']==old_runs[r['base_case_id']]['split'] for r in runs),
        coefficient_observation_noise_paired=all(r['observation_noise']==old_obs[r['base_case_id'],r['noise_sigma']]['observation_noise'] for r in observations),
        coefficient_parent_continuity=all(d['parent_clean']=={p:r['node_values_clean'][p] for p in d['parent_clean']}
            for r in runs for d in r['node_details'].values()),
        coefficient_values_in_range=all(0<=v<=100 for r in observations for k in ('node_values_clean','node_values_observed') for v in r[k].values()))
    report['clean_runs'].extend(runs); report['observations'].extend(observations)
    checks['coefficient_observation_ids_unique'] = len({r['observation_id'] for r in report['observations']})==len(report['observations'])
    report.update(stage='stage2_coefficient_generation', source_varying_generation_sha256=source_sha,
        coefficient_config_snapshot=deepcopy(config), variant_node_configs=variants,
        coefficient_shifts=shifts, coefficient_structural_by_split=structural,
        input_sha256=digest(dict(network=network, node_configs=nodes, experiment=config)))
    for group, weights in groups.items():
        report['config_snapshot']['groups'][group] = (dict(source_group=source,**deepcopy(weights)) if condition
            else dict(source_group=source, receiver=receiver, **{field:deepcopy(weights)}))
    report['counts'].update(clean_network_runs=len(report['clean_runs']), network_observation_versions=len(report['observations']),
        node_observation_rows=len(report['observations'])*len(generator.nodes))
    scope='terminal_R_conditions_one_at_a_time' if condition else ('T_complementary_one_at_a_time' if support else 'terminal_R_normalized_weight_pair')
    if extended_condition:scope='network_conditions_one_at_a_time'
    report['policies'].update(coefficient_scope=scope, coefficient_groups_prespecified=True)
    report['limits'] = ['本批只检查R权重对，不代表所有生成系数敏感性完成；两权重和为1，属于一个自由度。',
        '生成权重不是FDNA参数真值；跨权重组目标发生变化，RMSE差不是因果传播误差。',
        '共享基础输入、条件、划分及噪声创新；四档噪声不是独立生成实验。',
        '过程波动与交互关闭；未为改善拟合调整机制或δ界限。']
    if support:
        report['limits'][0] = '本批逐一改变T的互补支持系数，两系数不要求和为1；不代表条件函数敏感性完成。'
        report['limits'][1] = '生成支持系数不是FDNA参数真值；T的变化经干净状态传至Q/R，跨组RMSE差不是因果传播误差。'
    if condition:
        report['limits'][0] = '本批只检查R任务项及环境项的生成系数；任务基础项与增益项联动保持和为1，不代表全部条件函数敏感性完成。'
        report['limits'][1] = '生成条件函数系数不是FDNA参数真值；C/M/E样本不变，R目标随系数变化，RMSE差不是因果效应估计。'
    if extended_condition:
        report['limits'][0]='按接受节点逐一扰动条件函数系数；能力/任务的基础项与增益联动保持和为1，边界参数使用预设单侧扰动。'
        report['limits'][1]='仅当前节点及其下游允许改变干净状态；C/M/E样本、根输入和划分冻结，跨组RMSE差不是单独的因果传播量。'
    report['status'] = 'complete' if all(checks.values()) else 'blocked'
    return report
