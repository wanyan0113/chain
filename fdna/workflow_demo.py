"""Run the independent 11/18 demo through the same user-facing network entry point."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from .generation_network_check import run_network_check
from .generation_network import NetworkGenerator
from .network_workflow import fit_network,analyze_dependencies,export_outputs
from .structure import digest
from .workflow_common import write_bundle,dumps,csv_text


def generate(network,nodes,config):
    gen_network=dict(format_version='1.0',basis='论文图6拓扑的独立机制模拟；未使用表7参数',**network)
    generation=run_network_check(gen_network,nodes,config)
    rows=[]
    for r in generation['observations']:
        group=f"rep{r['repetition']}_sigma{r['noise_sigma']}"
        rows.append(dict(sample_id=r['observation_id'],base_case_id=r['base_case_id'],scenario_id=r['group'],fit_group=group,
            split=r['split'],node_values=deepcopy(r['node_values_observed']),predictor_values=deepcopy(r['node_values_clean'])))
    return generation,rows


def input_csvs(network,rows):
    wide=[];long=[]
    meta=('sample_id','base_case_id','scenario_id','fit_group','split')
    for r in rows:
        head={k:r[k] for k in meta}
        wide.append(dict(**head,**r['node_values'],**{'predictor:'+n:v for n,v in r['predictor_values'].items()}))
        long.extend(dict(**head,node_id=n,se=r['node_values'].get(n),predictor_se=r['predictor_values'].get(n)) for n in sorted(network['nodes']))
    return {'输入_SE宽表.csv':csv_text(wide),'输入_SE长表.csv':csv_text(long)}


def run_demo(config_dir,output_dir,progress=None):
    config_dir=Path(config_dir);output_dir=Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()): raise ValueError('输出目录非空')
    read=lambda n:json.loads((config_dir/n).read_text(encoding='utf-8'))
    network=read('workflow_demo_network.json');nodes=read('workflow_demo_nodes.json')
    gconfig=read('workflow_demo_generation.json');fconfig=read('workflow_demo_fit.json')
    generation,rows=generate(network,nodes,gconfig)
    # This calls the general adapter, not a demo-only fitting implementation.
    report=fit_network(network,rows,fconfig,progress)
    analysis=analyze_dependencies(report,rows,fconfig['analysis'])
    counts=dict(nodes=len(network['nodes']),edges=len(network['edges']),base_network_samples=len(generation['root_inputs']),
        observation_versions=len(rows),node_observations=len(rows)*len(network['nodes']),fit_jobs=len(report['jobs']),
        local_fits=sum(len(j['local_fits']) for j in report['jobs']),converged_local_fits=sum(l['status']=='fitted' for j in report['jobs'] for l in j['local_fits']),
        core_recursive_checks=sum(j['core_crosscheck_count'] for j in report['jobs']),intervention_effect_rows=len(analysis['effects']))
    notes=['# 11节点18边独立模拟与应用流程演示','',f"运行状态：{report['status']}。",'',dumps(counts),'',
        '本批使用论文网络拓扑，节点目标由互补、几何、加权机制及自身条件乘数生成，未将表7参数作为生成真值或拟合起点。',
        '3次独立重复，每次250组基础网络样本、200训练/50留出；σ=0/2配对。基础式、固定δ=-5与自由δ[-10,10]使用相同数据和划分。',
        '这是代表性完整流程演示，不是原论文真实参数恢复，也不代表所有场景或输入误差已校准。',
        '统一入口支持JSON记录、CSV宽表和CSV长表；本包保留三种输入和模型配置，可直接用于复跑。',
        '局部预测使用干净供给与接受节点观测目标；整网预测只使用干净根节点。模型预测不裁剪。',
        '依赖分析分参数、预测、退化情景和传播路径四层。退化基准预设为每组按sample_id排序的首条留出样本，保留0%与50%节点效能；不按误差或效果挑样本。',
        '路径是结构可达证据，不将多条路径影响相加，不把模型情景推演当成真实因果效应。参数约束不足、触界、越界及训练覆盖外推分别标记。']
    outputs={**export_outputs(report,analysis),**input_csvs(network,rows),'输入_拓扑.json':dumps(network),'输入_SE记录.json':dumps(rows),
        '输入_拟合配置.json':dumps(fconfig),'生成_节点机制.json':dumps(nodes),'生成_实验配置.json':dumps(gconfig),
        'generation_report.json':dumps(generation),'运行计数.json':dumps(counts),'结果说明.md':'\n'.join(notes)+'\n'}
    write_bundle(output_dir,outputs)
    return report,analysis,counts


def main():
    p=argparse.ArgumentParser();p.add_argument('--config-dir',type=Path,default=Path('data/fdna'));p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();r,_,counts=run_demo(a.config_dir,a.output_dir,lambda p:print(json.dumps(p,ensure_ascii=False),flush=True))
    print(dumps(counts));return 0 if r['status']=='complete' else 1

if __name__=='__main__': raise SystemExit(main())
