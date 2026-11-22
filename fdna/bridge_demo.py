"""Small synthetic bridge acceptance run; no operational equipment optimization."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import numpy as np
from .equipment_bridge import bridge
from .generation_network import NetworkGenerator
from .network_workflow import fit_network, analyze_dependencies, export_outputs
from .workflow_common import settings, write_bundle, dumps, csv_text
from .workflow_demo import input_csvs


def generate(network, nodes, experiment):
    if experiment['process_std'] != 0 or experiment['interaction_gamma'] != 0 or experiment['repetitions'] != 1:
        raise ValueError('首版采样器仅支持无过程波动、无交互、单次重复；不得静默忽略配置')
    generator = NetworkGenerator(dict(format_version='1.0', basis='已审查的合成活动流假设', **network), nodes)
    rng = np.random.default_rng(experiment['seed'])
    # Single-root compatible sampler. No invented second physical variable.
    count = experiment['base_cases']; train = experiment['train_count']
    if not 0 < train < count: raise ValueError('训练与留出必须均非空')
    train_ids = set(rng.permutation(count)[:train].tolist())
    rows = []; clean_states = []
    for i in range(count):
        root = {n: float(rng.uniform(*experiment['root_range'])) for n in generator.roots}
        clean = generator.evaluate(root)['node_values_clean']
        clean_states.append(dict(base_case_id=f'b{i:04d}', values=clean))
        for sigma in experiment['noise_std']:
            state = generator.evaluate(root, observation_noise={n:float(rng.normal(0,sigma)) for n in generator.nodes})
            rows.append(dict(sample_id=f'b{i:04d}_s{sigma}', base_case_id=f'b{i:04d}', scenario_id='fixed_conditions',
                fit_group=f'sigma{sigma}', split='train' if i in train_ids else 'holdout',
                node_values=state['node_values_observed'], predictor_values=state['node_values_clean']))
    return rows, clean_states


def figures(out, network, sidecar, report, analysis):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'], 'axes.unicode_minus':False})
    def save(fig, name):
        fig.savefig(out/(name+'.png'), dpi=170, bbox_inches='tight')
        fig.savefig(out/(name+'.svg'), bbox_inches='tight'); plt.close(fig)
    fig, ax = plt.subplots(figsize=(11,4)); ax.set(xlim=(-.5,3),ylim=(-.9,1.25));ax.axis('off')
    positions={n:(i,0) for i,n in enumerate(network['nodes'])}
    for n,(x,y) in positions.items():
        item=sidecar['capability_instances'][n]
        ax.text(x,y,n+'\n'+item['label']+'\n'+item['subject'],ha='center',va='center',bbox=dict(boxstyle='round,pad=.5',fc='#edf3fa',ec='#335c81'))
    for e in network['edges']:
        x,y=positions[e['feeder']];xx,yy=positions[e['receiver']]
        ax.annotate('',xy=(xx-.31,yy),xytext=(x+.31,y),arrowprops=dict(arrowstyle='->',color='#21835d',lw=2))
    ax.text(1,1,'已选合成装备链 → 活动产品消费 → 能力依赖DAG',ha='center',fontsize=14)
    ax.text(1,-.65,'实线：工作假设已批准；A→C直接边待审，不导出\n通信心跳不被下游活动消费，也不单独导出为依赖边',ha='center',fontsize=11)
    save(fig,'01_装备到能力映射')
    metric=[dict(group=j['fit_group'],model=j['model'],**m) for j in report['jobs'] for m in j['metrics'] if m['split']=='holdout']
    fig,axes=plt.subplots(1,2,figsize=(11,4),sharey=True)
    for ax,kind in zip(axes,['local','recursive']):
        selected=[m for m in metric if m['prediction']==kind]
        ax.bar(range(len(selected)),[m['rmse'] for m in selected],color=['#335c81' if m['model']=='base' else '#d18c42' for m in selected])
        ax.set_xticks(range(len(selected)),[m['group']+'\n'+m['model']+'\n'+m['node'] for m in selected],rotation=45,ha='right',fontsize=8)
        ax.set_title('留出局部误差' if kind=='local' else '留出整网递推误差'); ax.set_ylabel('RMSE（SE点）')
    fig.suptitle('独立机制合成数据；模型预测不裁剪');fig.tight_layout();save(fig,'02_留出预测误差')
    selected=[e for e in analysis['effects'] if e['intervened_node']=='A' and e['target']=='C' and e['group']=='sigma2']
    fig,ax=plt.subplots(figsize=(8,4))
    for mode in ['base','free_delta']:
        own=sorted([e for e in selected if e['model']==mode],key=lambda e:e['retained_fraction'])
        ax.plot([e['retained_fraction']*100 for e in own],[e['intervened'] for e in own],'-o',label=mode)
    ax.set(xlabel='A保留效能（%）',ylabel='C预测SE',title='首条留出基准的模型内退化响应（σ=2）');ax.legend();ax.grid(alpha=.2)
    fig.tight_layout();save(fig,'03_模型内传播响应')


def run(config_dir, output_dir):
    directory=Path(config_dir); out=Path(output_dir)
    read=lambda name: json.loads((directory/name).read_text(encoding='utf-8'))
    source=read('source_report.json'); mapping=read('mapping.json')
    network, sidecar=bridge(source,mapping)
    nodes=read('generation_nodes.json'); experiment=read('experiment.json'); fitconfig=read('fit.json')
    rows, states=generate(network,nodes,experiment)
    report=fit_network(network,rows,fitconfig,lambda p:print(dumps(p),flush=True))
    analysis=analyze_dependencies(report,rows,fitconfig['analysis'])
    counts=dict(status=report['status'],nodes=len(network['nodes']),edges=len(network['edges']),
        base_cases=len(states),observation_versions=len(rows),local_fits=sum(len(j['local_fits']) for j in report['jobs']),
        fitted=sum(l['status']=='fitted' for j in report['jobs'] for l in j['local_fits']),
        flagged_local_fits=sum(bool(l['flags']) for j in report['jobs'] for l in j['local_fits']),
        effect_rows=len(analysis['effects']),source_file_sha256=hashlib.sha256((directory/'source_report.json').read_bytes()).hexdigest())
    outputs={**export_outputs(report,analysis),**input_csvs(network,rows), '输入_拓扑.json':dumps(network),
        '输入_SE记录.json':dumps(rows),'衔接依据与审查.json':dumps(sidecar),'候选边审查.csv':csv_text(sidecar['candidates']),
        '干净状态.json':dumps(states),'运行计数.json':dumps(counts)}
    for name in ['source_report.json','mapping.json','generation_nodes.json','experiment.json','fit.json']:
        outputs['冻结输入_'+name]=(directory/name).read_text(encoding='utf-8')
    write_bundle(out, outputs);figures(out,network,sidecar,report,analysis)
    inventory={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}
    (out/'全部产物校验.json').write_text(dumps(inventory),encoding='utf-8')
    return counts


def main():
    p=argparse.ArgumentParser();p.add_argument('--config-dir',required=True);p.add_argument('--output-dir',required=True)
    a=p.parse_args();counts=run(a.config_dir,a.output_dir);print(dumps(counts));return 0 if counts['status']=='complete' else 1

if __name__=='__main__':raise SystemExit(main())
