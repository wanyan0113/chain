"""Replay frozen fitted networks over prespecified held-out baselines; never refit."""
import argparse
from collections import defaultdict
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from .model import ModelError
from .network_workflow import analyze_dependencies,read_samples
from .workflow_common import csv_text,dumps,write_bundle


def summarize_effects(effects):
    grouped=defaultdict(list)
    for e in effects:
        key=tuple(e[k] for k in ('group','model','intervened_node','target','retained_fraction'))
        grouped[key].append(e)
    rows=[]
    for key,entries in sorted(grouped.items()):
        values=[-e['effect'] for e in entries if e['effect'] is not None]
        rows.append(dict(zip(('group','model','intervened_node','target','retained_fraction'),key),
            baseline_records=len(entries),evaluated=len(values),blocked=sum(e['blocked'] for e in entries),
            mean_decrease=float(np.mean(values)) if values else None,
            min_decrease=min(values) if values else None,max_decrease=max(values) if values else None,
            p10_decrease=float(np.quantile(values,.1)) if values else None,
            p90_decrease=float(np.quantile(values,.9)) if values else None,
            outside=sum(e['outside'] for e in entries),
            extrapolated=sum(bool(e['extrapolated_inputs']) for e in entries)))
    return rows


def replay(report,rows,baseline_count=10):
    config=dict(retained_fractions=[0.,.5],max_paths_per_node=1000,
                baseline_policy='evenly_spaced_holdout',baseline_count=baseline_count)
    analysis=analyze_dependencies(report,rows,config)
    checked=[c for c in analysis['checks'] if c['status']=='checked']
    finite=[e for e in analysis['effects'] if e['effect'] is not None]
    positive=[e for e in finite if e['effect']>1e-8]
    if positive: raise ModelError('非负系数的固定FDNA模型中退化导致后代增加，请检查实现')
    groups=sorted({(j['fit_group'],j['model']) for j in report['jobs']})
    baselines=[dict(group=g,model=m,selected_baselines=len({c['baseline_sample'] for c in analysis['checks']
        if c['group']==g and c['model']==m and 'baseline_sample' in c})) for g,m in groups]
    audit=dict(selected_baselines=baselines,baseline_job_pairs=sum(r['selected_baselines'] for r in baselines),
        effects=len(analysis['effects']),evaluated=len(finite),blocked=sum(e['blocked'] for e in analysis['effects']),
        outside=sum(e['outside'] for e in analysis['effects']),extrapolated=sum(bool(e['extrapolated_inputs']) for e in analysis['effects']),
        skipped_node_baselines=sum(c['status']=='baseline_missing_or_outside' for c in analysis['checks']),
        no_holdout_jobs=sum(c['status']=='no_holdout_baseline' for c in analysis['checks']),
        core_crosschecks=sum(c.get('core_max_difference') is not None for c in checked),
        max_core_difference=max((c.get('core_max_difference') or 0. for c in checked),default=None),
        non_descendant_invariance=all(c['non_descendant_invariant'] for c in checked) if checked else None,
        monotonicity_violations=len(positive),refitted=False,
        input_data_sha256=report['data_sha256'],
        selection_rule='按sample_id排序，含首尾、按整数等距索引选取；不足预设条数时全部保留，不按目标或响应筛选。')
    return analysis,summarize_effects(analysis['effects']),audit


def figures(report,analysis,group_suffix=None):
    selected=[e for e in analysis['effects'] if group_suffix is None or e['group'].endswith(group_suffix)]
    if not selected: raise ModelError('所选分组没有可绘制的退化记录')
    models=sorted({e['model'] for e in selected});order=report['topology']['order']
    feeders={e['feeder'] for e in report['network_snapshot']['edges']}
    terminals=[n for n in order if n not in feeders and n in report['topology']['receivers']]
    labels={'base':'基础式','fixed_delta':'固定δ','free_delta':'自由δ'}
    outputs={};tables=[]
    scope='全部分组' if group_suffix is None else '分组后缀='+group_suffix
    def save(name,fig):
        for ext in ('png','svg'):
            stream=io.BytesIO();fig.savefig(stream,format=ext,dpi=180,bbox_inches='tight');outputs[name+'.'+ext]=stream.getvalue()
        plt.close(fig)
    zero=[e for e in selected if e['retained_fraction']==0.]
    maximum=max((max(0.,-e['effect']) for e in zero if e['effect'] is not None),default=1.) or 1.
    fig,axes=plt.subplots(1,len(models),figsize=(max(6,4.3*len(models)),6.5),squeeze=False,layout='constrained')
    for ax,mode in zip(axes[0],models):
        matrix=np.full((len(order),len(terminals)),np.nan)
        for i,n in enumerate(order):
            for k,t in enumerate(terminals):
                entries=[e for e in zero if (e['model'],e['intervened_node'],e['target'])==(mode,n,t)]
                values=[-e['effect'] for e in entries if e['effect'] is not None]
                if values: matrix[i,k]=float(np.mean(values))
                tables.append(dict(model=mode,intervened_node=n,target=t,baseline_records=len(entries),evaluated=len(values),
                    mean_decrease=float(np.mean(values)) if values else None,
                    p10=float(np.quantile(values,.1)) if values else None,p90=float(np.quantile(values,.9)) if values else None))
        ax.imshow(np.ma.masked_invalid(matrix),cmap='Blues',vmin=0,vmax=maximum,aspect='auto')
        for i in range(len(order)):
            for k in range(len(terminals)):
                v=matrix[i,k];ax.text(k,i,'—' if np.isnan(v) else f'{v:.1f}',ha='center',va='center',fontsize=9,
                    color='white' if v>.65*maximum else '#222222')
        ax.set(xticks=range(len(terminals)),xticklabels=terminals,yticks=range(len(order)),yticklabels=order,
            title=labels.get(mode,mode),xlabel='下游末端',ylabel='被置零节点')
    fig.suptitle('多基准节点置零：末端SE减少量的描述性均值\n'+scope+'；仅统计可达后代，—表示无有效后代记录')
    save('图01_多基准末端响应均值',fig)
    target=terminals[-1]
    fig,axes=plt.subplots(1,len(models),figsize=(max(6,4.3*len(models)),6.5),squeeze=False,layout='constrained')
    for ax,mode in zip(axes[0],models):
        own=[r for r in tables if r['model']==mode and r['target']==target and r['evaluated']]
        for i,r in enumerate(own):
            ax.plot([r['p10'],r['p90']],[i,i],color='#628CAB',linewidth=3)
            ax.scatter([r['mean_decrease']],[i],color='#234C70',s=25,zorder=3)
        ax.set(yticks=range(len(own)),yticklabels=[r['intervened_node'] for r in own],xlabel='末端SE减少量（SE点）',title=labels.get(mode,mode))
        ax.grid(axis='x',alpha=.2)
    fig.suptitle(f'置零对{target}的影响随基准变化\n点=均值，线=P10至P90；描述分布，非置信区间；'+scope)
    save('图02_末端响应的基准差异',fig)
    fig,axes=plt.subplots(1,2,figsize=(10,4.6),layout='constrained')
    for ax,key,title in zip(axes,('outside','extrapolated_inputs'),('下游输出越界','目标节点供给超训练范围')):
        values=[]
        for mode in models:
            own=[e for e in selected if e['model']==mode]
            values.append(100*sum(bool(e[key]) for e in own)/len(own))
        ax.bar([labels.get(m,m) for m in models],values,color='#628CAB')
        for i,v in enumerate(values):ax.text(i,v+.5,f'{v:.1f}%',ha='center')
        ax.set(ylabel='占导出下游响应记录的比例（%）',title=title,ylim=(0,max(105,max(values)*1.2)));ax.grid(axis='y',alpha=.2)
    fig.suptitle('多基准退化的范围提示：保留0%与50%合并\n'+scope+'；保留原始预测，不裁剪')
    save('图03_退化推演的范围提示',fig)
    outputs['图表数据_置零末端汇总.csv']=csv_text(tables)
    return outputs


def main(argv=None):
    p=argparse.ArgumentParser(description='冻结拟合结果的多基准退化重算与汇报图，不运行优化器')
    p.add_argument('--report',type=Path,required=True);p.add_argument('--samples',type=Path,required=True)
    p.add_argument('--layout',choices=['wide','long'],default='wide');p.add_argument('--baseline-count',type=int,default=10)
    p.add_argument('--figure-group-suffix');p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args(argv)
    try:
        if a.output_dir.exists() and any(a.output_dir.iterdir()):raise ModelError('输出目录非空')
        report=json.loads(a.report.read_text(encoding='utf-8'));rows=read_samples(a.samples,report['network_snapshot'],a.layout)
        analysis,summary,audit=replay(report,rows,a.baseline_count)
        audit.update(report_file_sha256=hashlib.sha256(a.report.read_bytes()).hexdigest(),figure_group_suffix=a.figure_group_suffix)
        plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10})
        outputs={'多基准依赖分析.json':dumps(analysis),'多基准核验.json':dumps(audit),'多基准响应汇总.csv':csv_text(summary),
            '逐条退化响应.csv':csv_text(analysis['effects']),'逐条核验状态.csv':csv_text(analysis['checks']),
            '结构传播路径.csv':csv_text(analysis['paths'])}
        outputs.update(figures(report,analysis,a.figure_group_suffix))
        outputs['结果说明.md']=f'''# 多基准退化分析与再次复核

本包固定第22包已经拟合的参数，不生成新拟合结果。每个拟合任务从留出记录中按sample_id排序，预先规定取最多{a.baseline_count}条等距基准，包含首尾；不按误差、响应或参数表现挑选。该规则在本次运行前确定，属于原首条基准分析的补充。

共处理{audit['baseline_job_pairs']}个“拟合任务×基准”组合，导出{audit['effects']}条后代响应；其中{audit['blocked']}条受阻，{audit['outside']}条输出越界，{audit['extrapolated']}条目标节点供给超出训练范围。跳过{audit['skipped_node_baselines']}个缺失或越界的被干预节点基准，另有{audit['no_holdout_jobs']}个任务没有留出基准。跳过记录保留，不按结果另选替代样本。

完成{audit['core_crosschecks']}次与核心干预公式的交叉核验，最大差为{audit['max_core_difference']}；非后代不变检查为{audit['non_descendant_invariance']}。退化后后代增加的异常记录为{audit['monotonicity_violations']}条。在参数非负、模型固定的FDNA中，降低节点输入不应增加后代输出，该项是实现检查。

## 图表怎么讲

1. 图01展示节点置零后的末端平均减少量。每个数是多个基准的描述性平均，用于观察模型中的传播响应；不能据此给真实节点作确定排名。横线表示没有可统计的有效后代记录，不能当成零影响。
2. 图02展示对拓扑顺序最后一个末端节点的影响分布。点为均值，线为P10至P90，说明响应随基准变化；不是置信区间，也不是参数不确定性区间。不同独立重复与同一模型中的多个基准一起描述，不把它们都视为独立实验。
3. 图03保留0%和50%两档退化，统计原始响应的输出越界与目标节点供给外推比例。外推比例仅检查该目标节点的直接供给范围，不等于整条传播链均在训练覆盖内。

图的分组筛选为{a.figure_group_suffix or '全部'}；JSON、逐条表和按组汇总仍覆盖全部拟合任务。PNG适合汇报，SVG可缩放，没有PDF。

## 解释边界

多基准分析增加情景覆盖，不解决参数不可辨识、δ触界和真实数据缺失。固定参数下的响应分布不能替代参数补偿路径、重采样或置信区间。节点自身不计为其后代；干预中间节点时暂停其入边，保持根输入和其他节点机制不变。结构路径只列可达关系，多路径效应不相加。

原第22包首条留出基准的结果完整保留。本包是追加结果，不覆盖历史图表。剩余16组系数诊断继续暂缓，尚未开展真实数据校准。
'''
        write_bundle(a.output_dir,outputs);print(dumps(audit));return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(dumps(dict(status='invalid',error=str(exc))));return 2


if __name__=='__main__':raise SystemExit(main())
