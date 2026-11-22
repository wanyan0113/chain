"""Freeze the compact independent 11-node demo before generating or fitting data."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
from fdna.workflow_common import settings,dumps

root=Path(__file__).resolve().parents[1]
reference=json.loads((root/'data/fdna/arcane_case.json').read_text(encoding='utf-8'))
network=dict(id='independent_11_nodes_18_edges',nodes=[n['id'] for n in reference['nodes']],
    edges=[{k:e[k] for k in ('feeder','receiver')} for e in reference['edges']])
receivers=sorted({e['receiver'] for e in network['edges']})
nodes={}
kinds={'S5':'complementary','S6':'geometric','F7':'weighted','F9':'geometric','F11':'weighted'}
for receiver in receivers:
    parents=sorted(e['feeder'] for e in network['edges'] if e['receiver']==receiver)
    kind=kinds[receiver]
    c=dict(node_id=receiver,parent_nodes=parents,mechanism_type=kind,configuration_source='manual',
        condition_function=dict(type='product_conditions',values=dict(C=.8,M=.7,E=.2),capability_base=.55,capability_gain=.45,
            task_base=.8,task_gain=.2,environment_penalty=.3))
    if kind=='complementary': c['support_coefficients']=dict(zip(parents,[.9,.78,.65]))
    else: c['weights']=dict(zip(parents,[.62,.38] if receiver=='F7' else [.10,.20,.25,.20,.25] if receiver=='F11' else [1/len(parents)]*len(parents)))
    nodes[receiver]=c
generation=dict(format_version='1.0',id='workflow_11_node_generation',basis='三次独立重复的代表性完整流程演示；表7只提供拓扑，不提供生成或拟合真值。',
    seed=20261122,repetitions=3,base_cases=250,train_count=200,noise_std=[0,2],process_std=0.,interaction_gamma=0.,
    groups={'fixed':dict(C=.8,M=.7,E=.2)},root_sampling=dict(normal=[.65,.98],degraded=[.2,.6],shared_factor_weight=.15,
        fractions=dict(normal=.5,single=.2,double=.2,all=.1)))
fit=dict(input_role='clean_predictors_observed_targets',pool_scenarios=False,data_kind='simulated',
    models={mode:settings(mode,receivers,seed=20261122,starts=12,max_nfev=4000,delta=-5) for mode in ('base','fixed_delta','free_delta')},
    analysis=dict(retained_fractions=[0.,.5],max_paths_per_node=1000))
values={'workflow_demo_network.json':network,
    'workflow_demo_nodes.json':dict(format_version='1.0',id='manual_independent_11_node_mechanisms',basis='独立任务生成假设；系数不解释为FDNA真值',nodes=nodes),
    'workflow_demo_generation.json':generation,'workflow_demo_fit.json':fit}
for name,value in values.items():
    dest=root/'data/fdna'/name
    if dest.exists(): raise SystemExit('Refusing to overwrite frozen configuration '+name)
    dest.write_text(dumps(value),encoding='utf-8')
print('Frozen demo network, independent mechanisms, generation and fitting configurations')
