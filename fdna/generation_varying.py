"""Paired per-receiver external-condition experiment, preserving frozen controls."""
from copy import deepcopy
import math

from .generation import _number
from .generation_network import NetworkGenerator
from .generation_network_check import run_network_check
from .model import ModelError, _finite
from .structure import digest


def run_varying_network_check(network, nodes, config):
    _finite(config)
    if not isinstance(config, dict) or set(config) != {'format_version','id','basis','base_experiment','condition_sampling'} or config['format_version'] != '1.0':
        raise ModelError('变化条件实验配置字段或版本无效')
    sampling = config['condition_sampling']; base = config['base_experiment']
    if not isinstance(sampling, dict) or set(sampling) != {'group_id','mode','ranges'} or sampling['mode'] != 'per_receiver_independent':
        raise ModelError('首轮仅支持逐接受节点独立条件抽样')
    group = sampling['group_id']
    if not isinstance(group,str) or not group or group in base['groups']:
        raise ModelError('变化条件组编号为空或与冻结组重复')
    ranges = sampling['ranges']
    if not isinstance(ranges,dict) or set(ranges) != {'C','M','E'}:
        raise ModelError('抽样区间须恰好覆盖C、M、E')
    for bounds in ranges.values():
        if not isinstance(bounds,list) or len(bounds)!=2:
            raise ModelError('条件抽样须为上下界数组')
        for value in bounds: _number(value,'条件界限',0,1)
        if bounds[0]>=bounds[1]: raise ModelError('抽样下界须小于上界')
    if base['process_std']!=0 or base['interaction_gamma']!=0:
        raise ModelError('首轮变化条件实验不同时引入过程波动或交互')
    report = run_network_check(network,nodes,base)
    baseline_sha = digest(report)
    old_counts = {k:len(report[k]) for k in ('root_inputs','clean_runs','observations')}
    old_hashes = {k:digest(report[k]) for k in old_counts}
    generator = NetworkGenerator(network,nodes)
    old_obs = {(r['base_case_id'],r['noise_sigma']):r for r in report['observations'] if r['group']==sorted(base['groups'])[0]}
    import numpy as np
    condition_rows = []; structural = []
    for rep, rep_seed in enumerate(np.random.SeedSequence(base['seed']).spawn(base['repetitions']),1):
        stream = rep_seed.spawn(5)[1]
        values = {}
        for node,node_seed in zip(generator.receivers,stream.spawn(len(generator.receivers))):
            values[node] = {key:np.random.default_rng(seed).uniform(*ranges[key],base['base_cases'])
                           for key,seed in zip(('C','M','E'),node_seed.spawn(3))}
        bases = [r for r in report['root_inputs'] if r['repetition']==rep]
        for i,row in enumerate(bases):
            conditions = {n:{k:float(v[i]) for k,v in entries.items()} for n,entries in values.items()}
            result = generator.evaluate(row['root_values_clean'],condition_values_by_node=conditions)
            run_id = group+':'+row['base_case_id']
            trace = {n:{k:v for k,v in detail.items() if k not in ('observation_noise','receiver_se_observed','observation_clipped')}
                     for n,detail in result['node_details'].items()}
            report['clean_runs'].append(dict(network_run_id=run_id,base_case_id=row['base_case_id'],repetition=rep,
                group=group,split=row['split'],node_values_clean=result['node_values_clean'],node_details=trace,
                conditions_by_node=deepcopy(conditions)))
            for node,c in conditions.items():
                condition_rows.append(dict(base_case_id=row['base_case_id'],repetition=rep,node=node,split=row['split'],**c))
            for sigma in base['noise_std']:
                noise = old_obs[row['base_case_id'],sigma]['observation_noise'].copy()
                clean = result['node_values_clean']
                report['observations'].append(dict(observation_id=run_id+':sigma:'+str(sigma),network_run_id=run_id,
                    base_case_id=row['base_case_id'],repetition=rep,group=group,split=row['split'],noise_sigma=sigma,
                    node_values_clean=clean.copy(),node_values_observed={n:min(100,max(0,v+noise[n])) for n,v in clean.items()},
                    observation_noise=noise,conditions_by_node=deepcopy(conditions)))
        runs = [r for r in report['clean_runs'] if r['group']==group and r['repetition']==rep]
        for split in ('train','holdout'):
            for node in generator.receivers:
                deficits = [max(0,min(r['node_details'][node]['parent_clean'].values())-r['node_values_clean'][node])
                            for r in runs if r['split']==split]
                structural.append(dict(group=group,repetition=rep,split=split,node=node,count=len(deficits),
                    below_min_parent_fraction=sum(d>1e-10 for d in deficits)/len(deficits),
                    clean_local_base_rmse_lower_bound=math.sqrt(math.fsum(d*d for d in deficits)/len(deficits))))
    by_run = {r['network_run_id']:r for r in report['clean_runs']}
    new_obs = [r for r in report['observations'] if r['group']==group]
    checks = report['checks']
    checks.update(frozen_control_rows_unchanged=all(digest(report[k][:n])==old_hashes[k] for k,n in old_counts.items()),
        conditions_shared_across_noise=all(r['conditions_by_node']==by_run[r['network_run_id']]['conditions_by_node'] for r in new_obs),
        paired_noise_unchanged=all(r['observation_noise']==old_obs[r['base_case_id'],r['noise_sigma']]['observation_noise'] for r in new_obs),
        varying_split_unchanged=all(r['split']==old_obs[r['base_case_id'],r['noise_sigma']]['split'] for r in new_obs),
        varying_parent_continuity=all(d['parent_clean']=={p:r['node_values_clean'][p] for p in d['parent_clean']}
            for r in report['clean_runs'] if r['group']==group for d in r['node_details'].values()),
        varying_values_in_range=all(math.isfinite(v) and 0<=v<=100 for r in new_obs
            for key in ('node_values_clean','node_values_observed') for v in r[key].values()),
        varying_zero_noise_equals_clean=all(r['node_values_clean']==r['node_values_observed'] for r in new_obs if r['noise_sigma']==0),
        all_observation_ids_unique=len({r['observation_id'] for r in report['observations']})==len(report['observations']))
    report.update(stage='stage2_varying_conditions_generation',source_baseline_sha256=baseline_sha,
        input_sha256=digest(dict(network=network,node_configs=nodes,experiment=config)),
        varying_config_snapshot=deepcopy(config),condition_samples=condition_rows,structural_by_split=structural)
    report['config_snapshot']['groups'][group] = {'sampling_mode':sampling['mode'],'ranges':deepcopy(ranges)}
    report['counts'].update(clean_network_runs=len(report['clean_runs']),network_observation_versions=len(report['observations']),
        node_observation_rows=len(report['observations'])*len(generator.nodes),sampled_receiver_conditions=len(condition_rows))
    report['policies'].update(condition_rng_stream='reserved_conditions / sorted receiver / C,M,E',
        condition_sharing='per_receiver_independent',conditions_are_fdna_inputs=False)
    report['limits'] = ['条件联合变化只支持描述性分层分析，不识别单因素因果贡献；C/M/E只进入生成端。',
        '每个基础网络的四档噪声为配对观测，不是四次独立推演。',
        '结构下界只适用于同划分、干净目标、真实上游输入的基础式局部预测；不用于含δ或整网误差。']
    report['status'] = 'complete' if all(checks.values()) else 'blocked'
    return report
