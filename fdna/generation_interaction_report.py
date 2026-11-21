"""Primary interaction model comparison and explicitly unfitted strength controls."""
import argparse
from collections import Counter
import io
import math
from pathlib import Path
import statistics

from source_data.common import read_json,write_outputs
from .generation_varying_experiment import csv_rows,dumps
from .model import ModelError
from .structure import digest


def paired_interaction_changes(current,reference,source_group):
    keys=('model','noise_sigma','repetition','node','split','target','prediction')
    lookup={tuple(r[k] for k in keys):r for r in reference['metrics'] if r['group']==source_group}
    if len(lookup)!=sum(r['group']==source_group for r in reference['metrics']):raise ModelError('交互来源指标重复')
    if len({tuple(r[k] for k in keys) for r in current['metrics']})!=len(current['metrics']):raise ModelError('主交互指标重复')
    rows=[]
    for r in current['metrics']:
        key=tuple(r[k] for k in keys)
        if key not in lookup or lookup[key]['count']!=r['count']:raise ModelError('交互对照分组或样本数不匹配')
        old=lookup[key]
        rows.append(dict(zip(keys,key),reference_rmse=old['rmse'],interaction_rmse=r['rmse'],rmse_change=r['rmse']-old['rmse'],
            reference_mae=old['mae'],interaction_mae=r['mae'],mae_change=r['mae']-old['mae'],
            reference_mean_residual=old['mean_residual'],interaction_mean_residual=r['mean_residual']))
    if len(rows)!=len(lookup):raise ModelError('交互与来源指标覆盖范围不同')
    return rows


def build_report(generation,base,comparison,summary,reference_generation,reference_comparison):
    if (generation['source_varying_generation_sha256']!=digest(reference_generation)
        or comparison['generation_sha256']!=digest(generation) or comparison['baseline_fit_sha256']!=digest(base)
        or summary['comparison_sha256']!=digest(comparison) or reference_comparison['generation_sha256']!=digest(reference_generation)
        or summary['status']!='complete'):
        raise ModelError('交互实验及冻结对照版本不一致，或拟合未完成')
    config=generation['interaction_config_snapshot'];primary=config['primary_group'];source=config['source_group']
    if summary['config_snapshot']['groups']!=[primary]:raise ModelError('本汇报入口仅接受首轮主交互组拟合')
    pairs=paired_interaction_changes(comparison,reference_comparison,source)
    nodes=[n for n in generation['topology']['order'] if n in generation['topology']['receivers']]
    structural={(r['group'],r['repetition'],r['node'],r['split']):r for r in generation['interaction_structural_by_split']}
    checked=[]
    for job in base['jobs']:
        for m in job['metrics']:
            if m['target']!='clean' or m['mode']!='local':continue
            bound=structural[job['group'],job['repetition'],m['node'],m['split']]['clean_local_base_rmse_lower_bound']
            if not math.isclose(bound,m['rmse_lower_bound'],abs_tol=1e-10):raise ModelError('交互组实际数据下界核对失败')
            checked.append(dict(group=job['group'],repetition=job['repetition'],noise_sigma=job['noise_sigma'],
                node=m['node'],split=m['split'],rmse=m['rmse'],structural_lower_bound=bound))
    generation_stats=[];clipping=[]
    for group,gamma in sorted(config['gamma_groups'].items(),key=lambda pair:pair[1]):
        for n in nodes:
            rows=[r for r in generation['interaction_samples'] if r['group']==group and r['node']==n]
            for key in ('local_interaction_deduction','total_clean_shift','interaction_clean'):
                values=[r[key] for r in rows]
                generation_stats.append(dict(group=group,gamma=gamma,node=n,quantity=key,count=len(rows),mean=statistics.mean(values),
                    sd=statistics.stdev(values),minimum=min(values),maximum=max(values),state_clipped_count=sum(r['state_clipped'] for r in rows),
                    state_zero_count=sum(r['interaction_clean']==0 for r in rows),fdna_fitted=group==primary))
        for sigma in summary['config_snapshot']['noise_std']:
            rows=[r for r in generation['observations'] if r['group']==group and r['noise_sigma']==sigma]
            for n in nodes:
                clipping.append(dict(group=group,gamma=gamma,noise_sigma=sigma,node=n,count=len(rows),
                    observed_clipped_count=sum(not 0<=r['node_values_clean'][n]+r['observation_noise'][n]<=100 for r in rows),
                    observed_zero_count=sum(r['node_values_observed'][n]==0 for r in rows),
                    observed_100_count=sum(r['node_values_observed'][n]==100 for r in rows)))
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import rc_context
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    outputs={};modes=[('base','基础式','#367AA8'),('fixed_delta','固定δ','#32986B'),('free_delta','自由δ','#D78C36')]
    def select(rows,mode,sigma,node='R',prediction='recursive'):
        return [r for r in rows if r['model']==mode and r['noise_sigma']==sigma and r['node']==node
            and r['split']=='holdout' and r['target']=='clean' and r['prediction']==prediction]
    def save(fig,name):
        for ext in ('png','svg'):
            stream=io.BytesIO();fig.savefig(stream,format=ext,dpi=180);outputs[name+'.'+ext]=stream.getvalue()
    with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10}):
        fig=Figure(figsize=(12,5),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,2)
        for ax,sigma in zip(axes,(0,8)):
            for i,(mode,label,color) in enumerate(modes):
                rows=select(pairs,mode,sigma);values=[[r[k] for r in rows] for k in ('reference_rmse','interaction_rmse')]
                ax.bar(np.arange(2)+(i-1)*.24,[statistics.mean(v) for v in values],width=.24,
                    yerr=[statistics.stdev(v) for v in values],capsize=3,color=color,label=label)
            ax.set(xticks=[0,1],xticklabels=['无交互γ=0','主交互γ=0.15'],ylabel='末端R整网验证RMSE（SE点）',title=f'观测噪声σ={sigma}')
            ax.grid(axis='y',alpha=.2);ax.legend(fontsize=8)
        fig.suptitle('冻结对照与主交互组（10次重复均值±样本标准差，过程波动关闭）');save(fig,'interaction_vs_reference')
        fig=Figure(figsize=(12,4.8),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,len(nodes))
        for ax,n in zip(axes,nodes):
            groups=[(source,0)]+sorted(config['gamma_groups'].items(),key=lambda p:p[1])
            means=[];deviations=[]
            for group,gamma in groups:
                rows=[r for r in generation['clean_runs'] if r['group']==group]
                values=[statistics.mean(r['node_values_clean'][n] for r in rows if r['repetition']==rep)
                        for rep in range(1,generation['config_snapshot']['repetitions']+1)]
                means.append(statistics.mean(values));deviations.append(statistics.stdev(values))
            ax.errorbar([g[1] for g in groups],means,yerr=deviations,marker='o',capsize=3,color='#367AA8')
            ax.set(title=n,xlabel='预设交互强度γ',ylabel='生成干净SE的重复内均值',xticks=[g[1] for g in groups]);ax.grid(alpha=.2)
        fig.suptitle('生成端强弱响应（误差棒为10次重复SD；0.05/0.30尚未进行FDNA拟合）');save(fig,'interaction_strength_generation')
        fig=Figure(figsize=(12,4.8),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,len(nodes))
        for ax,n in zip(axes,nodes):
            local=select(comparison['metrics'],'base',0,n,'local')
            bounds=[r['clean_local_base_rmse_lower_bound'] for r in generation['interaction_structural_by_split']
                    if r['group']==primary and r['node']==n and r['split']=='holdout']
            values=[[r['rmse'] for r in local],bounds]
            ax.bar([0,1],[statistics.mean(v) for v in values],yerr=[statistics.stdev(v) for v in values],capsize=3,color=['#367AA8','#9AA8B6'])
            ax.set(title=n,xticks=[0,1],xticklabels=['基础式局部RMSE','同数据结构下界'],ylabel='SE点');ax.grid(axis='y',alpha=.2)
        fig.suptitle('主交互组：实际干净上游、干净验证目标、观测σ=0（均值±样本标准差）');save(fig,'interaction_structural_bound')
    lines=['# 首轮交互效应实验结果解释','',
        '本轮对应第四次修订补充版第7节及13.3节。在逐接受节点独立变化C/M/E的七节点网络上，使用交互扣减100γE(1−C)，按实际交互后的上游状态逐层重算任务机制，最后截断到[0,100]。过程波动关闭，观测噪声独立形成记录且不向下游传递。',
        '', '交互γ=0.05、0.15、0.30均预先指定，没有按验证误差选择。其中γ=0.15完成正式FDNA拟合；γ=0.05及0.30本轮仅生成及核验样本，后续再作完整拟合敏感性对照。它们是模拟假设，不是原论文的α/β或实证交互参数。',
        '', '各强度使用相同的2500个基础网络（10次重复，每次200训练、50验证）及四档配对观测噪声。三个强度新增7500个干净网络和30000个观测版本；主交互组的三模型共120组计算、360次接受节点拟合。噪声版本和强度版本不能计为独立推演次数。',
        '', '主组沿用固定δ=-5、自由δ范围[-10,10]。每个接受节点/噪声/重复估计一个常数δ，训练估计、验证沿用；FDNA预测端只读取SE，不直接读取γ或C/M/E。预测保持不裁剪。',
        '', '| 观测σ | 基础式R | 固定δ R | 自由δ R | 自由δ比同组基础式改善次数 |', '| ---: | ---: | ---: | ---: | ---: |']
    for sigma in summary['config_snapshot']['noise_std']:
        rows={mode:select(comparison['metrics'],mode,sigma) for mode,_,_ in modes}
        base_by_rep={r['repetition']:r['rmse'] for r in rows['base']}
        improvement=sum(r['rmse']<base_by_rep[r['repetition']] for r in rows['free_delta'])
        lines.append(f'| {sigma} | '+' | '.join(f"{statistics.mean(r['rmse'] for r in rows[m]):.3f} ± {statistics.stdev(r['rmse'] for r in rows[m]):.3f}" for m,_,_ in modes)+f" | {improvement}/{len(rows['base'])} |")
    free=[r for r in summary['delta_summary'] if r['model']=='free_delta'];outside=summary['outside_predictions']
    lines+=['',f"自由δ有{sum(r['lower_bound_count'] for r in free)}/{sum(r['n'] for r in free)}次接受节点估计触下界、{sum(r['upper_bound_count'] for r in free)}次触上界，{sum(r['rank_deficient_count'] for r in free)}次局部秩不足。含δ越界预测共{len(outside)}条，逐条保留节点、划分、局部/整网与数值。它们不是独立实验次数。",'',
        '交互会同时改变中间真实状态和末端目标；与无交互组的配对RMSE差描述本次场景变化，不直接等于传播误差。局部扣减为0也不保证总状态不变，因为上游可能已经受到交互影响。扣减公式本身随γ线性变化，最终网络输出受非线性机制和截断影响，不要求与γ线性变化。',
        '', '基础式下界按主交互组的实际干净上游和目标重算，并与同划分拟合指标核对；不套用于含δ或整网误差。δ改善、参数可辨识性及输出范围分别解释，不据触界临时扩大界限或裁剪预测。',
        '', '图表：interaction_vs_reference比较主交互组与冻结无交互组；interaction_strength_generation只展示生成端强弱响应，不是强弱FDNA拟合结果；interaction_structural_bound展示主组局部误差与下界。comparison目录另含三模型噪声响应、配对改善和δ分布。',
        '', 'generation_statistics.csv分别列出本地扣减、相对无交互总状态变化、生成SE及截断数；observation_clipping.csv记录观测截断，不能与生成状态截断混用。interaction_effect_paired.csv保留同模型/噪声/重复/节点/划分/目标/预测方式的原指标与主交互指标。',
        '', '本轮完成γ=0.15主组，不代表强弱组FDNA敏感性拟合、多输入、11节点18边、输入测量误差或过程与交互联合实验已完成。','']
    if outside:lines+=[f"越界范围为{min(r['predicted'] for r in outside):.4f}至{max(r['predicted'] for r in outside):.4f}。",'']
    counts=Counter((r['model'],r['node'],r['prediction'],r['split']) for r in outside)
    outputs.update({'结果解释.md':'\n'.join(lines),'interaction_effect_paired.csv':csv_rows(pairs),
        'generation_statistics.csv':csv_rows(generation_stats),'observation_clipping.csv':csv_rows(clipping),
        'structural_crosscheck.csv':csv_rows(checked),
        'outside_counts.csv':csv_rows([dict(model=k[0],node=k[1],prediction=k[2],split=k[3],outside_count=v) for k,v in sorted(counts.items())]),
        '汇报来源核验.json':dumps(dict(generation_sha256=digest(generation),comparison_sha256=digest(comparison),
            reference_generation_sha256=digest(reference_generation),reference_comparison_sha256=digest(reference_comparison),
            paired_metric_rows=len(pairs),structural_checks=len(checked),fitted_groups=[primary],generated_strengths=config['gamma_groups']))})
    return outputs


def main():
    p=argparse.ArgumentParser();p.add_argument('--experiment-dir',type=Path,required=True);p.add_argument('--reference-dir',type=Path,required=True)
    a=p.parse_args();root=a.experiment_dir;ref=a.reference_dir
    outputs=build_report(read_json(root/'generation/generation_report.json'),read_json(root/'base/fit_report.json'),
        read_json(root/'comparison/delta_comparison_report.json'),read_json(root/'comparison/delta_repeats_report.json'),
        read_json(ref/'generation/generation_report.json'),read_json(ref/'comparison/delta_comparison_report.json'))
    write_outputs(root/'report',outputs)


if __name__=='__main__':main()
