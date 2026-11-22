"""Small synthetic bridge acceptance run; no operational equipment optimization."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from .equipment_bridge import bridge
from .generation_network import NetworkGenerator
from .network_workflow import fit_network, analyze_dependencies, export_outputs
from .workflow_common import number, write_bundle, dumps, csv_text
from .workflow_demo import input_csvs
from .bridge_shared import validate_shared_roots, shared_response, shared_figure


def generate(network, nodes, experiment):
    allowed={'seed','base_cases','train_count','root_range','noise_std','process_std','interaction_gamma','repetitions','basis','root_coupling'}
    if set(experiment)-allowed: raise ValueError('采样配置含未知字段')
    for key in ('base_cases','train_count','seed'):
        if isinstance(experiment[key],bool) or not isinstance(experiment[key],int) or experiment[key]<0:
            raise ValueError(key+'须为非负整数')
    bounds=experiment['root_range']
    if not isinstance(bounds,list) or len(bounds)!=2: raise ValueError('根节点范围须为两个SE值')
    for v in bounds: number(v,'根节点范围',0,100)
    if bounds[0]>=bounds[1]: raise ValueError('根节点范围须严格递增')
    sigmas=experiment['noise_std']
    if not isinstance(sigmas,list) or not sigmas: raise ValueError('噪声档位须为非空数组')
    for v in sigmas: number(v,'观测噪声标准差',0)
    if len(sigmas)!=len(set(sigmas)): raise ValueError('噪声档位不得重复')
    for key in ('process_std','interaction_gamma'):number(experiment[key],key,0)
    if experiment['process_std'] != 0 or experiment['interaction_gamma'] != 0 or isinstance(experiment['repetitions'],bool) or experiment['repetitions'] != 1:
        raise ValueError('首版采样器仅支持无过程波动、无交互、单次重复；不得静默忽略配置')
    generator = NetworkGenerator(dict(format_version='1.0', basis='已审查的合成活动流假设', **network), nodes)
    coupling=experiment.get('root_coupling',[]); coupled=set(); group_ids=set()
    if not isinstance(coupling,list):raise ValueError('根节点共同状态须为数组')
    for group in coupling:
        if not isinstance(group['id'],str) or not group['id'].strip() or group['id'] in group_ids:
            raise ValueError('共同状态编号须非空且不重复')
        group_ids.add(group['id']);members=group['nodes']
        if not isinstance(members,list) or not members or len(set(members))!=len(members) or not set(members)<=set(generator.roots) or set(members)&coupled:
            raise ValueError('共同状态须引用互不重叠的有效根节点')
        if set(group['offsets'])!=set(members):raise ValueError('任务偏移须覆盖共同状态的全部根节点')
        for offset in group['offsets'].values():
            number(offset,'任务产品偏移')
            if bounds[0]+offset<0 or bounds[1]+offset>100:raise ValueError('共同状态加偏移后超出SE范围')
        coupled.update(members)
    rng = np.random.default_rng(experiment['seed'])
    # Single-root compatible sampler. No invented second physical variable.
    count = experiment['base_cases']; train = experiment['train_count']
    if not 0 < train < count: raise ValueError('训练与留出必须均非空')
    train_ids = set(rng.permutation(count)[:train].tolist())
    rows = []; clean_states = []
    for i in range(count):
        root = {n: float(rng.uniform(*bounds)) for n in generator.roots if n not in coupled}
        latent={g['id']:float(rng.uniform(*bounds)) for g in coupling}
        for g in coupling:
            root.update({n:latent[g['id']]+g['offsets'][n] for n in g['nodes']})
        clean = generator.evaluate(root)['node_values_clean']
        clean_states.append(dict(base_case_id=f'b{i:04d}', values=clean,**({'root_latents':latent} if coupling else {})))
        for sigma in experiment['noise_std']:
            state = generator.evaluate(root, observation_noise={n:float(rng.normal(0,sigma)) for n in generator.nodes})
            rows.append(dict(sample_id=f'b{i:04d}_s{sigma}', base_case_id=f'b{i:04d}', scenario_id='fixed_conditions',
                fit_group=f'sigma{sigma}', split='train' if i in train_ids else 'holdout',
                node_values=state['node_values_observed'], predictor_values=state['node_values_clean']))
    return rows, clean_states


def select_response(network, report, analysis):
    roots=report['topology']['roots']
    active={e['intervened_node'] for e in analysis['effects']}
    root=next((n for n in roots if n in active),roots[0])
    possible=[e for e in analysis['effects'] if e['intervened_node']==root]
    terminals=set(network['nodes'])-{e['feeder'] for e in network['edges']}
    available={e['target'] for e in possible}
    target=next(iter(sorted(available & terminals)),next(iter(sorted(available)),root))
    group='sigma2' if any(e['group']=='sigma2' for e in possible) else (possible[0]['group'] if possible else '')
    selected=[e for e in possible if e['target']==target and e['group']==group]
    if selected:
        baseline=min(e['baseline_sample'] for e in selected)
        selected=[e for e in selected if e['baseline_sample']==baseline]
    return root,target,group,selected


def figures(out, network, sidecar, report, analysis):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'], 'axes.unicode_minus':False})
    def save(fig, name):
        fig.savefig(out/(name+'.png'), dpi=170, bbox_inches='tight')
        fig.savefig(out/(name+'.svg'), bbox_inches='tight'); plt.close(fig)
    chains=sorted(sidecar['scope_chain_ids']);activities=sidecar['capability_instances']
    columns={'insight':0,'coordination':1,'manifestation':2}
    positions={n:(columns[item['role']],-1.15*chains.index(item['chain_id'])) for n,item in activities.items()}
    bottom=-1.15*(len(chains)-1)
    fig, ax = plt.subplots(figsize=(11,3+1.6*len(chains))); ax.set(xlim=(-.65,2.65),ylim=(bottom-.85,.9));ax.axis('off')
    for n,(x,y) in positions.items():
        item=sidecar['capability_instances'][n]
        ax.text(x,y,n+'\n'+item['label']+'\n'+item['subject'],ha='center',va='center',bbox=dict(boxstyle='round,pad=.5',fc='#edf3fa',ec='#335c81'))
    for e in network['edges']:
        x,y=positions[e['feeder']];xx,yy=positions[e['receiver']]
        ax.annotate('',xy=(xx-.31,yy),xytext=(x+.31,y),arrowprops=dict(arrowstyle='->',color='#21835d',lw=2))
    ax.text(1,.65,'已选合成装备 → 活动产品消费 → 能力依赖DAG',ha='center',fontsize=14)
    pending=sum(not c['exported'] for c in sidecar['candidates'])
    ax.text(1,bottom-.6,f"{len(chains)}条已选链，{len(network['nodes'])}个能力实例，{len(network['edges'])}条批准边，{pending}条候选未导出\n同名主体代表共享装备；跨任务共同状态单独登记，不增加FDNA边",ha='center',fontsize=10)
    save(fig,'01_装备到能力映射')
    metric=[dict(group=j['fit_group'],model=j['model'],**m) for j in report['jobs'] for m in j['metrics'] if m['split']=='holdout']
    fig,axes=plt.subplots(1,2,figsize=(13,4.8),sharey=True)
    for ax,kind in zip(axes,['local','recursive']):
        selected=[m for m in metric if m['prediction']==kind]
        targets=sorted({m['node'] for m in selected});jobs=sorted({(m['group'],m['model']) for m in selected})
        width=.8/max(1,len(jobs));labels={'base':'基础FDNA','free_delta':'自由δ'}
        for i,(group,mode) in enumerate(jobs):
            own={m['node']:m['rmse'] for m in selected if m['group']==group and m['model']==mode}
            x=[k-.4+width*(i+.5) for k in range(len(targets))]
            ax.bar(x,[own.get(n) if own.get(n) is not None else float('nan') for n in targets],width,
                label=group.replace('sigma','σ=')+' / '+labels.get(mode,mode))
        ax.set_xticks(range(len(targets)),targets);ax.legend(fontsize=8);ax.grid(axis='y',alpha=.15)
        ax.set_title('留出局部误差' if kind=='local' else '留出整网递推误差'); ax.set_ylabel('RMSE（SE点）')
    fig.suptitle('独立机制合成数据；模型预测不裁剪');fig.tight_layout();save(fig,'02_留出预测误差')
    root,target,group,selected=select_response(network,report,analysis)
    fig,ax=plt.subplots(figsize=(8,4))
    for mode in report['config_snapshot']['models']:
        own=sorted([e for e in selected if e['model']==mode],key=lambda e:e['retained_fraction'])
        ax.plot([e['retained_fraction']*100 for e in own],[e['intervened'] for e in own],'-o',label=mode)
    ax.set(xlabel=root+'保留效能（%）',ylabel=target+'预测SE',title=f'首条留出基准的模型内退化响应（{group}）');ax.legend();ax.grid(alpha=.2)
    fig.tight_layout();save(fig,'03_模型内传播响应')


def run(config_dir, output_dir):
    directory=Path(config_dir); out=Path(output_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('输出目录须不存在或为空；在拟合前拒绝覆盖')
    read=lambda name: json.loads((directory/name).read_text(encoding='utf-8'))
    source=read('source_report.json'); mapping=read('mapping.json')
    network, sidecar=bridge(source,mapping)
    nodes=read('generation_nodes.json'); experiment=read('experiment.json'); fitconfig=read('fit.json')
    validate_shared_roots(network,sidecar,experiment)
    rows, states=generate(network,nodes,experiment)
    report=fit_network(network,rows,fitconfig,lambda p:print(dumps(p),flush=True))
    analysis=analyze_dependencies(report,rows,fitconfig['analysis'])
    common=shared_response(network,nodes,experiment,states,rows,report,fitconfig['analysis']['retained_fractions'])
    counts=dict(status=report['status'],nodes=len(network['nodes']),edges=len(network['edges']),
        base_cases=len(states),observation_versions=len(rows),local_fits=sum(len(j['local_fits']) for j in report['jobs']),
        fitted=sum(l['status']=='fitted' for j in report['jobs'] for l in j['local_fits']),
        flagged_local_fits=sum(bool(l['flags']) for j in report['jobs'] for l in j['local_fits']),
        effect_rows=len(analysis['effects']),shared_response_rows=len(common),
        selected_chains=len(sidecar['scope_chain_ids']),excluded_chains=len(sidecar['excluded_selected_chain_ids']),
        shared_subject_roles=len(sidecar['state_sharing']),
        source_file_sha256=hashlib.sha256((directory/'source_report.json').read_bytes()).hexdigest())
    outputs={**export_outputs(report,analysis),**input_csvs(network,rows), '输入_拓扑.json':dumps(network),
        '输入_SE记录.json':dumps(rows),'衔接依据与审查.json':dumps(sidecar),'候选边审查.csv':csv_text(sidecar['candidates']),
        '干净状态.json':dumps(states),'运行计数.json':dumps(counts)}
    if common:outputs['共同来源情景响应.csv']=csv_text(common)
    for name in ['source_report.json','mapping.json','generation_nodes.json','experiment.json','fit.json']:
        outputs['冻结输入_'+name]=(directory/name).read_text(encoding='utf-8')
    write_bundle(out, outputs);figures(out,network,sidecar,report,analysis);shared_figure(out,common)
    inventory={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}
    (out/'全部产物校验.json').write_text(dumps(inventory),encoding='utf-8')
    return counts


def main():
    p=argparse.ArgumentParser();p.add_argument('--config-dir',required=True);p.add_argument('--output-dir',required=True)
    a=p.parse_args();counts=run(a.config_dir,a.output_dir);print(dumps(counts));return 0 if counts['status']=='complete' else 1

if __name__=='__main__':raise SystemExit(main())
