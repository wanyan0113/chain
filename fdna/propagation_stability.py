"""All-held-out propagation replay and paired base-case composition summaries, no refit."""
import argparse
from collections import defaultdict
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .model import ModelError
from .network_workflow import analyze_dependencies, validate_samples
from .structure import digest
from .workflow_common import csv_text, dumps, write_bundle


PROTOCOL = dict(id='flow06_frozen_all_holdout_propagation_v1', baseline_policy='all_holdout',
    retained_fractions=[0,.25,.5,.75,1], max_paths_per_node=1000, reuse_prediction_stability_draws=True,
    model_refit=False, output_clipping=False, missing_panel='reject', quantiles=[.025,.5,.975],
    baseline_quantiles=[.1,.9], interpretation='frozen_model_baseline_heterogeneity_and_mean_response_composition_stability')
FIELDS = ('group','model','intervened_node','target','retained_fraction')


def reachable_pairs(network):
    outgoing = {n:[] for n in network['nodes']}
    for e in network['edges']: outgoing[e['feeder']].append(e['receiver'])
    result = []
    for start in sorted(outgoing):
        pending = list(outgoing[start]); found = set()
        while pending:
            n = pending.pop()
            if n not in found: found.add(n); pending.extend(outgoing[n])
        result.extend((start,n) for n in sorted(found))
    return result


def summarize(analysis, fit, rows, draws):
    cases = sorted({r['base_case_id'] for r in rows if r['split']=='holdout'})
    if len(cases)<2 or draws.get('base_cases') != cases: raise ModelError('传播基准与成组抽样的基础样本不一致')
    splits = {}; row_map = {}; group_cases = defaultdict(set)
    for row in rows:
        b = row['base_case_id']
        if b in splits and splits[b]!=row['split']: raise ModelError('基础样本跨训练与留出')
        splits[b] = row['split']
        if row['split']=='holdout':
            key = row['fit_group'], row['sample_id']
            if key in row_map or b in group_cases[row['fit_group']]: raise ModelError('同组基础样本存在重复观测')
            row_map[key]=b; group_cases[row['fit_group']].add(b)
    if any(c!=set(cases) for c in group_cases.values()): raise ModelError('各环境留出基础样本面板不完整')
    indices = np.asarray(draws.get('indices'))
    if (indices.ndim!=2 or indices.shape[1]!=len(cases) or len(indices)<2 or
        not np.issubdtype(indices.dtype,np.integer) or np.any(indices<0) or np.any(indices>=len(cases))):
        raise ModelError('成组抽样索引无效')
    grouped = defaultdict(dict)
    for e in analysis['effects']:
        if e['effect'] is None or not math.isfinite(e['effect']) or e['blocked']: raise ModelError('传播响应受阻或非有限，拒绝不完整面板')
        if e['effect']>1e-8: raise ModelError('降低节点值后出现后代增加，违反固定非负FDNA单调性')
        if e['retained_fraction']==1 and abs(e['effect'])>1e-8: raise ModelError('保留比例1出现非零效应')
        key = tuple(e[k] for k in FIELDS)
        base = row_map.get((e['group'],e['baseline_sample']))
        if base is None or base in grouped[key]: raise ModelError('传播响应基准未知、非留出或重复')
        grouped[key][base] = e
    expected = {(j['fit_group'],j['model'],a,b,f) for j in fit['jobs'] for a,b in reachable_pairs(fit['network_snapshot'])
                for f in PROTOCOL['retained_fractions']}
    if set(grouped)!=expected or any(set(v)!=set(cases) for v in grouped.values()):
        raise ModelError('传播响应缺少预定关系、比例、模型或基础样本')
    keys = sorted(grouped); values = np.array([[-grouped[key][b]['effect'] for b in cases] for key in keys])
    resampled = np.empty((len(indices),len(keys)))
    for i, draw in enumerate(indices): resampled[i] = np.mean(values[:,draw],axis=1)
    q = np.quantile(resampled,PROTOCOL['quantiles'],axis=0,method='linear')
    summary = []
    for i,key in enumerate(keys):
        entries = [grouped[key][b] for b in cases]; own = values[i]
        summary.append(dict(zip(FIELDS,key),baseline_records=len(cases),mean_decrease=float(np.mean(own)),
            min_decrease=float(np.min(own)),max_decrease=float(np.max(own)),p10_decrease=float(np.quantile(own,.1)),
            p90_decrease=float(np.quantile(own,.9)),zero_response_count=int(np.sum(np.abs(own)<=1e-8)),
            outside_records=sum(e['outside'] for e in entries),
            target_predictor_extrapolated_records=sum(bool(e['extrapolated_inputs']) for e in entries),
            mean_resample_q025=float(q[0,i]),mean_resample_median=float(q[1,i]),mean_resample_q975=float(q[2,i]),
            resamples=len(indices)))
    return summary,resampled,cases,indices


def replay(fit, rows, legacy, draws, protocol):
    if protocol!=PROTOCOL: raise ModelError('传播方案与登记协议不一致')
    validate_samples(fit['network_snapshot'],rows,fit['config_snapshot'])
    if digest(dict(network=fit['network_snapshot'],rows=rows,config=fit['config_snapshot']))!=fit['input_sha256']:
        raise ModelError('冻结拟合的输入摘要与数据、拓扑或配置不一致')
    if legacy['config_snapshot']['retained_fractions']!=protocol['retained_fractions']:
        raise ModelError('不得静默改变原06保留比例')
    config={k:deepcopy(protocol[k]) for k in ('baseline_policy','retained_fractions','max_paths_per_node')}
    analysis=analyze_dependencies(fit,rows,config)
    lookup = {(tuple(e[k] for k in FIELDS),e['baseline_sample']):e for e in analysis['effects']}
    if len(lookup)!=len(analysis['effects']): raise ModelError('全基准响应主键重复')
    max_difference=0.; original_keys=set()
    for e in legacy['effects']:
        key = tuple(e[k] for k in FIELDS),e['baseline_sample']
        if key in original_keys or key not in lookup: raise ModelError('原首条基准响应重复或未被保留')
        original_keys.add(key); current=lookup[key]
        for field in ('baseline','forced_value','intervened','effect'):
            if e[field] is None or current[field] is None: raise ModelError('原基准响应未完整完成')
            difference=abs(e[field]-current[field]);max_difference=max(max_difference,difference)
            if difference>1e-8: raise ModelError('原首条基准的数值响应发生变化')
    summary,resampled,cases,indices=summarize(analysis,fit,rows,draws)
    checks=analysis['checks']; checked=[c for c in checks if c['status']=='checked']
    audit=dict(original_responses_compared=len(original_keys),original_max_difference=max_difference,
        core_crosschecks=sum(c.get('core_max_difference') is not None for c in checked),
        core_max_difference=max((c.get('core_max_difference') or 0. for c in checked),default=None),
        non_descendants_invariant=all(c['non_descendant_invariant'] for c in checked),
        baseline_job_pairs=len({(e['group'],e['model'],e['baseline_sample']) for e in analysis['effects']}),
        responses=len(analysis['effects']),response_summaries=len(summary),base_cases=len(cases),resamples=len(indices),
        outside_responses=sum(e['outside'] for e in analysis['effects']),
        target_predictor_extrapolated_responses=sum(bool(e['extrapolated_inputs']) for e in analysis['effects']))
    return dict(stage='frozen_all_holdout_propagation_stability',protocol=deepcopy(protocol),audit=audit,
        network_snapshot=deepcopy(fit['network_snapshot']),data_sha256=fit['data_sha256'],input_sha256=fit['input_sha256'],
        analysis=analysis,summary=summary,base_cases=cases,
        policies=dict(refitted=False,se_generated=False,topology_changed=False,output_clipped=False,
            parameter_uncertainty_included=False,training_resampled=False,causal_effect_established=False),
        limits=['基准P10/P90描述具体基准间差异；均值重采样范围描述冻结模型下的样本构成波动。',
                '不包括训练数据、参数或求解器不确定性，不作为参数置信区间。',
                '目标直接供给外推标记不表示整条路径的覆盖已验证；模型内干预不等于真实因果。']),resampled,indices


def figures(report):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'pdf.fonttype':42,'svg.fonttype':'path'})
    outputs={}; summary=report['summary']; groups=sorted({r['group'] for r in summary}); models=sorted({r['model'] for r in summary})
    terminals=sorted(set(report['network_snapshot']['nodes'])-{e['feeder'] for e in report['network_snapshot']['edges']})
    affected=sorted({r['intervened_node'] for r in summary}); zero=[r for r in summary if r['retained_fraction']==0]
    vmax=max(r['mean_decrease'] for r in zero if r['target'] in terminals)
    labels={'base':'基础式','free_delta':'自由δ'}
    def save(fig,name):
        for ext in ('png','svg'):
            buffer=BytesIO();fig.savefig(buffer,format=ext,dpi=200,bbox_inches='tight');outputs[name+'.'+ext]=buffer.getvalue()
        plt.close(fig)
    fig,axes=plt.subplots(len(models),len(groups),figsize=(14,8),squeeze=False,layout='constrained')
    for mi,model in enumerate(models):
        for gi,group in enumerate(groups):
            ax=axes[mi,gi];matrix=np.full((len(affected),len(terminals)),np.nan)
            for i,n in enumerate(affected):
                for j,t in enumerate(terminals):
                    row=next((r for r in zero if (r['group'],r['model'],r['intervened_node'],r['target'])==(group,model,n,t)),None)
                    if row: matrix[i,j]=row['mean_decrease']
            im=ax.imshow(np.ma.masked_invalid(matrix),vmin=0,vmax=vmax,cmap='Blues',aspect='auto')
            for i in range(len(affected)):
                for j in range(len(terminals)):
                    v=matrix[i,j];ax.text(j,i,'—' if np.isnan(v) else f'{v:.1f}',ha='center',va='center',color='white' if v>vmax*.6 else '#17334d')
            ax.set(xticks=range(len(terminals)),xticklabels=terminals,yticks=range(len(affected)),yticklabels=affected,
                title=group+' / '+labels.get(model,model),xlabel='末端目标',ylabel='被置零节点')
    fig.colorbar(im,ax=axes,label='30个留出基准的平均SE减少量')
    fig.suptitle('冻结模型：全部30个留出基准下的节点置零响应\n—表示结构不可达，不是零影响；不代表真实干预效应',fontsize=15)
    save(fig,'01_多基准末端平均响应')
    focus=terminals[0]; rows=[r for r in zero if r['target']==focus]
    fig,axes=plt.subplots(1,2,figsize=(14,7),sharex=True,sharey=True,layout='constrained')
    for i,r in enumerate(rows):
        for ax,low,high in [(axes[0],'p10_decrease','p90_decrease'),(axes[1],'mean_resample_q025','mean_resample_q975')]:
            ax.plot([r[low],r[high]],[i,i],color='#168c87',lw=3);ax.scatter(r['mean_decrease'],i,color='#17334d',s=28,zorder=3)
    for ax in axes:
        ax.set(yticks=range(len(rows)),yticklabels=[r['group']+' / '+labels.get(r['model'],r['model'])+' / '+r['intervened_node'] for r in rows],
            xlabel='末端'+focus+'的SE减少量');ax.grid(axis='x',alpha=.2)
    axes[0].set_title('基准异质性：具体响应的P10-P90');axes[1].set_title('均值构成稳定性：重采样中央95%经验范围')
    axes[0].invert_yaxis();fig.suptitle('节点置零对'+focus+'：同样的均值，两种不同范围\n两图共用横轴尺度；模型固定，不包括参数不确定性，不作置信或显著性解释',fontsize=15)
    save(fig,'02_基准差异与均值稳定性分开')
    fig,axes=plt.subplots(1,2,figsize=(13,4.8),layout='constrained');keys=[(g,m) for g in groups for m in models]
    for ax,field,title in [(axes[0],'outside','后代预测值越界'),(axes[1],'extrapolated_inputs','目标直接供给超训练范围')]:
        percentages=[]
        for group,model in keys:
            entries=[e for e in report['analysis']['effects'] if (e['group'],e['model'])==(group,model)]
            percentages.append(100*sum(bool(e[field]) for e in entries)/len(entries))
        bars=ax.bar(range(len(keys)),percentages,color='#c48b38')
        for bar,v in zip(bars,percentages):ax.text(bar.get_x()+bar.get_width()/2,v+.5,f'{v:.1f}%',ha='center',fontsize=9)
        ax.set(xticks=range(len(keys)),xticklabels=[g+'\n'+labels.get(m,m) for g,m in keys],ylabel='占本组全部下游响应的比例 / %',title=title,ylim=(0,105));ax.grid(axis='y',alpha=.15)
    fig.suptitle('全基准、五档保留比例的范围提示\n保留原始预测，不裁剪；目标供给覆盖不代表整条路径覆盖',fontsize=14)
    save(fig,'03_退化响应越界与外推')
    return outputs


def notes(report):
    a=report['audit']
    return f'''# 冻结模型的全留出基准传播补充分析

保留原FDNA参数和SE，把原首条留出基准扩展到每个任务全部30条留出记录。五档保留比例为0、0.25、0.5、0.75、1。干预值为节点的模型基准值乘保留比例；中间节点暂停入边，根输入和其他机制不变。节点自身不算其后代。

共{a['baseline_job_pairs']}个“模型任务×基准”组合、{a['responses']}条下游响应、{a['response_summaries']}行条件化摘要。原首条基准{a['original_responses_compared']}条响应均保留，最大数值差{a['original_max_difference']:.6g}。核心干预公式交叉核验{a['core_crosschecks']}次，最大差{a['core_max_difference']:.6g}；非后代不变检查通过。

## 图1：末端平均响应

![多基准末端平均响应](01_多基准末端平均响应.png)

每格是固定模型在30个不同留出基准下、某节点置零引起的末端平均SE减少量。横线表示结构上没有可达后代记录，不能读成观测到零影响；不同任务分量之间没有自动新增供给边。该图用于说明模型内传播，不能作真实节点因果重要度排名。

## 图2：两种范围分开看

![基准与均值范围](02_基准差异与均值稳定性分开.png)

左图为30条具体基准响应的P10-P90，描述基准异质性；右图是复用04包2000次成组索引后，平均减少量的2.5%-97.5%经验范围，描述均值对样本构成的波动。两图共用横轴尺度，两者统计对象不同，不应混称置信区间。图中展示节点置零对拓扑首个末端F1的影响，完整节点和五档比例均保留在CSV/JSON中。

## 图3：推演范围限制

![越界及外推](03_退化响应越界与外推.png)

全部响应中{a['outside_responses']}条预测值越界，{a['target_predictor_extrapolated_responses']}条目标直接供给超训练范围。输出没有裁剪；越界和外推保留为解释限制。外推标记只检查该目标的直接供给，不意味着传播链其他位置均在训练覆盖内。

## 边界

这补充了冻结模型的基准响应差异与均值构成稳定性，不包括训练重采样后重拟合、参数或求解器不确定性；不是参数置信区间、真实干预因果或新拓扑实验。没有改变原公式、参数、SE、边界和结构。原06包及此前汇报保留。
'''


def run(source_dir,draw_dir,protocol_path,output_dir):
    source=Path(source_dir);drawdir=Path(draw_dir);out=Path(output_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())): raise ModelError('输出目录须不存在或为空')
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    protocol=read(Path(protocol_path))
    if protocol!=PROTOCOL: raise ModelError('传播方案与登记协议不一致')
    from .flow_scenario import verify
    source_files=verify(source)
    for name,expected in read(drawdir/'manifest.json').items():
        if hashlib.sha256((drawdir/name).read_bytes()).hexdigest()!=expected: raise ModelError('04抽样来源校验失败')
    fit=read(source/'04_FDNA拟合/workflow_report.json');rows=read(source/'04_FDNA拟合/输入_SE记录.json')
    draw_report=read(drawdir/'stability_report.json')
    if draw_report['source_data_sha256']!=fit['data_sha256']: raise ModelError('抽样与拟合数据来源不同')
    draw_path=drawdir/'成组抽样索引.json';draws=read(draw_path)
    print(dumps(dict(stage='replay_started',baselines='all_holdout',fdna_refitted=False)),flush=True)
    report,resampled,indices=replay(fit,rows,read(source/'04_FDNA拟合/dependency_analysis.json'),draws,protocol)
    report['source']=dict(directory=str(source.resolve()),draw_directory=str(drawdir.resolve()),verified_files=source_files,
        draw_sha256=hashlib.sha256(draw_path.read_bytes()).hexdigest(),fit_sha256=hashlib.sha256((source/'04_FDNA拟合/workflow_report.json').read_bytes()).hexdigest())
    buffer=BytesIO();np.savez_compressed(buffer,mean_decreases=resampled)
    outputs={'propagation_report.json':dumps(report),'多基准响应摘要.csv':csv_text(report['summary']),
        '逐条退化响应.csv':csv_text(report['analysis']['effects']),'核验状态.csv':csv_text(report['analysis']['checks']),
        '原成组抽样索引.json':dumps(dict(base_cases=report['base_cases'],indices=indices.tolist())),
        '均值重采样结果.npz':buffer.getvalue(),'结果说明.md':notes(report),'固定传播方案.json':dumps(protocol)}
    outputs.update(figures(report));write_bundle(out,outputs);verify(source);print(dumps(report['audit']));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='冻结FDNA的全留出基准传播响应与均值成组稳定性')
    parser.add_argument('--source-dir',type=Path,required=True);parser.add_argument('--draw-dir',type=Path,required=True)
    parser.add_argument('--protocol',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();run(args.source_dir,args.draw_dir,args.protocol,args.output_dir)
