"""Read-only reporting of frozen fits: traceable cards, delta contrasts and mapping audit."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from .model import ModelError
from .structure import digest
from .workflow_common import csv_text, dumps, write_bundle


def split_identity(rows):
    splits={}
    for row in rows:
        base=row['base_case_id'];split=row['split']
        if base in splits and splits[base]!=split:raise ModelError('同一基础样本跨训练与留出')
        splits[base]=split
    return 'split_'+digest(sorted(splits.items()))[:20]


def parameter_rows(row, fit, diagnostic):
    parameters=[];spread=row.get('near_best_parameter_spread',{})
    strict=dict(zip(diagnostic.get('parameter_names',[]),diagnostic.get('parameter_spread',[])))
    bounds=fit.get('solver',{}).get('bounds',{})
    for p in row.get('parameters',[]):
        for kind in ('alpha','beta'):
            name=kind+':'+p['feeder'];low,high=bounds.get(kind,[0,1] if kind=='alpha' else [0,100]);value=p[kind]
            parameters.append(dict(name=name,value=value,lower=low,upper=high,distance_lower=value-low,distance_upper=high-value,
                boundary_distance_fraction=min(value-low,high-value)/(high-low),near_best_spread=spread.get(name),strict_near_spread=strict.get(name)))
    if row.get('delta') is not None:
        limits=row.get('delta_bounds');value=row['delta']
        parameters.append(dict(name='delta',value=value,lower=limits[0] if limits else None,upper=limits[1] if limits else None,
            distance_lower=value-limits[0] if limits else None,distance_upper=limits[1]-value if limits else None,
            boundary_distance_fraction=min(value-limits[0],limits[1]-value)/(limits[1]-limits[0]) if limits else None,
            near_best_spread=spread.get('delta'),strict_near_spread=strict.get('delta')))
    return parameters


def build_cards(report, rows, sidecar, generation_rng=None):
    if digest(rows)!=report['data_sha256']:raise ModelError('SE记录与拟合数据摘要不符')
    experiment='exp_'+report['input_sha256'][:20];split_version=split_identity(rows)
    cards=[];seen=set()
    for ji,job in enumerate(report['jobs']):
        selected=[r for r in rows if r['fit_group']==job['fit_group']]
        for li,local in enumerate(job['local_fits']):
            key=job['fit_group'],job['model'],local['node']
            if key in seen:raise ModelError('重复的拟合组、模型和接受节点记录')
            seen.add(key)
            fit=local.get('fit') or {};node_row=fit.get('receivers',[{}])[0]
            settings=fit.get('settings_snapshot',report['config_snapshot']['models'][job['model']])
            diagnostic=local.get('diagnostic') or {};sensitivity=node_row.get('sensitivity',{})
            flags=list(local['flags']);metrics=[m for m in job['metrics'] if m['node']==local['node']]
            points=[p for p in job['points'] if p['node']==local['node']]
            interpretations=['预测表现按训练/留出及局部/递推分别列示；未登记应用容许误差，不自动判定预测可用']
            if local['status']!='fitted':interpretations.append('拟合未完整完成，预测和参数解释证据受限')
            if 'LOCAL_RANK_DEFICIENT' in flags or 'INACTIVE_PARAMETERS' in flags:
                interpretations.append('当前训练点附近存在参数约束不足；低误差不能证明αβ唯一')
            else:interpretations.append('当前点附近观察到局部约束证据，仍不保证全局唯一')
            if 'MULTISTART_PARAMETER_SPREAD' in flags:interpretations.append('近优起点的参数存在跨度，不能把单次参数值当作稳定排序依据')
            if 'DELTA_AT_BOUNDARY' in flags:interpretations.append('δ触及登记边界；不解释为真实δ就在该边界')
            bounds=[m['rmse_lower_bound'] for m in metrics if m['prediction']=='local' and m['split']=='holdout' and m.get('rmse_lower_bound') is not None]
            if any(b>1e-8 for b in bounds):interpretations.append('基础式在本批观测上存在正的结构下界；含观测噪声，不能据此断定真实机制不适用')
            group_split=[dict(sample_id=r['sample_id'],base_case_id=r['base_case_id'],scenario_id=r['scenario_id'],split=r['split']) for r in selected]
            card=dict(experiment_id=experiment,fit_id='fit_'+digest([experiment,*key,digest(local)])[:24],
                fit_group=job['fit_group'],model=job['model'],node=local['node'],status=local['status'],flags=flags,
                node_definition=sidecar['capability_instances'][local['node']],data_sha256=report['data_sha256'],
                split_version=split_version,group_split_sha256=digest(group_split),model_config_sha256=digest(settings),
                model_config=settings,fit_seed=settings.get('seed'),generation_rng=generation_rng,
                source_record=f'workflow_report.json#/jobs/{ji}/local_fits/{li}',source_record_sha256=digest(local),
                source_fit_input_sha256=fit.get('input_sha256'),
                split_counts={s:sum(r['split']==s for r in selected) for s in ('train','holdout')},
                parameters=parameter_rows(node_row,fit,diagnostic),delta_policy=settings.get('delta_policy'),
                metrics=metrics,sensitivity=sensitivity,training_predictor_ranges=local.get('training_predictor_ranges',{}),
                prediction_coverage=[dict(split=s,rows=sum(p['split']==s for p in points),
                    local_outside_training=sum(p['split']==s and bool(p['local_extrapolated_inputs']) for p in points),
                    recursive_outside_training=sum(p['split']==s and bool(p['recursive_extrapolated_inputs']) for p in points)) for s in ('train','holdout')],
                attempts=node_row.get('attempts',[]),attempt_selection=node_row.get('attempt_selection'),
                near_best_start_count=node_row.get('near_best_start_count'),strict_near_start_count=diagnostic.get('near_start_count'),
                null_probe=diagnostic.get('null_probe'),omitted=node_row.get('omitted',[]),interpretations=interpretations,
                limits=['近优跨度不是置信区间；拟合器集合口径为选中训练RMSE+max(1e-6,1%选中训练RMSE)。',
                    '严格近等起点口径为成功起点的训练RMSE差≤1e-6；保留的有限扰动只检查当前样本，不证明全局等价。',
                    '局部Jacobian只取平滑活跃行；排除的分支并列行和阈值见sensitivity。'])
            cards.append(card)
    return cards


def mapping_audit(report, sidecar, source_kind):
    activities=sidecar['capability_instances'];nodes=[];edges=[]
    if set(activities)!=set(report['network_snapshot']['nodes']):raise ModelError('能力实例与拟合拓扑节点不符')
    for n,a in sorted(activities.items()):
        nodes.append(dict(node=n,subject=a['subject'],function=a['role'],task_context=a['context'],chain_id=a['chain_id'],
            label=a['label'],inputs=a['inputs'],outputs=a['outputs'],metric=a['metric']['definition'],
            raw_range=a['metric']['raw_range'],direction=a['metric']['direction'],normalization=a['metric']['normalization'],
            state_identity=a['state_identity_reason'],source_kind=source_kind,definition_evidence_form='scenario_or_user_declared',
            review_status=a.get('review_status'),review_note=a.get('review_note'),real_dependency_verified=False))
    for c in sidecar['candidates']:
        a,b=activities[c['feeder']],activities[c['receiver']];token=c.get('product')
        produced=bool(token and token in a['outputs']);consumed=bool(token and token in b['inputs'])
        if c['product_matched']!=(produced and consumed):raise ModelError('保存的产品匹配结果与实例定义不一致')
        edges.append(dict(candidate_id=c['id'],feeder=c['feeder'],receiver=c['receiver'],feeder_subject=a['subject'],receiver_subject=b['subject'],
            product=token,produced=produced,consumed=consumed,product_match_checked=True,exported=c['exported'],review_status=c['status'],
            rationale=c['rationale'],source_kind=source_kind,channel_evidence_form='upstream_declared_with_rule_checks',
            consumption_evidence_form='registered_assumption',product_match_evidence_form='computed_consistency_check',
            channel_evidence=c['equipment_link_evidence'],real_dependency_verified=False))
    exported={(e['feeder'],e['receiver']) for e in edges if e['exported']}
    expected={(e['feeder'],e['receiver']) for e in report['network_snapshot']['edges']}
    if exported!=expected:raise ModelError('登记导出边与拟合拓扑不一致')
    return nodes,edges


def delta_contrasts(cards):
    base={(c['fit_group'],c['node']):c for c in cards if c['delta_policy']=='excluded_for_base_check'}
    results=[]
    for card in cards:
        if card['delta_policy']=='excluded_for_base_check':continue
        prior=base.get((card['fit_group'],card['node']))
        if prior is None:continue
        if (card['data_sha256'],card['split_version'],card['group_split_sha256'])!=(prior['data_sha256'],prior['split_version'],prior['group_split_sha256']):
            raise ModelError('δ对照的数据或划分不一致')
        delta=next((p for p in card['parameters'] if p['name']=='delta'),{})
        row=dict(experiment_id=card['experiment_id'],fit_group=card['fit_group'],node=card['node'],
            base_fit_id=prior['fit_id'],comparison_fit_id=card['fit_id'],comparison_model=card['model'],delta_policy=card['delta_policy'],
            delta=delta.get('value'),delta_lower=delta.get('lower'),delta_upper=delta.get('upper'),
            delta_boundary='DELTA_AT_BOUNDARY' in card['flags'],
            rank_base=prior['sensitivity'].get('local_jacobian_rank'),parameter_count_base=prior['sensitivity'].get('parameter_count'),
            rank_comparison=card['sensitivity'].get('local_jacobian_rank'),parameter_count_comparison=card['sensitivity'].get('parameter_count'),
            probe=card['null_probe'],claim='预测差异、触界和参数约束分别记录；不推断δ的唯一物理成因')
        for kind in ('local','recursive'):
            for split in ('train','holdout'):
                bm=next((m for m in prior['metrics'] if m['prediction']==kind and m['split']==split),{})
                cm=next((m for m in card['metrics'] if m['prediction']==kind and m['split']==split),{})
                before,after=bm.get('rmse'),cm.get('rmse');prefix=kind+'_'+split
                row[prefix+'_base_rmse']=before;row[prefix+'_comparison_rmse']=after
                row[prefix+'_improvement']=before-after if before is not None and after is not None else None
        row['parameter_changes']=[dict(name=p['name'],base=next(q['value'] for q in prior['parameters'] if q['name']==p['name']),comparison=p['value']) for p in card['parameters'] if p['name']!='delta']
        results.append(row)
    return results


def fmt(value):
    if value is None:return '—'
    if isinstance(value,float):return f'{value:.6g}'
    if isinstance(value,(dict,list)):return json.dumps(value,ensure_ascii=False)
    return str(value).replace('|','/').replace('\n',' ')


def markdown_card(card):
    lines=[f"# {card['node']} · {card['fit_group']} · {card['model']}",'',
        f"- experiment_id：{card['experiment_id']}",f"- fit_id：{card['fit_id']}",f"- split_version：{card['split_version']}",
        f"- 数据摘要：{card['data_sha256']}",f"- 组划分摘要：{card['group_split_sha256']}",f"- 模型配置摘要：{card['model_config_sha256']}",
        f"- 拟合种子：{card['fit_seed']}；初值数：{card['model_config'].get('starts')}；求解上限：{card['model_config'].get('max_nfev')}",
        f"- 原始记录：{card['source_record']}；记录摘要：{card['source_record_sha256']}",
        f"- 主体/任务定义：{card['node_definition']['subject']} / {card['node_definition']['context']}",
        f"- 状态：{card['status']}；标记：{fmt(card['flags'])}",'','## 解释状态','',
        *['- '+v for v in card['interpretations']],'','## 预测表现','',
        '| 划分 | 预测方式 | 有效/可用行数 | RMSE | MAE | 预测越界条数 | 基础式局部结构下界 |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: |']
    for m in card['metrics']:
        lines.append('| '+' | '.join(fmt(v) for v in [m['split'],m['prediction'],str(m['evaluated'])+'/'+str(m['available']),m['rmse'],m['mae'],m['outside'],m.get('rmse_lower_bound')])+' |')
    lines+=['','## 参数与边界','',f"δ策略：{card['delta_policy']}。基础式未启用δ，不解释为自由估计得到0。",'',
        '| 参数 | 值 | 下界 | 上界 | 距下界 | 距上界 | 拟合器近优跨度 | 严格近等跨度 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for p in card['parameters']:lines.append('| '+' | '.join(fmt(p[k]) for k in ('name','value','lower','upper','distance_lower','distance_upper','near_best_spread','strict_near_spread'))+' |')
    lines+=['','## 分支、秩与输入范围','',
        '- 局部秩/待估参数数：'+fmt(card['sensitivity'].get('local_jacobian_rank'))+'/'+fmt(card['sensitivity'].get('parameter_count')),
        '- 不活跃参数：'+fmt(card['sensitivity'].get('inactive_parameters')),
        '- SoD活跃行数：'+fmt(card['sensitivity'].get('sod_active_rows'))+'；CoD活跃行数：'+fmt(card['sensitivity'].get('cod_active_rows')),
        '- 排除并列行：'+fmt(card['sensitivity'].get('excluded_tie_rows'))+'；并列阈值：'+fmt(card['sensitivity'].get('tie_tolerance')),
        '- 训练供给范围：'+fmt(card['training_predictor_ranges']),'- 覆盖检查：'+fmt(card['prediction_coverage']),
        '- 有限扰动：'+fmt(card['null_probe']),'','## 多初值结果','',
        '| 起点序号 | 起点类型 | 训练RMSE | 收敛 | nfev | 终点物理参数 |','| --- | --- | ---: | --- | ---: | --- |']
    for i,a in enumerate(card['attempts']):lines.append('| '+' | '.join(fmt(v) for v in [i,a['initial_source'],a['rmse'],a['success'],a['nfev'],a['parameters']])+' |')
    lines+=['','选中起点规则：'+fmt(card['attempt_selection']),'',
        '近优起点数：'+fmt(card['near_best_start_count'])+'；严格近等且成功起点数：'+fmt(card['strict_near_start_count']),'',
        *['- '+v for v in card['limits']],'- 完整模型设置、随机流标识与结构化字段见同名JSON卡片。','']
    return '\n'.join(lines)


def figures(out,cards,contrasts):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'],'axes.unicode_minus':False})
    groups=sorted({r['fit_group'] for r in contrasts});nodes=sorted({r['node'] for r in contrasts})
    fig,axes=plt.subplots(1,2,figsize=(13,4.7),sharey=True)
    for ax,kind in zip(axes,('local','recursive')):
        width=.8/max(1,len(groups))
        for gi,group in enumerate(groups):
            own={r['node']:r for r in contrasts if r['fit_group']==group}
            positions=[i-.4+width*(gi+.5) for i in range(len(nodes))]
            values=[own[n][kind+'_holdout_improvement'] for n in nodes]
            ax.bar(positions,values,width,label=group)
            for x,n,y in zip(positions,nodes,values):
                if own[n]['delta_boundary']:ax.plot(x,y,'ko',markersize=3)
        ax.set_xticks(range(len(nodes)),nodes);ax.axhline(0,color='gray',lw=.7)
        ax.set(title='局部留出误差改善' if kind=='local' else '整网留出误差改善',ylabel='基础式RMSE − 自由δ RMSE（SE点）')
        ax.legend(fontsize=8);ax.grid(axis='y',alpha=.15)
    fig.suptitle('正值仅表示预测误差下降；黑点表示δ触界，不代表参数解释可靠');fig.tight_layout()
    for suffix in ('png','svg'):fig.savefig(out/('δ与留出误差对照.'+suffix),dpi=170,bbox_inches='tight')
    plt.close(fig)


def run(source_dir,output_dir,source_kind='unverified_user_provided'):
    source=Path(source_dir);out=Path(output_dir)
    if source_kind not in ('synthetic_scenario','unverified_user_provided'):raise ModelError('未知来源性质')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):raise ModelError('输出目录须不存在或为空')
    from .flow_scenario import verify
    verify(source)
    fitdir=source/'04_FDNA拟合';read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    report=read(fitdir/'workflow_report.json');rows=read(fitdir/'输入_SE记录.json');side=read(fitdir/'衔接依据与审查.json')
    rng=read(source/'03_事件与SE/随机流与事件核验.json')['random_streams']
    cards=build_cards(report,rows,side,rng);nodes,edges=mapping_audit(report,side,source_kind);contrasts=delta_contrasts(cards)
    if not cards:raise ModelError('没有可整理的局部拟合记录')
    index=[];parameters=[];attempts=[];outputs={}
    for c in cards:
        name=c['fit_group']+'__'+c['model']+'__'+c['node']
        if any(ch in name for ch in ('/','\\')):raise ModelError('卡片名称不允许路径分隔符')
        path='诊断卡片/'+name
        outputs[path+'.md']=markdown_card(c);outputs[path+'.json']=dumps(c)
        index.append(dict(experiment_id=c['experiment_id'],fit_id=c['fit_id'],fit_group=c['fit_group'],model=c['model'],node=c['node'],
            card=path+'.md',split_version=c['split_version'],fit_seed=c['fit_seed'],rank=c['sensitivity'].get('local_jacobian_rank'),
            parameter_count=c['sensitivity'].get('parameter_count'),flags=c['flags'],interpretations=c['interpretations']))
        parameters.extend(dict(fit_id=c['fit_id'],fit_group=c['fit_group'],model=c['model'],node=c['node'],**p) for p in c['parameters'])
        attempts.extend(dict(fit_id=c['fit_id'],fit_group=c['fit_group'],model=c['model'],node=c['node'],attempt=i,**a) for i,a in enumerate(c['attempts']))
    flags=Counter(f for c in cards for f in c['flags'])
    stats=dict(experiment_id=cards[0]['experiment_id'],cards=len(cards),node_audit_rows=len(nodes),candidate_audit_rows=len(edges),
        exported_edges=sum(e['exported'] for e in edges),delta_pairs=len(contrasts),flag_counts=dict(flags),refitted=False,generated=False,
        source_dir=str(source.resolve()),source_kind=source_kind,source_package_index_sha256=hashlib.sha256((source/'完整包校验.json').read_bytes()).hexdigest())
    outputs.update({'诊断卡片索引.csv':csv_text(index),'诊断卡片索引.json':dumps(index),'参数与边界汇总.csv':csv_text(parameters),
        '全部初值结果.csv':csv_text(attempts),'δ对照.csv':csv_text(contrasts),'δ对照.json':dumps(contrasts),
        '能力变量审计.csv':csv_text(nodes),'依赖候选审计.csv':csv_text(edges),'映射审计.json':dumps(dict(nodes=nodes,edges=edges,shared_subjects=side['state_sharing'])),
        '整理计数.json':dumps(stats),'诊断卡片总表.md':'# 诊断卡片索引\n\n'+ '\n'.join('- ['+r['fit_group']+' / '+r['model']+' / '+r['node']+']('+r['card']+')' for r in index)+'\n'})
    from .diagnostic_overview import evidence_rows, overview_markdown, audit_markdown
    overview=evidence_rows(cards,index)
    outputs.update({'诊断结果总览.json':dumps(overview),'诊断结果总览.csv':csv_text(overview),
        '优化第一步总览.md':overview_markdown(overview,contrasts,stats,source_kind),
        '审计阅读表.md':audit_markdown(nodes,edges)})
    write_bundle(out,outputs);figures(out,cards,contrasts)
    (out/'全部产物校验.json').write_text(dumps({p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}),encoding='utf-8')
    print(dumps(stats));return stats


def main():
    parser=argparse.ArgumentParser(description='读取06冻结结果，整理诊断卡与映射审计，不重新拟合')
    parser.add_argument('--source-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--source-kind',choices=['synthetic_scenario','unverified_user_provided'],default='unverified_user_provided')
    args=parser.parse_args();run(args.source_dir,args.output_dir,args.source_kind)


if __name__=='__main__':main()
