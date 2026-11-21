"""Paired process innovations propagated through the independent-mechanism DAG."""
from copy import deepcopy
import math

from .generation import _number
from .generation_network import NetworkGenerator
from .generation_varying import run_varying_network_check
from .model import ModelError, _finite
from .structure import digest


def run_process_network_check(network,nodes,config):
    _finite(config)
    required={'format_version','id','basis','reference_experiment','source_group','group_id','process_std'}
    if not isinstance(config,dict) or set(config)!=required or config['format_version']!='1.0':
        raise ModelError('过程波动实验配置字段或版本无效')
    std=_number(config['process_std'],'过程波动标准差',0,100)
    if std==0: raise ModelError('过程波动扩展组标准差须大于0；无波动对照保留在来源组')
    reference=config['reference_experiment']
    report=run_varying_network_check(network,nodes,reference)
    source_group=config['source_group']; group=config['group_id']
    if source_group!=reference['condition_sampling']['group_id']:
        raise ModelError('本轮过程波动仅扩展逐接受节点变化条件组')
    if not isinstance(group,str) or not group or group in report['config_snapshot']['groups']:
        raise ModelError('过程波动组编号为空或与既有组重复')
    source_sha=digest(report)
    original={k:digest(report[k]) for k in ('root_inputs','clean_runs','observations')}
    original_counts={k:len(report[k]) for k in original}
    generator=NetworkGenerator(network,nodes); base=reference['base_experiment']
    source_runs={r['base_case_id']:r for r in report['clean_runs'] if r['group']==source_group}
    source_obs={(r['base_case_id'],r['noise_sigma']):r for r in report['observations'] if r['group']==source_group}
    import numpy as np
    process_rows=[]; added_runs=[]; added_obs=[]; structural=[]
    for rep,rep_seed in enumerate(np.random.SeedSequence(base['seed']).spawn(base['repetitions']),1):
        process_seed=rep_seed.spawn(5)[2]
        rng=np.random.default_rng(process_seed)
        # Match the frozen process substream protocol (sorted receivers, one vector each).
        draws={n:rng.standard_normal(base['base_cases']) for n in generator.receivers}
        for i,row in enumerate(r for r in report['root_inputs'] if r['repetition']==rep):
            source=source_runs[row['base_case_id']]; conditions=source['conditions_by_node']
            noise={n:float(std*draws[n][i]) for n in generator.receivers}
            evaluation=generator.evaluate(row['root_values_clean'],process_noise=noise,condition_values_by_node=conditions)
            trace={n:{k:v for k,v in detail.items() if k not in ('observation_noise','receiver_se_observed','observation_clipped')}
                   for n,detail in evaluation['node_details'].items()}
            run_id=group+':'+row['base_case_id']
            run=dict(network_run_id=run_id,base_case_id=row['base_case_id'],repetition=rep,group=group,split=row['split'],
                node_values_clean=evaluation['node_values_clean'],node_details=trace,conditions_by_node=deepcopy(conditions),
                process_noise_by_node=noise)
            added_runs.append(run)
            for n in generator.receivers:
                detail=trace[n]
                process_rows.append(dict(base_case_id=row['base_case_id'],repetition=rep,node=n,split=row['split'],
                    process_std=std,process_noise=noise[n],process_before_clip=detail['receiver_se_before_clip'],
                    process_clipped=detail['process_clipped'],formula_on_process_parents=detail['receiver_se_formula'],
                    reference_clean=source['node_values_clean'][n],process_clean=evaluation['node_values_clean'][n],
                    total_clean_shift=evaluation['node_values_clean'][n]-source['node_values_clean'][n]))
            for sigma in base['noise_std']:
                observation=source_obs[row['base_case_id'],sigma]['observation_noise'].copy()
                clean=evaluation['node_values_clean']
                added_obs.append(dict(observation_id=run_id+':sigma:'+str(sigma),network_run_id=run_id,
                    base_case_id=row['base_case_id'],repetition=rep,group=group,split=row['split'],noise_sigma=sigma,
                    node_values_clean=clean.copy(),node_values_observed={n:min(100,max(0,v+observation[n])) for n,v in clean.items()},
                    observation_noise=observation,conditions_by_node=deepcopy(conditions),process_noise_by_node=noise.copy()))
    for rep in range(1,base['repetitions']+1):
        for split in ('train','holdout'):
            for n in generator.receivers:
                rows=[r for r in added_runs if r['repetition']==rep and r['split']==split]
                deficit=[max(0,min(r['node_details'][n]['parent_clean'].values())-r['node_values_clean'][n]) for r in rows]
                structural.append(dict(group=group,repetition=rep,split=split,node=n,count=len(rows),
                    below_min_parent_fraction=sum(v>1e-10 for v in deficit)/len(rows),
                    clean_local_base_rmse_lower_bound=math.sqrt(math.fsum(d*d for d in deficit)/len(rows))))
    report['clean_runs'].extend(added_runs); report['observations'].extend(added_obs)
    lookup={r['network_run_id']:r for r in added_runs}
    checks=report['checks']
    checks.update(process_frozen_rows_unchanged=all(digest(report[k][:original_counts[k]])==original[k] for k in original),
        process_roots_unchanged=all(all(r['node_values_clean'][n]==source_runs[r['base_case_id']]['node_values_clean'][n]
                                      for n in generator.roots) for r in added_runs),
        process_conditions_unchanged=all(r['conditions_by_node']==source_runs[r['base_case_id']]['conditions_by_node'] for r in added_runs),
        process_and_clean_shared_across_observation_noise=all(r['process_noise_by_node']==lookup[r['network_run_id']]['process_noise_by_node']
            and r['node_values_clean']==lookup[r['network_run_id']]['node_values_clean'] for r in added_obs),
        process_observation_innovations_unchanged=all(r['observation_noise']==source_obs[r['base_case_id'],r['noise_sigma']]['observation_noise'] for r in added_obs),
        process_split_unchanged=all(r['split']==source_runs[r['base_case_id']]['split'] for r in added_runs),
        process_parent_continuity=all(d['parent_clean']=={p:r['node_values_clean'][p] for p in d['parent_clean']}
            for r in added_runs for d in r['node_details'].values()),
        process_applied_before_clip=all(math.isclose(d['receiver_se_before_clip'],d['receiver_se_formula']+d['process_noise'],abs_tol=1e-12)
            and d['receiver_se_clean']==min(100,max(0,d['receiver_se_before_clip']))
            for r in added_runs for d in r['node_details'].values() if d['kind']=='receiver'),
        process_values_in_range=all(math.isfinite(v) and 0<=v<=100 for r in added_obs
            for key in ('node_values_clean','node_values_observed') for v in r[key].values()),
        process_zero_observation_noise_equals_clean=all(r['node_values_clean']==r['node_values_observed'] for r in added_obs if r['noise_sigma']==0),
        process_ids_unique=len({r['observation_id'] for r in report['observations']})==len(report['observations']))
    report.update(stage='stage2_process_generation',source_varying_generation_sha256=source_sha,
        input_sha256=digest(dict(network=network,node_configs=nodes,experiment=config)),
        process_config_snapshot=deepcopy(config),process_samples=process_rows,process_structural_by_split=structural)
    report['config_snapshot']['groups'][group]=dict(source_group=source_group,process_std=std)
    report['counts'].update(clean_network_runs=len(report['clean_runs']),network_observation_versions=len(report['observations']),
        node_observation_rows=len(report['observations'])*len(generator.nodes),sampled_process_innovations=len(process_rows))
    report['policies'].update(process_rng_stream='process / sorted receiver vectors',process_innovations_shared_across_noise=True,
        clean_means='state_after_process_innovation_before_measurement',process_conditions_are_fdna_inputs=False)
    report['limits']=['过程波动组单独计数；相同基础样本的四档观测版本不视为独立实验。',
        'clean指未叠加观测噪声的真实模拟状态，已包含过程波动；过程影响向下游传递，观测误差不传递。',
        '当前仅变化条件上启用过程波动；尚未覆盖所有固定条件或波动强度。',
        '结构下界按含过程波动的实际干净状态重算，仅用于同数据基础式局部预测。']
    report['status']='complete' if all(checks.values()) else 'blocked'
    return report
