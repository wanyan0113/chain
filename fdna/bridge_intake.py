"""Draft, check and compile explicit capability mappings for an upstream report."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
from .equipment_bridge import bridge, selected_chain_records
from .model import ModelError
from .structure import digest
from .workflow_common import write_bundle, dumps, csv_text


ROLES=(('insight','信息产品质量'),('coordination','协调产品质量'),('manifestation','执行结果质量'))


def draft_mapping(report):
    chains=selected_chain_records(report);activities=[];reviews=[];shared=defaultdict(list)
    for cid,(task,chain) in sorted(chains.items()):
        ids={role:'N_'+digest([cid,role])[:8] for role,_ in ROLES}
        for role,label in ROLES:
            node=ids[role];subject=chain['artifacts'][role]
            inputs=[] if role=='insight' else [ids['insight' if role=='coordination' else 'coordination']+':product']
            outputs=[] if role=='manifestation' else [node+':product']
            activities.append(dict(id=node,chain_id=cid,role=role,subject=subject,label=label+'·'+task,
                context='待确认：'+task+'的任务产品与观测条件',state_identity_reason='待确认：按主体、功能和任务定义状态；共享装备不可自动独立复制',
                inputs=inputs,outputs=outputs,review_status='pending',review_note='',
                metric=dict(definition='待填写：实际产品质量指标及其测量或模拟依据',raw_range=[0,1],direction='higher_is_better',normalization='100*x')))
            shared[subject,role].append(node)
        for left,right in (('insight','coordination'),('coordination','manifestation')):
            reviews.append(dict(id='flow_'+ids[left]+'_'+ids[right],feeder=ids[left],receiver=ids[right],product=ids[left]+':product',
                status='pending',origin='role_template_proposal',
                rationale='由三角色活动模板提出，尚未确认真实产品消费关系；上游连接只提供渠道证据，不自动批准'))
    policies=[dict(subject=subject,role=role,instance_ids=sorted(ids),mode='task_conditioned_outputs',
        rationale='待确认：是否应定义不同任务产品状态',coupling='待确认：共同来源或固定条件如何保持一致',
        resource_scope='待确认：本次是否包含资源竞争与调度',review_status='pending',review_note='')
        for (subject,role),ids in sorted(shared.items()) if len(ids)>1]
    return dict(format_version='bridge-review-1',id='intake_'+digest(sorted(chains))[:12],
        source_report_sha256=digest(report),chain_ids=sorted(chains),activities=activities,reviews=reviews,state_sharing=policies,
        review_gate=dict(status='pending',reviewed_by='',note='模板仅为待审提案，不能直接当作真实依赖或进入拟合'))


def inspect_mapping(report, mapping):
    result=dict(status='blocked',issues=[],counts={})
    if not isinstance(mapping,dict):
        result['issues']=['映射须为JSON对象'];return result
    try:
        if mapping.get('format_version')!='bridge-review-1':raise ModelError('新接入入口需要bridge-review-1审查格式；旧手工案例仍可从原演示入口运行')
        network,side=bridge(report,mapping)
    except (ModelError,KeyError,TypeError,ValueError) as exc:
        result['issues']=[str(exc)]
    else:
        result.update(status='ready',counts=dict(nodes=len(network['nodes']),edges=len(network['edges']),
            selected_chains=len(side['scope_chain_ids']),excluded_chains=len(side['excluded_selected_chain_ids'])))
    for field,key in [('activities','pending_instances'),('state_sharing','pending_shared_policies')]:
        items=mapping.get(field,[])
        if isinstance(items,list):result['counts'][key]=sum(isinstance(i,dict) and i.get('review_status')=='pending' for i in items)
    rules=mapping.get('reviews',[])
    if isinstance(rules,list):result['counts']['pending_candidates']=sum(isinstance(r,dict) and r.get('status')=='pending' for r in rules)
    result['source_bound']=mapping.get('source_report_sha256')==digest(report)
    result['note']='ready仅表示声明与结构通过校验，不表示真实依赖已验证，也不保证SE数据能唯一识别参数。'
    return result


def draft_bundle(report, output_dir):
    mapping=draft_mapping(report)
    nodes=[dict(node=a['id'],chain=a['chain_id'],subject=a['subject'],role=a['role'],
        indicator=a['metric']['definition'],review_status=a['review_status'],review_note=a['review_note']) for a in mapping['activities']]
    write_bundle(output_dir,{'source_report.json':dumps(report),'mapping_draft.json':dumps(mapping),
        '能力实例待审.csv':csv_text(nodes),'依赖候选待审.csv':csv_text(mapping['reviews']),
        '共享状态待审.json':dumps(mapping['state_sharing']),'preflight.json':dumps(inspect_mapping(report,mapping)),
        '审查说明.md':'# 新报告映射草稿\n\n只读取上游最终已选链。能力产品与供需关系由角色模板提出，全部待审，不自动进入FDNA拟合。\n\n请在mapping_draft.json中确认能力指标、输入输出消费关系、共享状态与资源范围；逐项填写review_status/review_note，最后登记review_gate。边状态用approved_assumption、pending或rejected。批准仅代表工作假设登记，不能写成实证验证。\n\n源报告摘要已绑定。上游任务、装备或其他报告内容变化后，须重新审查；不能直接套用旧映射。CSV只作审阅清单，当前以JSON为执行依据，不会自动回读CSV中的编辑。\n'})
    return mapping


def compile_bundle(report, mapping, output_dir):
    result=inspect_mapping(report,mapping)
    if result['status']!='ready':raise ModelError('映射不可导出：'+'；'.join(result['issues']))
    network,side=bridge(report,mapping)
    write_bundle(output_dir,{'source_report.json':dumps(report),'mapping.json':dumps(mapping),
        '输入_拓扑.json':dumps(network),'衔接依据与审查.json':dumps(side),'preflight.json':dumps(result),
        '依赖候选审查.csv':csv_text(side['candidates'])})
    return result


def main():
    parser=argparse.ArgumentParser(description='已选装备报告的能力映射草稿、审查校验和拓扑导出')
    parser.add_argument('command',choices=['draft','check','compile']);parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--mapping',type=Path);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    if args.command!='draft' and args.mapping is None:parser.error('check/compile需要--mapping')
    report=json.loads(args.report.read_text(encoding='utf-8'))
    if args.command=='draft':
        value=draft_bundle(report,args.output_dir);print(dumps(inspect_mapping(report,value)));return 0
    mapping=json.loads(args.mapping.read_text(encoding='utf-8'))
    result=inspect_mapping(report,mapping)
    if args.command=='check':write_bundle(args.output_dir,{'preflight.json':dumps(result)})
    elif result['status']=='ready':compile_bundle(report,mapping,args.output_dir)
    else:
        print(dumps(result));return 2
    print(dumps(result));return 0 if result['status']=='ready' else 2


if __name__=='__main__':raise SystemExit(main())
