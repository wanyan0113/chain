"""Explain frozen FDNA responses with SOD/COD constraints, preserving near ties."""
import argparse
from collections import Counter,defaultdict
import hashlib
import io
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from .additive import ADDITIVE_MODEL,evaluate_additive
from .engine import evaluate
from .model import ModelError
from .network_workflow import predictors,read_samples,outside_training
from .structure import digest
from .workflow_common import csv_text,dumps,make_model,number,write_bundle


TOLERANCE=1e-8


def explain_node(node,tolerance=TOLERANCE):
    """Derivatives apply only to a unique local branch with parameters fixed."""
    number(tolerance,'分支容差',0)
    if node['kind']=='intervened':
        return dict(node=node['id'],status='intervened',value=node['value'],constraints=[],binding_candidates=[],
                    branch_type='intervened',edge_slopes=[],incoming_suspended=True)
    if not node['terms']:
        return dict(node=node['id'],status=node['status'],value=node['value'],constraints=[],binding_candidates=[],
                    branch_type='root',edge_slopes=[],incoming_suspended=False)
    if node['value'] is None:
        return dict(node=node['id'],status='blocked',value=None,constraints=[],binding_candidates=[],
                    branch_type='blocked',edge_slopes=[],incoming_suspended=False)
    before=node.get('base_before_delta')
    if before is None: before=node['value']
    constraints=[dict(branch='sod_mean',value=node['sod_mean'])]
    constraints.extend(dict(branch='cod:'+t['feeder'],value=t['cod']) for t in node['terms'])
    for c in constraints:c['slack']=c['value']-before
    candidates=sorted(c['branch'] for c in constraints if abs(c['slack'])<=tolerance)
    if not candidates or abs(min(c['value'] for c in constraints)-before)>TOLERANCE:
        raise ModelError('分支约束与核心公式输出不一致')
    slopes=[]
    for term in node['terms']:
        possible=[term['alpha']/len(node['terms']) if c=='sod_mean' else float(c=='cod:'+term['feeder']) for c in candidates]
        slopes.append(dict(feeder=term['feeder'],slope=min(possible) if len(candidates)==1 else None,
            candidate_slope_min=min(possible),candidate_slope_max=max(possible),
            derivative_unique=len(candidates)==1))
    return dict(node=node['id'],status='computed',value=node['value'],base_before_delta=before,
        delta=node.get('delta') or 0.,sod_mean=node['sod_mean'],cod_min=node['cod_min'],constraints=constraints,
        binding_candidates=candidates,exact_binding_constraints=node['binding_constraints'],
        branch_type='near_tie' if len(candidates)>1 else 'sod' if candidates==['sod_mean'] else 'cod',
        edge_slopes=slopes,incoming_suspended=False)


def fitted_core(report,job):
    model=make_model(report['network_snapshot'])
    fitted={l['node']:l['fit']['receivers'][0] for l in job['local_fits'] if l.get('fit')}
    for edge in model['edges']:
        row=fitted.get(edge['receiver'])
        if row and row['status']=='fitted':
            p=next(p for p in row['parameters'] if p['feeder']==edge['feeder'])
            edge.update(alpha=p['alpha'],beta=p['beta'])
    extension=dict(model=ADDITIVE_MODEL,basis='冻结已拟合参数的分支解释',
        delta_values={n:fitted[n].get('delta') if n in fitted and fitted[n]['status']=='fitted' else None for n in report['topology']['receivers']},
        output_policy='no_clipping')
    return model,extension


def core_state(report,job,model,extension,row,forced=None,tolerance=TOLERANCE):
    values=predictors(row,report['config_snapshot'])
    scene=dict(id=row['sample_id'],basis='已登记留出基准',node_values={n:values.get(n) for n in report['topology']['roots']})
    intervention=dict(basis='固定模型内退化，暂停干预节点入边',node_values=forced) if forced else None
    is_additive=report['config_snapshot']['models'][job['model']]['model']==ADDITIVE_MODEL
    result=evaluate_additive(model,scene,extension,intervention=intervention) if is_additive else evaluate(model,scene,intervention=intervention)
    return {n['id']:explain_node(n,tolerance) for n in result['nodes']}


def explain_dependencies(report,rows,analysis,tolerance=TOLERANCE):
    number(tolerance,'分支容差',0)
    if digest(rows)!=report['data_sha256']:raise ModelError('分支分析数据与拟合报告不一致')
    jobs={(j['fit_group'],j['model']):j for j in report['jobs']}
    records={(r['fit_group'],r['sample_id']):r for r in rows}
    baseline_states={};changed_states={};models={};responses=[];seen=set();maximum=0.
    reach={}
    for source in report['network_snapshot']['nodes']:
        reachable=set();pending=[source]
        while pending:
            n=pending.pop()
            for edge in report['network_snapshot']['edges']:
                if edge['feeder']==n and edge['receiver'] not in reachable:
                    reachable.add(edge['receiver']);pending.append(edge['receiver'])
        reach[source]=reachable
    def compare(actual,stored):
        if actual is None or stored is None:
            if actual is not stored:raise ModelError('分支重算的缺失状态与退化结果不一致')
            return 0.
        number(stored,'退化记录数值')
        discrepancy=abs(actual-stored)
        if discrepancy>TOLERANCE:raise ModelError('分支重算与已保存退化结果不一致')
        return discrepancy
    for e in analysis['effects']:
        head=(e['group'],e['model']);key=head+(e['baseline_sample'],)
        effect_key=key+(e['intervened_node'],e['retained_fraction'],e['target'])
        if effect_key in seen:raise ModelError('退化响应主键重复')
        seen.add(effect_key)
        if head not in jobs or (key[0],key[2]) not in records:raise ModelError('退化记录引用未知任务或样本')
        row=records[key[0],key[2]];job=jobs[head]
        if row['split']!='holdout':raise ModelError('分支解释只接收留出基准')
        if e['target'] not in reach.get(e['intervened_node'],set()):raise ModelError('退化目标不是被干预节点的后代')
        number(e['retained_fraction'],'保留比例',0,1)
        if head not in models:models[head]=fitted_core(report,job)
        if key not in baseline_states:baseline_states[key]=core_state(report,job,*models[head],row,tolerance=tolerance)
        base=baseline_states[key]
        source_value=base[e['intervened_node']]['value']
        if source_value is None:raise ModelError('被干预节点基准缺失')
        forced=source_value*e['retained_fraction']
        maximum=max(maximum,compare(forced,e['forced_value']))
        changed_key=key+(e['intervened_node'],e['retained_fraction'])
        if changed_key not in changed_states:
            changed_states[changed_key]=core_state(report,job,*models[head],row,{e['intervened_node']:forced},tolerance)
        changed=changed_states[changed_key];before=base[e['target']];after=changed[e['target']]
        effect=after['value']-before['value'] if after['value'] is not None and before['value'] is not None else None
        maximum=max(maximum,compare(before['value'],e['baseline']),compare(after['value'],e['intervened']),compare(effect,e['effect']))
        outside=after['value'] is not None and not 0<=after['value']<=100
        ranges=next(l.get('training_predictor_ranges',{}) for l in job['local_fits'] if l['node']==e['target'])
        extrapolated=outside_training({n:s['value'] for n,s in changed.items()},ranges)
        if outside!=e['outside'] or extrapolated!=e['extrapolated_inputs']:
            raise ModelError('响应的越界或外推标记与重算不一致')
        baseline_signature=before['binding_candidates'];signature=after['binding_candidates']
        responses.append(dict(group=key[0],model=key[1],baseline_sample=key[2],intervened_node=e['intervened_node'],
            retained_fraction=e['retained_fraction'],target=e['target'],baseline=before['value'],intervened=after['value'],effect=effect,
            baseline_type=before['branch_type'],post_type=after['branch_type'],baseline_candidates=baseline_signature,post_candidates=signature,
            branch_changed=baseline_signature!=signature if before['status']=='computed' and after['status']=='computed' else None,
            response_changed=abs(effect)>tolerance if effect is not None else None,
            baseline_sod=before.get('sod_mean'),post_sod=after.get('sod_mean'),baseline_cod=before.get('cod_min'),post_cod=after.get('cod_min'),
            post_constraints=after['constraints'],outside=e['outside'],extrapolated_inputs=e['extrapolated_inputs']))
    baseline=[];edges=[]
    for key,state in sorted(baseline_states.items()):
        job=jobs[key[:2]]
        for n in report['topology']['receivers']:
            info=state[n];head=dict(group=key[0],model=key[1],baseline_sample=key[2],node=n)
            local=next(l for l in job['local_fits'] if l['node']==n)
            baseline.append(dict(**head,**{k:v for k,v in info.items() if k!='node'},fit_flags=local['flags']))
            for slope in info['edge_slopes']:
                edges.append(dict(**head,**slope,branch_type=info['branch_type'],binding_candidates=info['binding_candidates'],fit_flags=local['flags']))
    counts=Counter(r['branch_type'] for r in baseline)
    audit=dict(baseline_job_pairs=len(baseline_states),baseline_receiver_records=len(baseline),baseline_edge_records=len(edges),
        response_records=len(responses),distinct_intervention_scenarios=len(changed_states),branch_types=dict(counts),
        branch_changes=sum(r['branch_changed'] is True for r in responses),near_tie_responses=sum('near_tie' in (r['baseline_type'],r['post_type']) for r in responses),
        zero_responses=sum(r['response_changed'] is False for r in responses),blocked_responses=sum(r['response_changed'] is None for r in responses),
        max_response_difference=maximum,tolerance=tolerance,refitted=False,parameter_uniqueness_established=False,
        topology_implies_active_path=False,finite_effects_decomposed=False,
        local_slope_policy='只有唯一候选分支时给出局部导数；近并列时只列候选斜率范围，不选唯一分支。')
    return dict(stage='frozen_dependency_branch_explanation',baseline_nodes=baseline,baseline_edges=edges,response_branches=responses,
        audit=audit,input_data_sha256=report['data_sha256'],analysis_config_snapshot=analysis['config_snapshot'])


def branch_figures(report,explained,group_suffix=None):
    nodes=report['topology']['receivers'];labels={'base':'基础式','fixed_delta':'固定δ','free_delta':'自由δ'}
    selected=lambda r:group_suffix is None or r['group'].endswith(group_suffix)
    baseline=[r for r in explained['baseline_nodes'] if selected(r)]
    response=[r for r in explained['response_branches'] if selected(r)]
    models=sorted({r['model'] for r in baseline});outputs={}
    if not models:raise ModelError('所选分组没有可绘制的分支记录')
    scope='全部分组' if group_suffix is None else '分组后缀='+group_suffix
    def save(name,fig):
        for ext in ('png','svg'):
            stream=io.BytesIO();fig.savefig(stream,format=ext,dpi=180,bbox_inches='tight');outputs[name+'.'+ext]=stream.getvalue()
        plt.close(fig)
    fig,axes=plt.subplots(1,len(models),figsize=(max(6,4.4*len(models)),4.8),squeeze=False,layout='constrained')
    for ax,model in zip(axes[0],models):
        bottom=np.zeros(len(nodes))
        for kind,label,color in [('sod','SOD均值限制','#4B8BA9'),('cod','单条COD限制','#D79B51'),('near_tie','并列或近并列','#9479AC'),('blocked','受阻','#BBBBBB')]:
            values=[]
            for n in nodes:
                own=[r for r in baseline if (r['model'],r['node'])==(model,n)]
                values.append(100*sum(r['branch_type']==kind for r in own)/len(own) if own else 0.)
            ax.bar(nodes,values,bottom=bottom,label=label,color=color);bottom+=values
        ax.set(ylim=(0,105),title=labels.get(model,model),ylabel='所选留出基准中的比例（%）')
    fig.legend(*axes[0,-1].get_legend_handles_labels(),loc='outside lower center',ncol=4,fontsize=9)
    fig.suptitle('基准输入下的限制分支\n'+scope+'；近并列保留多候选，不代表参数可辨识')
    save('图01_基准限制分支构成',fig)
    fig,axes=plt.subplots(1,len(models),figsize=(max(6,4.4*len(models)),4.8),squeeze=False,layout='constrained');summary=[]
    for ax,model in zip(axes[0],models):
        change=[];zeros=[]
        for n in nodes:
            own=[r for r in response if (r['model'],r['target'])==(model,n) and r['branch_changed'] is not None]
            change.append(100*sum(r['branch_changed'] for r in own)/len(own) if own else np.nan)
            zeros.append(100*sum(not r['response_changed'] for r in own)/len(own) if own else np.nan)
            summary.append(dict(model=model,target=n,evaluated=len(own),branch_changes=sum(r['branch_changed'] for r in own),
                zero_responses=sum(not r['response_changed'] for r in own),branch_change_percent=change[-1] if own else None,zero_response_percent=zeros[-1] if own else None))
            if not own:ax.text(len(change)-1,3,'无有效记录',rotation=90,fontsize=8,ha='center')
        x=np.arange(len(nodes));ax.bar(x-.18,change,.36,label='候选分支集合变化',color='#4B8BA9');ax.bar(x+.18,zeros,.36,label='响应近似为零',color='#D79B51')
        ax.set(xticks=x,xticklabels=nodes,ylim=(0,105),title=labels.get(model,model),ylabel='该目标有效后代响应中的比例（%）');ax.legend(fontsize=8)
    fig.suptitle('拓扑可达后的数值响应与分支变化\n'+scope+'；保留0%和50%合并，两种比例可重叠，不能相加')
    save('图02_分支变化与近零响应',fig)
    outputs['图表数据_分支变化与近零响应.csv']=csv_text(summary)
    # Prespecified example: first terminal, first group/sample, first two direct parents
    # that are receivers (otherwise first two direct parents). Never select by effect size.
    network=report['network_snapshot'];feeders={e['feeder'] for e in network['edges']}
    target=sorted(n for n in nodes if n not in feeders)[0]
    parents=sorted(e['feeder'] for e in network['edges'] if e['receiver']==target)
    sources=([n for n in parents if n in nodes] or parents)[:2]
    group=sorted({r['group'] for r in baseline})[0]
    sample=sorted({r['baseline_sample'] for r in baseline if r['group']==group})[0]
    examples=[]
    fig,axes=plt.subplots(len(sources),len(models),figsize=(max(7,5*len(models)),max(4,3.7*len(sources))),squeeze=False,layout='constrained')
    lookup={(r['group'],r['model'],r['baseline_sample'],r['node']):r for r in baseline}
    for i,source in enumerate(sources):
        for k,model in enumerate(models):
            ax=axes[i,k];own=[r for r in response if (r['group'],r['model'],r['baseline_sample'],r['intervened_node'],r['target'],r['retained_fraction'])==(group,model,sample,source,target,0.) and r['effect'] is not None]
            if not own:ax.text(.5,.5,'该预设基准无有效响应',ha='center');ax.axis('off');continue
            r=own[0];before=lookup[group,model,sample,target]['constraints'];after=r['post_constraints']
            branches=[c['branch'] for c in before];x=np.arange(len(branches))
            a={c['branch']:c['value'] for c in after}
            ax.bar(x-.18,[c['value'] for c in before],.36,label='基准',color='#4B8BA9')
            ax.bar(x+.18,[a[b] for b in branches],.36,label='置零后',color='#D79B51')
            ax.set(xticks=x,xticklabels=['SOD' if b=='sod_mean' else b.replace('cod:','COD:') for b in branches],
                ylabel='加δ前的候选值（SE点）',title=f"{labels.get(model,model)} / {source}置零 → {target}\n输出变化 {r['effect']:.2f} SE点")
            ax.tick_params(axis='x',labelrotation=35,labelsize=8);ax.grid(axis='y',alpha=.2)
            examples.append(dict(**r,baseline_constraints=before))
    handles,legend_labels=next((ax.get_legend_handles_labels() for ax in axes.flat if ax.get_legend_handles_labels()[0]),([],[]))
    if handles:fig.legend(handles,legend_labels,loc='outside lower center',ncol=2,fontsize=9)
    fig.suptitle(f'固定规则选取的分支示例：{group} / {sample}\n每组柱取最小值后再加δ；δ固定，但上游δ会改变下游输入')
    save('图03_退化前后候选约束对照',fig)
    outputs['预设分支示例.json']=dumps(dict(group=group,baseline_sample=sample,target=target,sources=sources,records=examples,
        selection='首个末端、首组、首基准，优先选前两个有入边的直接供给节点；不按响应挑选。'))
    return outputs


def main(argv=None):
    p=argparse.ArgumentParser(description='冻结模型的SOD/COD限制分支解释与汇报图表')
    p.add_argument('--report',type=Path,required=True);p.add_argument('--samples',type=Path,required=True)
    p.add_argument('--replay-dir',type=Path,required=True);p.add_argument('--layout',choices=['wide','long'],default='wide')
    p.add_argument('--figure-group-suffix');p.add_argument('--tolerance',type=float,default=TOLERANCE);p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args(argv)
    try:
        if a.output_dir.exists() and any(a.output_dir.iterdir()):raise ModelError('输出目录非空')
        read=lambda p:json.loads(p.read_text(encoding='utf-8'))
        report=read(a.report);audit=read(a.replay_dir/'多基准核验.json')
        if hashlib.sha256(a.report.read_bytes()).hexdigest()!=audit['report_file_sha256']:raise ModelError('拟合报告与多基准报告来源不一致')
        manifest=read(a.replay_dir/'manifest.json')
        for name in ('多基准依赖分析.json','多基准核验.json'):
            if hashlib.sha256((a.replay_dir/name).read_bytes()).hexdigest()!=manifest[name]:raise ModelError('多基准分析文件哈希不一致')
        analysis=read(a.replay_dir/'多基准依赖分析.json');rows=read_samples(a.samples,report['network_snapshot'],a.layout)
        result=explain_dependencies(report,rows,analysis,a.tolerance)
        result['audit'].update(report_file_sha256=audit['report_file_sha256'],figure_group_suffix=a.figure_group_suffix,
            replay_file_sha256=hashlib.sha256((a.replay_dir/'多基准依赖分析.json').read_bytes()).hexdigest())
        plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10})
        outputs={'分支解释.json':dumps(result),'分支核验.json':dumps(result['audit']),
            '基准节点分支.csv':csv_text(result['baseline_nodes']),'基准边局部斜率.csv':csv_text(result['baseline_edges']),
            '逐条响应分支.csv':csv_text(result['response_branches'])}
        outputs.update(branch_figures(report,result,a.figure_group_suffix))
        counts=result['audit']
        outputs['结果说明.md']=f'''# FDNA限制分支与传播响应解释

本包解释已有第26包的冻结模型响应，不重新拟合。对有导出后代响应的{counts['baseline_job_pairs']}个任务与基准组合，保存{counts['baseline_receiver_records']}条接受节点基准分支、{counts['baseline_edge_records']}条边的局部斜率记录，并对{counts['response_records']}条后代响应核对退化前后的候选限制分支。与已保存响应最大差为{counts['max_response_difference']}。没有后代响应记录的基准不另选样本补齐。

## 公式与边的含义

每个接受节点先计算SOD均值和每条供给边的COD（供给SE+β），取全部候选的最小值，再加该节点δ。δ在该节点内固定，不直接决定哪个候选最小；但上游δ改变输出后，会间接改变下游的分支。

若只有SOD均值限制，对第i条供给输入的局部导数是αᵢ/n；若仅COD:i限制，则该边的局部导数为1，其余边为0。这是固定参数、当前输入附近且分支不切换时的局部结果，不能直接乘成大幅置零效应，也不能等同于α、β可辨识。

候选值与最小值相差不超过{counts['tolerance']} SE点时保留为并列或近并列。CSV在此情形不给唯一导数，只列各候选分支对应斜率的最小与最大值；这不是置信区间，也不宣称近并列就是严格数学不可微。零斜率仅表示当前局部一阶不响应，大幅退化可能切换分支。

## 图表怎么讲

1. 图01展示所选留出基准中各接受节点的SOD、COD和近并列分支比例。它说明拟合模型在哪些约束下工作，不是依赖强度排名或置信概率。
2. 图02对拓扑可达的响应，分别统计候选分支集合变化与近零响应。没有切换也可有较大响应；切换不保证可观测幅度，两项不能相加。目标受阻时不计入有效比例，原始记录仍保留。
3. 图03按固定规则选首个末端、首组、首个基准和最多两个直接供给节点，对比退化前后所有候选值。最小柱决定加δ前输出，最终输出变化另见标题。没有按效果大小挑选例子。

COD候选值可以超过100，它们是取最小值前的候选约束，不等于最终SE预测越界；输出范围应查看最终输出及第26包越界标记。

图表分组筛选为{a.figure_group_suffix or '全部'}；JSON/CSV仍保留全部分组。不同基准与不同拟合重复合并只作描述；不能当作独立实验。PNG用于汇报，SVG可缩放，没有PDF。

## 本批统计及边界

全部分组中，{counts['branch_changes']}条响应的候选分支集合变化，{counts['near_tie_responses']}条响应在前后至少一端近并列，{counts['zero_responses']}条响应在当前容差内近零，{counts['blocked_responses']}条受阻。上述计数可重叠，不能相加。

本包不把结构路径解释成必然活跃的传播路径，不把有限退化效应按路径相加，也不消除参数补偿、δ触界和外推问题。边的斜率表保留原拟合诊断标记。真实数据验证与剩余16组系数诊断均未在本轮开展。
'''
        write_bundle(a.output_dir,outputs);print(dumps(counts));return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(dumps(dict(status='invalid',error=str(exc))));return 2


if __name__=='__main__':raise SystemExit(main())
