"""New abstract record-flow scenario: actual chain generation through SE fitting."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
from io import BytesIO
from itertools import product
import json
from pathlib import Path
import platform
import sys
import numpy as np
from design.engine import design
from design.knowledge import knowledge_outputs
from .bridge_intake import draft_bundle, draft_mapping, compile_bundle
from .equipment_bridge import selected_chain_records, bridge
from .bridge_observed import run as fit_observed
from .conditional_events import generate, validate_experiment, expected_probabilities
from .network_workflow import recursive, select_baselines
from .structure import digest
from .workflow_common import settings, csv_text, dumps, write_bundle

ROOT=Path(__file__).resolve().parents[1]
ROLE_ORDER=('insight','coordination','manifestation')


def canonical_document(document):
    doc=deepcopy(document)
    for field in ('tasks','artifacts','task_rules','artifact_rules','resource_groups'):
        doc[field]=sorted(doc.get(field,[]),key=lambda x:x['id'])
    doc['links']=sorted(doc['links'],key=lambda x:(x['from'],x['to']))
    return doc


def independent_enumeration(document):
    """Direct label/channel/count oracle, independent of ontology and scheduling code."""
    if any(t['start_s']!=0 for t in document['tasks']):raise ValueError('本场景仅核对同一逻辑窗口')
    artifacts={a['id']:a for a in document['artifacts']};links={(e['from'],e['to']):e for e in document['links']}
    candidates={};combinations=[]
    for task in sorted(document['tasks'],key=lambda t:t['id']):
        options=[[a['id'] for a in artifacts.values() if a['role']==role and a['available'] and task['attributes']['target_type'] in a['attributes']['target_types']] for role in ROLE_ORDER]
        accepted=[]
        for ids in product(*options):
            valid=True
            for k,pair in enumerate(zip(ids,ids[1:])):
                e=links.get(pair)
                valid=valid and e is not None and e.get('communicates') is True and not e['blocking_issue_ids']
                if e is not None:valid=valid and e['distance']['value']<e['max_distance']['value'] and (k!=1 or e.get('controls') is True)
            if valid:accepted.append(tuple(ids))
        candidates[task['id']]=sorted(accepted)
    for choice in product(*candidates.values()):
        usage=Counter(a for ids in choice for a in ids)
        conflicts=[dict(subject=a,used=n,capacity=artifacts[a]['capacity']) for a,n in sorted(usage.items()) if n>artifacts[a]['capacity']]
        combinations.append(dict(index=len(combinations)+1,assignment=[list(x) for x in choice],feasible=not conflicts,reasons=conflicts))
    return candidates,combinations


def check_design(document, report, expected):
    if report['status']!='complete' or not report['canonical_export_ready']:raise ValueError('新场景链网或能力知识映射未完成')
    candidates,combos=independent_enumeration(document)
    for task in report['tasks']:
        actual=sorted(tuple(c['artifacts'][r] for r in ROLE_ORDER) for c in task['chains'])
        if actual!=candidates[task['task']]:raise ValueError('候选链与独立标签/通道枚举不一致')
    feasible=[c for c in combos if c['feasible']]
    counts=dict(candidate_chains=sum(len(x) for x in candidates.values()),combinations=len(combos),feasible_networks=len(feasible))
    for key,value in counts.items():
        if value!=expected[key]:raise ValueError(f'{key}实际{value}与预期{expected[key]}不符')
    if report['network_search']['evaluated']!=len(combos) or report['network_search']['feasible']!=len(feasible):raise ValueError('引擎与独立容量枚举不一致')
    scores={c['score'] for t in report['tasks'] for c in t['chains']}
    if len(scores)!=1:raise ValueError('结构示例不允许用不同性能分数代替稳定选择')
    chosen=selected_chain_records(report)
    by_task={task:chain for task,chain in chosen.values()}
    actual=[list(by_task[t]['artifacts'][r] for r in ROLE_ORDER) for t in sorted(by_task)]
    first=min(c['assignment'] for c in feasible)
    if actual!=first:raise ValueError('实际选网不是语义排序首个可行组合')
    counts.update(candidate_counts={k:len(v) for k,v in candidates.items()},selection_key=actual,
        selection_policy='task_id_then_role_order_subject_ids_first_feasible',all_legacy_scores_equal=True,
        legacy_metrics='同值接口占位，不是实际秒数、精度或成本')
    report['scenario_selection_policy']=deepcopy(counts)
    return counts,combos


def register_mapping(report):
    draft=draft_mapping(report)
    mapping=deepcopy(draft);mapping['activities']=[];mapping['reviews']=[];slots=[];old_to_new={}
    chosen=selected_chain_records(report)
    for i,(task,chain) in enumerate(sorted(chosen.values(),key=lambda x:x[0]),1):
        ids=[f'A{i}',f'B{i}',f'F{i}'];cid=chain['id']
        slots.append(dict(task=task,chain_id=cid,nodes=ids,information_subject=chain['artifacts']['insight']))
        for k,(role,n,label) in enumerate(zip(ROLE_ORDER,ids,('侦察信息产品','指挥信息产品','抽象效应回执'))):
            prior=next(a for a in draft['activities'] if a['chain_id']==cid and a['role']==role)
            old_to_new[prior['id']]=n
            inputs=[] if k==0 else [('information:' if k==1 else 'instruction:')+task]
            outputs=[] if k==2 else [('information:' if k==0 else 'instruction:')+task]
            if k==0:outputs.append('heartbeat:'+task)
            mapping['activities'].append(dict(prior,id=n,label=label+'·'+task,context=task+'单逻辑窗口合成记录',
                state_identity_reason='按实际主体、功能和任务产品区分；共享身份由state_sharing明确登记',inputs=inputs,outputs=outputs,
                metric=dict(definition=label+'满足预设格式与逻辑一致性规则的比例；不是作战性能',raw_range=[0,1],direction='higher_is_better',normalization='100*x'),
                review_status='approved_assumption',review_note='按用户批准的MD登记合成产品定义，非专家实证确认'))
        for k,token in enumerate(('information:','instruction:')):
            mapping['reviews'].append(dict(id=task+'_flow_'+str(k),feeder=ids[k],receiver=ids[k+1],product=token+task,
                status='approved_assumption',rationale='MD预先登记的直接产品消费与实际设备通道同时成立，非真实因果证明'))
        mapping['reviews'].extend([
            dict(id=task+'_heartbeat',feeder=ids[0],receiver=ids[1],product='heartbeat:'+task,status='pending',rationale='只有通道、无指令活动消费，不作为额外供给边'),
            dict(id=task+'_shortcut',feeder=ids[0],receiver=ids[2],product=None,status='pending',rationale='只有间接可达关系，不添加直接消费捷径')])
    for policy in mapping['state_sharing']:
        policy['instance_ids']=[old_to_new[n] for n in policy['instance_ids']]
        policy.update(rationale='实际基准网中的同一主体产生不同任务产品',coupling='shared_uniform_state' if policy['role']=='insight' else 'fixed_common_condition',
            resource_scope='仅上游逻辑容量，不建模动态拥塞或实际作战资源竞争',review_status='approved_assumption',review_note='依照MD登记的合成状态方案')
    mapping['review_gate']=dict(status='approved_assumption',reviewed_by='用户授权MD场景的程序登记',note='只登记模拟工作假设，不表示自动完成真实依赖验证')
    network,side=bridge(report,mapping)
    return mapping,slots,network,side


def audit_generation(generation, slots, config):
    with np.load(BytesIO(generation['archive']),allow_pickle=False) as saved:
        flags=saved['events'];u=saved['uniforms'];h=saved['states'];mask=saved['train_mask'];envs=saved['environments']
        if not np.all((u>=0)&(u<1)):raise ValueError('均匀随机数越界')
        if int(mask.sum())!=config['train_count']:raise ValueError('训练数量不匹配')
        p=config['probabilities'];pen=config['penalties']
        counts=flags.sum(axis=-1)
        for ei,e in enumerate(envs):
            expected=saved['probabilities'][ei]
            a=(u[:,:,0,:]<expected[:,:,0,None]);b=(u[:,:,1,:]<np.where(a,p['command_good'],p['command_bad'])*(1-pen['command']*e))
            f=(u[:,:,2,:]<np.where(b,p['receipt_good'],p['receipt_bad'])*(1-pen['receipt']*e))
            if not np.array_equal(flags[ei],np.stack([a,b,f],axis=-2)):raise ValueError('事件档案不满足逐级条件抽取')
            for i in range(config['base_cases']):
                row=generation['rows'][ei*config['base_cases']+i]
                for j,slot in enumerate(slots):
                    for k,n in enumerate(slot['nodes']):
                        if row['node_values'][n]!=int(counts[ei,i,j,k])*100/config['trials']:raise ValueError('SE与档案事件计数不符')
        if not np.all(np.diff(flags.astype(np.int8),axis=0)<=0):raise ValueError('环境复用随机数校验失败')
        return dict(status='passed',micro_events=int(flags.size),uniform_draws=int(u.size),states_shape=list(h.shape),
            conditional_event_reconstruction=True,archived_counts_match_se=True,paired_environments=True,
            random_streams=generation['rng'],invariants=generation['invariants'])


def common_source_scenarios(slots, generation, config, fit):
    """Known synthetic H replay; distinct from interventions on observed SE."""
    subjects=sorted({s['information_subject'] for s in slots});state_map={s['base_case_id']:s for s in generation['states']};result=[]
    for job in fit['jobs']:
        rows=[r for r in generation['rows'] if r['fit_group']==job['fit_group'] and r['split']=='holdout']
        local={l['node']:l['fit']['receivers'][0] for l in job['local_fits'] if l.get('fit')}
        for row in select_baselines(rows,fit['config_snapshot']['analysis']):
            state=state_map[row['base_case_id']]['states'];e=next(a['environment'] for a in generation['audit'] if a['sample_id']==row['sample_id'])
            h=np.asarray([[state[s] for s in subjects]])
            original=expected_probabilities(h,subjects,slots,e,config)[0];root={s['nodes'][0]:float(original[j,0]*100) for j,s in enumerate(slots)}
            baseline,_=recursive(fit['network_snapshot'],local,root)
            for subject in subjects:
                for fraction in config['retained_fractions']:
                    changed=h.copy();changed[0,subjects.index(subject)]*=fraction
                    probs=expected_probabilities(changed,subjects,slots,e,config)[0]
                    inputs={s['nodes'][0]:float(probs[j,0]*100) for j,s in enumerate(slots)}
                    values,_=recursive(fit['network_snapshot'],local,inputs)
                    outside=any(not min(r['node_values'][n] for r in generation['rows'] if r['fit_group']==job['fit_group'] and r['split']=='train')<=v<=max(r['node_values'][n] for r in generation['rows'] if r['fit_group']==job['fit_group'] and r['split']=='train') for n,v in inputs.items())
                    for j,s in enumerate(slots):
                        n=s['nodes'][2];v=values[n]
                        result.append(dict(fit_group=job['fit_group'],model=job['model'],baseline_sample=row['sample_id'],subject=subject,
                            retained_fraction=fraction,target=n,baseline=baseline[n],prediction=v,
                            effect=None if v is None or baseline[n] is None else v-baseline[n],
                            mechanism_reference=float(probs[j,2]*100),mechanism_baseline=float(original[j,2]*100),
                            root_outside_training=outside,prediction_outside=v is not None and not 0<=v<=100,
                            basis='known_synthetic_H_and_expected_root_values_not_inferred_from_observed_data'))
    return result


def file_inventory(out):
    return {p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file() and p!=out/'完整包校验.json'}


def run(config_dir, output_dir, plan_path):
    config_dir=Path(config_dir);out=Path(output_dir);plan_path=Path(plan_path)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):raise ValueError('输出目录须不存在或为空，请为每次复跑选新目录')
    read=lambda name:json.loads((config_dir/name).read_text(encoding='utf-8-sig'))
    config=read('experiment.json');doc=canonical_document(read('design_input.json'))
    plan=plan_path.read_bytes()
    report=design(doc);design_counts,combos=check_design(doc,report,config['expected'])
    print(dumps(dict(stage='design',**design_counts)),flush=True)
    mapping,slots,network,side=register_mapping(report)
    for key,value in [('capability_nodes',len(network['nodes'])),('dependency_edges',len(network['edges']))]:
        if value!=config['expected'][key]:raise ValueError(key+'不符')
    write_bundle(out/'00_冻结输入',{'场景设计.md':plan,'design_input.json':dumps(doc),'experiment.json':dumps(config),
        '运行环境.json':dumps(dict(python=sys.version,executable=sys.executable,platform=platform.platform(),numpy=np.__version__,
            design_sha256=hashlib.sha256(plan).hexdigest(),scope='抽象产品质量场景，非真实装备性能'))})
    design_outputs={'design_report.json':dumps(report),'实际计数与排序.json':dumps(design_counts),'全部网络组合.json':dumps(combos),
        '全部网络组合.csv':csv_text(combos),'候选链.csv':csv_text([dict(c,task=t['task']) for t in report['tasks'] for c in t['chains']])}
    design_outputs.update(knowledge_outputs(doc,report));write_bundle(out/'01_链网生成',design_outputs)
    draft_bundle(report,out/'02_能力转换/自动草稿')
    compile_bundle(report,mapping,out/'02_能力转换/已登记映射')
    write_bundle(out/'02_能力转换/任务状态对照',{'slots.json':dumps(slots)})
    generation=generate(slots,config);audit=audit_generation(generation,slots,config)
    if len(generation['rows'])!=config['expected']['observations']:raise ValueError('观测数量不符')
    wide=[dict(**{k:r[k] for k in ('sample_id','base_case_id','scenario_id','fit_group','split')},**r['node_values']) for r in generation['rows']]
    write_bundle(out/'03_事件与SE',{'观测SE.json':dumps(generation['rows']),'观测SE.csv':csv_text(wide),
        '生成审计.json':dumps(generation['audit']),'主体状态与划分.json':dumps(generation['states']),
        '随机流与事件核验.json':dumps(audit),'微观事件.npz':generation['archive']})
    receivers=sorted({e['receiver'] for e in network['edges']})
    fconfig=dict(input_role='observed',data_kind='simulated',pool_scenarios=False,
        models={mode:settings(mode,receivers,seed=config['seed'],starts=config['fit']['starts'],max_nfev=config['fit']['max_nfev'],delta_bounds=config['fit']['delta_bounds']) for mode in ('base','free_delta')},
        analysis=dict(retained_fractions=config['retained_fractions'],max_paths_per_node=1000))
    (out/'02_能力转换/拟合配置.json').write_text(dumps(fconfig),encoding='utf-8')
    print(dumps(dict(stage='generation',observations=len(generation['rows']),**audit['invariants'])),flush=True)
    fit,analysis,counts=fit_observed(out/'01_链网生成/design_report.json',out/'02_能力转换/已登记映射/mapping.json',
        out/'03_事件与SE/观测SE.csv',out/'02_能力转换/拟合配置.json',out/'04_FDNA拟合')
    if counts['local_fits']!=config['expected']['local_fits']:raise ValueError('局部拟合数量与登记不符')
    common=common_source_scenarios(slots,generation,config,fit)
    write_bundle(out/'05_共同来源情景',{'已知合成H情景.csv':csv_text(common),'已知合成H情景.json':dumps(common),
        '解释.md':'# 已知合成H情景\n\n本表使用生成机制中已登记的H和干净期望根值重算，未重新拟合。它不是从观测SE中识别出的共享因果状态；基准与普通观测根值退化不同，不能混用。\n'})
    flags=Counter(flag for j in fit['jobs'] for l in j['local_fits'] for flag in l['flags'])
    layers=dict(chain_network=dict(status='passed',actual=design_counts),capability_conversion=dict(status='passed',nodes=len(network['nodes']),edges=len(network['edges'])),
        event_generation=dict(status='passed',observations=len(generation['rows']),micro_events=generation['micro_events'],audit=audit),
        parameter_calculation=dict(status='passed' if counts['status']=='complete' else 'partial',counts=counts,
            core_max_difference=max(j['core_max_difference'] for j in fit['jobs'])),
        model_applicability=dict(status='evidence_and_limits',flag_counts=dict(flags),claim='流程完成不证明真实因果、参数唯一或模型适用性；预测不裁剪'))
    from .flow_figures import render_figures, result_notes
    render_figures(out/'06_汇报图表',doc,report,combos,slots,generation,fit,analysis,common)
    (out/'分层验收.json').write_text(dumps(layers),encoding='utf-8')
    (out/'本次结果说明.md').write_text(result_notes(design_counts,counts,flags,common),encoding='utf-8')
    sources=[Path(__file__),Path(__file__).with_name('conditional_events.py'),Path(__file__).with_name('flow_figures.py'),
        Path(__file__).with_name('bridge_observed.py'),Path(__file__).with_name('equipment_bridge.py'),ROOT/'design/engine.py',ROOT/'design/plans.py',
        ROOT/'knowledge/synthetic-flow.ttl',ROOT/'knowledge/synthetic-flow.rules.json']
    (out/'实现文件校验.json').write_text(dumps({p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}),encoding='utf-8')
    (out/'完整包校验.json').write_text(dumps(file_inventory(out)),encoding='utf-8')
    print(dumps(layers));return layers


def verify(directory):
    out=Path(directory);expected=json.loads((out/'完整包校验.json').read_text(encoding='utf-8'))
    actual=file_inventory(out)
    if actual!=expected:raise ValueError('结果包有新增、缺失或内容变化的文件')
    print(dumps(dict(status='passed',verified_files=len(actual))))
    return len(actual)


def main():
    parser=argparse.ArgumentParser(description='MD抽象信息流程场景的完整可复跑实验')
    parser.add_argument('command',choices=['run','verify']);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--config-dir',type=Path,default=ROOT/'data/fdna/flow_scenario')
    parser.add_argument('--plan',type=Path)
    args=parser.parse_args()
    if args.command=='verify':verify(args.output_dir);return 0
    if args.plan is None:parser.error('run需要--plan指定已冻结MD')
    layers=run(args.config_dir,args.output_dir,args.plan);return 0 if layers['parameter_calculation']['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
