"""Stage two joint DAG samples; fitting and predicted-state recursion come later."""
import argparse
from collections import Counter
from copy import deepcopy
import csv
import io
import json
import math
from pathlib import Path
import sys

from source_data.common import read_json, write_outputs
from .generation import validate_config
from .generation_network import NetworkGenerator
from .model import ModelError
from .structure import digest


def _check_experiment(config,root_count):
    validate_config('network_experiment',config)
    if not 0<config['train_count']<config['base_cases'] or 0 not in config['noise_std']:
        raise ModelError('须保留训练、验证样本及σ=0对照')
    sampling=config['root_sampling']; fractions=sampling['fractions']
    if not math.isclose(math.fsum(fractions.values()),1,rel_tol=0,abs_tol=1e-12):
        raise ModelError('根节点类别比例之和须为1')
    if any(not math.isclose(config['base_cases']*f,round(config['base_cases']*f),abs_tol=1e-9,rel_tol=0)
           for f in fractions.values()):
        raise ModelError('基础样本数须使每类样本数量为整数，不自动修改比例')
    if root_count<2:
        raise ModelError('本联合抽样入口须至少两个根节点，以区分单根与双根退化')
    for key in ('normal','degraded'):
        if sampling[key][0]>=sampling[key][1]:
            raise ModelError('根节点抽样区间下界须严格小于上界')
    if sampling['degraded'][1]>sampling['normal'][0]:
        raise ModelError('初抽样退化区间应不高于正常区间；共享因子作用后的区间可重叠')


def run_network_check(network,nodes,config):
    baseline=NetworkGenerator(network,nodes)
    _check_experiment(config,len(baseline.roots))
    import numpy as np
    generators={g:NetworkGenerator(network,nodes,condition_values=v) for g,v in sorted(config['groups'].items())}
    roots=baseline.roots; all_nodes=baseline.nodes; receivers=baseline.receivers
    root_inputs=[]; clean_runs=[]; observations=[]
    for rep,rep_seed in enumerate(np.random.SeedSequence(config['seed']).spawn(config['repetitions']),1):
        streams=rep_seed.spawn(5)
        root_rng=np.random.default_rng(streams[0]); process_rng=np.random.default_rng(streams[2])
        obs_rng=np.random.default_rng(streams[3]); split_rng=np.random.default_rng(streams[4])
        n=config['base_cases']; sampling=config['root_sampling']; z_weight=sampling['shared_factor_weight']
        training=set(split_rng.permutation(n)[:config['train_count']].tolist())
        process={p:process_rng.standard_normal(n) for p in receivers}
        obs={p:obs_rng.standard_normal(n) for p in all_nodes}
        base=[]
        for category in ('normal','single','double','all'):
            for _ in range(round(n*sampling['fractions'][category])):
                k={'normal':0,'single':1,'double':2,'all':len(roots)}[category]
                low=set(root_rng.choice(len(roots),size=k,replace=False).tolist())
                common=float(root_rng.uniform(0,1))
                values={p:100*((1-z_weight)*float(root_rng.uniform(*sampling['degraded' if j in low else 'normal']))+
                              z_weight*common) for j,p in enumerate(roots)}
                i=len(base); base_id=f'rep{rep}:base{i}'
                record=dict(base_case_id=base_id,repetition=rep,seed=config['seed'],
                    repetition_spawn_key=list(rep_seed.spawn_key),root_group=category,
                    degraded_root_ids=[p for j,p in enumerate(roots) if j in low],root_values_clean=values,
                    shared_factor=common,split='train' if i in training else 'holdout')
                base.append(record); root_inputs.append(record)
        for group,generator in generators.items():
            for i,base_row in enumerate(base):
                evaluation=generator.evaluate(base_row['root_values_clean'],
                    process_noise={p:float(config['process_std']*process[p][i]) for p in receivers},
                    interaction_gamma=config['interaction_gamma'])
                run_id=group+':'+base_row['base_case_id']
                trace={p:{k:v for k,v in detail.items() if k not in
                          ('observation_noise','receiver_se_observed','observation_clipped')}
                       for p,detail in evaluation['node_details'].items()}
                clean_runs.append(dict(network_run_id=run_id,base_case_id=base_row['base_case_id'],
                    repetition=rep,group=group,split=base_row['split'],
                    node_values_clean=evaluation['node_values_clean'].copy(),node_details=trace))
                for sigma in config['noise_std']:
                    noise={p:float(sigma*obs[p][i]) for p in all_nodes}
                    observed={p:min(100,max(0,evaluation['node_values_clean'][p]+noise[p])) for p in all_nodes}
                    observations.append(dict(observation_id=run_id+':sigma:'+str(sigma),network_run_id=run_id,
                        base_case_id=base_row['base_case_id'],repetition=rep,group=group,split=base_row['split'],
                        noise_sigma=sigma,node_values_clean=evaluation['node_values_clean'].copy(),
                        node_values_observed=observed,observation_noise=noise))
    by_base={r['base_case_id']:r for r in root_inputs}; by_run={r['network_run_id']:r for r in clean_runs}
    checks=dict(
        root_inputs_shared=all(all(r['node_values_clean'][p]==by_base[r['base_case_id']]['root_values_clean'][p]
                                   for p in roots) for r in clean_runs),
        clean_parent_continuity=all(all(detail['parent_clean'][p]==r['node_values_clean'][p]
                                          for p in detail['parent_clean'])
                                   for r in clean_runs for detail in r['node_details'].values()),
        clean_states_shared_across_noise=all(r['node_values_clean']==by_run[r['network_run_id']]['node_values_clean']
                                            for r in observations),
        zero_noise_equals_clean=all(r['node_values_observed']==r['node_values_clean'] for r in observations if r['noise_sigma']==0),
        grouped_split_consistent=all(r['split']==by_base[r['base_case_id']]['split'] for r in observations),
        values_in_range=all(math.isfinite(v) and 0<=v<=100 for r in observations
                            for key in ('node_values_clean','node_values_observed') for v in r[key].values()),
        unique_ids=len(by_base)==len(root_inputs) and len(by_run)==len(clean_runs) and
                   len({r['observation_id'] for r in observations})==len(observations))
    summaries=[]
    for group in sorted(generators):
        rows=[r for r in clean_runs if r['group']==group]
        for p in receivers:
            values=[r['node_values_clean'][p] for r in rows]
            deficit=[max(0,min(r['node_details'][p]['parent_clean'].values())-r['node_values_clean'][p]) for r in rows]
            summaries.append(dict(group=group,node=p,clean_min=min(values),clean_max=max(values),
                clean_mean=math.fsum(values)/len(values),below_min_parent_fraction=sum(v>1e-10 for v in deficit)/len(deficit),
                base_fdna_rmse_lower_bound=math.sqrt(math.fsum(v*v for v in deficit)/len(deficit))))
    snapshot=dict(network=network,node_configs=nodes,experiment=config)
    return dict(format_version='1.0',status='complete' if all(checks.values()) else 'blocked',
        stage='second_stage_step_2_joint_generation',input_sha256=digest(snapshot),
        network_snapshot=deepcopy(network),node_configs_snapshot=deepcopy(nodes),config_snapshot=deepcopy(config),
        topology=dict(order=baseline.order,roots=roots,receivers=receivers),
        checks=checks,summaries=summaries,
        root_category_counts=dict(Counter(r['root_group'] for r in root_inputs)),
        counts=dict(nodes=len(all_nodes),edges=len(network['edges']),repetitions=config['repetitions'],
                    paired_base_inputs=len(root_inputs),clean_network_runs=len(clean_runs),
                    network_observation_versions=len(observations),node_observation_rows=len(observations)*len(all_nodes)),
        policies=dict(fdna_generated=False,fdna_fitted=False,clean_states_propagated=True,
            observation_noise_propagated=False,split_unit='complete_base_network',
            noise_pairing='same_per_node_standard_normal_scaled_by_sigma',
            condition_groups_share_roots_splits_and_noise=True,
            random_streams=['root','reserved_conditions','process','observation','split']),
        root_inputs=root_inputs,clean_runs=clean_runs,observations=observations,
        limits=['结构与机制均为明确的模拟假设，不是自动发现的实际依赖',
                '本次只生成联合网络样本；尚未拟合FDNA或开展拟合参数的整网预测',
                '条件组和噪声版本为配对数据，不按独立实验次数重复计数'])


def node_rows(report):
    runs={r['network_run_id']:r for r in report['clean_runs']}
    bases={r['base_case_id']:r for r in report['root_inputs']}
    nodes=sorted(report['network_snapshot']['nodes'])
    for r in report['observations']:
        base=bases[r['base_case_id']]; run=runs[r['network_run_id']]
        for p in nodes:
            detail=run['node_details'][p]; clean=r['node_values_clean'][p]
            observed=r['node_values_observed'][p]; noise=r['observation_noise'][p]
            yield dict(observation_id=r['observation_id'],network_run_id=r['network_run_id'],
                base_case_id=r['base_case_id'],repetition=r['repetition'],seed=base['seed'],
                group=r['group'],split=r['split'],noise_sigma=r['noise_sigma'],
                root_group=base['root_group'],degraded_root_ids=json.dumps(base['degraded_root_ids']),
                node_id=p,node_kind=detail['kind'],parent_clean_json=json.dumps(detail['parent_clean'],sort_keys=True),
                receiver_se_formula=detail.get('receiver_se_formula'),condition_factor=detail.get('condition_factor'),
                process_noise=detail['process_noise'],process_clipped=detail['process_clipped'],
                se_clean=clean,se_observed=observed,observation_noise=noise,
                observation_clipped=not 0<=clean+noise<=100)


def network_csv(report):
    stream=io.StringIO(newline=''); iterator=iter(node_rows(report)); first=next(iterator)
    writer=csv.DictWriter(stream,fieldnames=list(first)); writer.writeheader(); writer.writerow(first)
    writer.writerows(iterator)
    return '\ufeff'+stream.getvalue()


def render_network_summary(report):
    c=report['counts']; config=report['config_snapshot']
    ids=sorted(report['network_snapshot']['nodes']); aliases={n:'N'+str(i) for i,n in enumerate(ids)}
    diagram=[aliases[n]+'['+json.dumps(n,ensure_ascii=False)+']' for n in ids]
    diagram.extend(aliases[e['feeder']]+' --> '+aliases[e['receiver']] for e in report['network_snapshot']['edges'])
    lines=['# 第二阶段第二步 综合网络样本生成','',
        f"将独立生成机制连接为{c['nodes']}节点、{c['edges']}条依赖边的综合场景。观测噪声只形成观测记录，不用于生成下游干净状态。",'',
        '## 网络结构','', '```mermaid','flowchart LR',*diagram,'```','',
        'A、B为两路探知信息；C_aux为融合的补充信息；D_aux为执行支撑；T、Q、R依次采用互补、几何、加权机制。网络和节点机制使用独立配置文件，边不保存FDNA参数。','',
        '## 本次规模','',f"重复{c['repetitions']}次，每次{config['base_cases']}组配对根节点输入。",'',
        '| 口径 | 数量 |','| --- | ---: |',
        f"| 配对基础输入 | {c['paired_base_inputs']} |",f"| 两组条件下的干净网络过程 | {c['clean_network_runs']} |",
        f"| 各档噪声的整网观测版本 | {c['network_observation_versions']} |",f"| 七节点长表观测记录 | {c['node_observation_rows']} |",'',
        '条件组和噪声档位共享根节点、划分及相应随机抽样，不能把上述数目都称为独立推演次数。',
        f"每次重复{config['train_count']}个基础网络用于训练划分，{config['base_cases']-config['train_count']}个用于验证划分；目前只冻结划分，未实际拟合。",'',
        '根节点类别：'+json.dumps(report['root_category_counts'],ensure_ascii=False),
        '节点条件组：'+json.dumps(config['groups'],ensure_ascii=False),
        f"观测噪声σ={config['noise_std']}；过程波动标准差={config['process_std']}，交互强度={config['interaction_gamma']}。",'',
        '## 联合数据检查','']
    lines.extend('- '+k+'：'+('通过' if v else '未通过') for k,v in report['checks'].items())
    lines+=['','## 接受节点的实际输出分布','',
        '| 条件组 | 节点 | 最小SE | 最大SE | 均值 | 低于最小上游比例 | 基础FDNA理论RMSE下界 |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for r in report['summaries']:
        lines.append(f"| {r['group']} | {r['node']} | {r['clean_min']:.4f} | {r['clean_max']:.4f} | {r['clean_mean']:.4f} | {r['below_min_parent_fraction']:.1%} | {r['base_fdna_rmse_lower_bound']:.4f} |")
    lines+=['','本表按每个条件组的全部干净网络统计，下界仅适用于不含δ的基础FDNA；不是已测得的拟合误差。后续与训练或验证RMSE比较时，必须在同一划分、同一输入和目标上重新计算下界。',
        'node_samples.csv按观测版本保存全部节点，含真实上游干净值、干净/观测SE、噪声与截断标记；network_report.json另存每个干净网络的逐节点生成过程。',
        '如使用--plot，network_example.png展示固定条件组第一条真实生成记录：框内为节点及干净SE，箭头传递该状态；这不是FDNA拟合后的网络预测。',
        '', '## 下一步','',
        '按条件组、噪声档位分别从综合网络训练记录拟合各接受节点全部入边参数，再从验证根节点递推。局部拟合可读实际中间节点，整网预测必须使用FDNA预测的中间节点；不直接搬用独立单元实验参数。',
        '', '## 重跑命令','', '```powershell',"Set-Location '"+str(Path(__file__).resolve().parents[1])+"'",
        "& '"+sys.executable+"' -B -m fdna.generation_network_check --network data/fdna/generation_network.json --nodes data/fdna/generation_network_nodes.json --config data/fdna/generation_network_check.json --output-dir generated/stage2-network-new --plot",'```','']
    return '\n'.join(lines)


def plot_network_example(report):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.patches import FancyArrowPatch, Patch
    run=next((r for r in report['clean_runs'] if r['group']=='fixed_conditions'),report['clean_runs'][0])
    roots=set(report['topology']['roots']); incoming={n:[] for n in report['network_snapshot']['nodes']}
    for e in report['network_snapshot']['edges']: incoming[e['receiver']].append(e['feeder'])
    depths={}
    for n in report['topology']['order']:
        depths[n]=0 if n in roots else 1+max(depths[p] for p in incoming[n])
    positions={}
    for depth in sorted(set(depths.values())):
        layer=sorted(n for n,d in depths.items() if d==depth)
        positions.update({n:(depth*2.7,(len(layer)-1)/2-i) for i,n in enumerate(layer)})
    fig=Figure(figsize=(11.5,5),layout='constrained'); FigureCanvasAgg(fig); axis=fig.subplots()
    for edge in report['network_snapshot']['edges']:
        a,b=edge['feeder'],edge['receiver']; span=depths[b]-depths[a]
        axis.add_patch(FancyArrowPatch(positions[a],positions[b],arrowstyle='-|>',mutation_scale=15,
            shrinkA=28,shrinkB=28,linewidth=1.4,color='#61758B',
            connectionstyle='arc3,rad='+str(.15 if span>1 else 0)))
    for n,(x,y) in positions.items():
        axis.text(x,y,f"{n}\nSE={run['node_values_clean'][n]:.2f}",ha='center',va='center',fontsize=10,
                  bbox=dict(boxstyle='round,pad=.5',facecolor='#DCEBF7' if n in roots else '#FFF1DB',edgecolor='#61758B'))
    axis.set_xlim(-.8,max(x for x,y in positions.values())+.8)
    axis.set_ylim(min(y for x,y in positions.values())-.8,max(y for x,y in positions.values())+.8)
    axis.axis('off')
    axis.set_title('Joint independent-mechanism network | '+run['group']+' | '+run['base_case_id'],pad=18)
    fig.legend(handles=[Patch(facecolor='#DCEBF7',label='Sampled root'),
                        Patch(facecolor='#FFF1DB',label='Generated receiver')],
               loc='outside lower center',ncol=2,frameon=False)
    stream=io.BytesIO(); fig.savefig(stream,format='png',dpi=250)
    return stream.getvalue()


def main(argv=None):
    parser=argparse.ArgumentParser(description='独立机制的整网联合样本生成')
    for flag in ('network','nodes','config','output-dir'):
        parser.add_argument('--'+flag,type=Path,required=True)
    parser.add_argument('--plot',action='store_true')
    args=parser.parse_args(argv)
    try:
        report=run_network_check(read_json(args.network),read_json(args.nodes),read_json(args.config))
        outputs={
            'network_report.json':json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',
            'node_samples.csv':network_csv(report),
            '第二阶段第二步_综合网络生成.md':render_network_summary(report),
        }
        if args.plot: outputs['network_example.png']=plot_network_example(report)
        write_outputs(args.output_dir,outputs,protected=[args.network,args.nodes,args.config])
        print(json.dumps(dict(status=report['status'],counts=report['counts'],checks=report['checks']),ensure_ascii=False,indent=2))
        return 0 if report['status']=='complete' else 1
    except (ValueError,OSError,ImportError) as exc:
        print(json.dumps(dict(status='invalid',error=str(exc)),ensure_ascii=False))
        return 2


if __name__=='__main__':
    sys.exit(main())
