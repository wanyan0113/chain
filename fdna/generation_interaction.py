"""Registered interaction strengths on paired, process-free varying conditions."""
from copy import deepcopy
import math

from .generation import _number
from .generation_network import NetworkGenerator
from .generation_varying import run_varying_network_check
from .model import ModelError, _finite
from .structure import digest


def run_interaction_network_check(network,nodes,config):
    _finite(config)
    keys={'format_version','id','basis','reference_experiment','source_group','gamma_groups','primary_group'}
    if not isinstance(config,dict) or set(config)!=keys or config['format_version']!='1.0':
        raise ModelError('交互实验配置字段或版本无效')
    groups=config['gamma_groups']
    if not isinstance(groups,dict) or not groups or config['primary_group'] not in groups:
        raise ModelError('交互强度组或主实验组无效')
    for group,gamma in groups.items():
        if not isinstance(group,str) or not group: raise ModelError('交互组编号须为非空字符串')
        if _number(gamma,'交互强度',0,1)==0: raise ModelError('γ=0使用冻结来源组，不重复建立交互组')
    if len(set(groups.values()))!=len(groups): raise ModelError('不同交互组不得重复使用同一强度')
    reference=config['reference_experiment']
    report=run_varying_network_check(network,nodes,reference)
    source_group=config['source_group']
    if source_group!=reference['condition_sampling']['group_id']:
        raise ModelError('首轮交互实验以无过程波动的变化条件组为来源')
    if set(groups)&set(report['config_snapshot']['groups']): raise ModelError('交互组编号与既有组重复')
    source_sha=digest(report)
    counts={k:len(report[k]) for k in ('root_inputs','clean_runs','observations')}
    hashes={k:digest(report[k]) for k in counts}
    generator=NetworkGenerator(network,nodes)
    source_runs={r['base_case_id']:r for r in report['clean_runs'] if r['group']==source_group}
    source_obs={(r['base_case_id'],r['noise_sigma']):r for r in report['observations'] if r['group']==source_group}
    base=reference['base_experiment'];samples=[];structural=[];new_runs=[];new_obs=[]
    for group,gamma in sorted(groups.items()):
        for row in report['root_inputs']:
            source=source_runs[row['base_case_id']];conditions=source['conditions_by_node']
            result=generator.evaluate(row['root_values_clean'],interaction_gamma=gamma,condition_values_by_node=conditions)
            trace={n:{k:v for k,v in detail.items() if k not in ('observation_noise','receiver_se_observed','observation_clipped')}
                   for n,detail in result['node_details'].items()}
            run_id=group+':'+row['base_case_id']
            new_runs.append(dict(network_run_id=run_id,base_case_id=row['base_case_id'],repetition=row['repetition'],
                group=group,split=row['split'],interaction_gamma=gamma,conditions_by_node=deepcopy(conditions),
                node_values_clean=result['node_values_clean'],node_details=trace))
            for n in generator.receivers:
                d=trace[n]
                samples.append(dict(group=group,interaction_gamma=gamma,base_case_id=row['base_case_id'],repetition=row['repetition'],
                    node=n,split=row['split'],C=conditions[n]['C'],M=conditions[n]['M'],E=conditions[n]['E'],
                    local_interaction_deduction=d['interaction_deduction'],formula_on_interaction_parents=d['receiver_se_formula'],
                    state_before_clip=d['receiver_se_before_clip'],state_clipped=d['process_clipped'],
                    reference_clean=source['node_values_clean'][n],interaction_clean=result['node_values_clean'][n],
                    total_clean_shift=result['node_values_clean'][n]-source['node_values_clean'][n]))
            for sigma in base['noise_std']:
                noise=source_obs[row['base_case_id'],sigma]['observation_noise'].copy();clean=result['node_values_clean']
                new_obs.append(dict(observation_id=run_id+':sigma:'+str(sigma),network_run_id=run_id,
                    base_case_id=row['base_case_id'],repetition=row['repetition'],group=group,split=row['split'],
                    noise_sigma=sigma,interaction_gamma=gamma,conditions_by_node=deepcopy(conditions),
                    node_values_clean=clean.copy(),node_values_observed={n:min(100,max(0,v+noise[n])) for n,v in clean.items()},
                    observation_noise=noise))
        for rep in range(1,base['repetitions']+1):
            for split in ('train','holdout'):
                rows=[r for r in new_runs if r['group']==group and r['repetition']==rep and r['split']==split]
                for n in generator.receivers:
                    deficits=[max(0,min(r['node_details'][n]['parent_clean'].values())-r['node_values_clean'][n]) for r in rows]
                    structural.append(dict(group=group,interaction_gamma=gamma,repetition=rep,split=split,node=n,count=len(rows),
                        below_min_parent_fraction=sum(v>1e-10 for v in deficits)/len(rows),
                        clean_local_base_rmse_lower_bound=math.sqrt(math.fsum(d*d for d in deficits)/len(rows))))
    lookup={r['network_run_id']:r for r in new_runs}
    checks=report['checks']
    checks.update(interaction_old_rows_unchanged=all(digest(report[k])==hashes[k] for k in counts),
        interaction_roots_unchanged=all(all(r['node_values_clean'][n]==source_runs[r['base_case_id']]['node_values_clean'][n]
                                         for n in generator.roots) for r in new_runs),
        interaction_conditions_unchanged=all(r['conditions_by_node']==source_runs[r['base_case_id']]['conditions_by_node'] for r in new_runs),
        interaction_split_unchanged=all(r['split']==source_runs[r['base_case_id']]['split'] for r in new_runs),
        interaction_shared_across_noise=all(r['node_values_clean']==lookup[r['network_run_id']]['node_values_clean']
            and r['conditions_by_node']==lookup[r['network_run_id']]['conditions_by_node'] for r in new_obs),
        interaction_observation_noise_unchanged=all(r['observation_noise']==source_obs[r['base_case_id'],r['noise_sigma']]['observation_noise'] for r in new_obs),
        interaction_parent_continuity=all(d['parent_clean']=={p:r['node_values_clean'][p] for p in d['parent_clean']}
            for r in new_runs for d in r['node_details'].values()),
        interaction_formula_before_clip=all(math.isclose(d['interaction_deduction'],100*r['interaction_gamma']*r['conditions_by_node'][n]['E']*(1-r['conditions_by_node'][n]['C']),abs_tol=1e-12)
            and d['process_noise']==0 and math.isclose(d['receiver_se_before_clip'],d['receiver_se_formula']-d['interaction_deduction'],abs_tol=1e-12)
            and d['receiver_se_clean']==min(100,max(0,d['receiver_se_before_clip']))
            for r in new_runs for n,d in r['node_details'].items() if d['kind']=='receiver'),
        interaction_values_in_range=all(math.isfinite(v) and 0<=v<=100 for r in new_obs
            for key in ('node_values_clean','node_values_observed') for v in r[key].values()),
        interaction_zero_observation_noise_equals_clean=all(r['node_values_clean']==r['node_values_observed'] for r in new_obs if r['noise_sigma']==0))
    report['clean_runs'].extend(new_runs);report['observations'].extend(new_obs)
    checks['interaction_ids_unique']=len({r['observation_id'] for r in report['observations']})==len(report['observations'])
    report.update(stage='stage2_interaction_generation',source_varying_generation_sha256=source_sha,
        interaction_config_snapshot=deepcopy(config),interaction_samples=samples,interaction_structural_by_split=structural,
        input_sha256=digest(dict(network=network,node_configs=nodes,experiment=config)))
    for group,gamma in groups.items():report['config_snapshot']['groups'][group]=dict(source_group=source_group,interaction_gamma=gamma,process_std=0)
    report['counts'].update(clean_network_runs=len(report['clean_runs']),network_observation_versions=len(report['observations']),
        node_observation_rows=len(report['observations'])*len(generator.nodes),interaction_receiver_rows=len(samples))
    report['policies'].update(interaction_process_noise=0,interaction_conditions_are_fdna_inputs=False,
        interaction_strengths_prespecified=True)
    report['limits']=['交互公式及强弱取值为模拟假设，不是原论文给定参数；不按拟合误差选择γ。',
        '强弱样本生成与FDNA拟合完成程度分别记录，不能由已生成数据推断已完成拟合。',
        '只比较无过程波动下的交互影响，不与过程组混合解释；结构下界按各组实际数据重算。']
    report['status']='complete' if all(checks.values()) else 'blocked'
    return report
