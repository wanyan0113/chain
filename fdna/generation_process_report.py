"""Report process innovations separately from measurement noise and propagated shifts."""
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


def paired_process_changes(current,reference,source_group):
    keys=('model','noise_sigma','repetition','node','split','target','prediction')
    lookup={tuple(r[k] for k in keys):r for r in reference['metrics'] if r['group']==source_group}
    if len(lookup)!=sum(r['group']==source_group for r in reference['metrics']):
        raise ModelError('无过程波动对照指标存在重复')
    changes=[]
    if len({tuple(r[k] for k in keys) for r in current['metrics']})!=len(current['metrics']):
        raise ModelError('过程波动组指标存在重复')
    for row in current['metrics']:
        key=tuple(row[k] for k in keys)
        if key not in lookup: raise ModelError('缺少配对的无过程波动指标')
        old=lookup[key]
        if row['count']!=old['count']: raise ModelError('过程波动对照划分数量不同')
        changes.append(dict(zip(keys,key),reference_rmse=old['rmse'],process_rmse=row['rmse'],
            rmse_change=row['rmse']-old['rmse'],reference_mae=old['mae'],process_mae=row['mae'],
            mae_change=row['mae']-old['mae'],reference_mean_residual=old['mean_residual'],
            process_mean_residual=row['mean_residual']))
    if len(changes)!=len(lookup): raise ModelError('两组实验指标覆盖范围不一致')
    return changes


def build_report(generation,baseline,comparison,summary,reference_generation,reference_comparison):
    if (generation['source_varying_generation_sha256']!=digest(reference_generation)
        or comparison['generation_sha256']!=digest(generation)
        or comparison['baseline_fit_sha256']!=digest(baseline)
        or summary['comparison_sha256']!=digest(comparison)
        or reference_comparison['generation_sha256']!=digest(reference_generation)
        or summary['status']!='complete'):
        raise ModelError('过程实验或无波动对照版本不一致，或实验未完成')
    config=generation['process_config_snapshot'];source_group=config['source_group'];group=config['group_id']
    changes=paired_process_changes(comparison,reference_comparison,source_group)
    nodes=[n for n in generation['topology']['order'] if n in generation['topology']['receivers']]
    process_rows=generation['process_samples'];stats=[]
    change_summary=[]
    for mode in ('base','fixed_delta','free_delta'):
        for sigma in summary['config_snapshot']['noise_std']:
            for n in nodes:
                for split in ('train','holdout'):
                    for target in ('clean','observed'):
                        for prediction in ('local','recursive'):
                            rows=[r for r in changes if (r['model'],r['noise_sigma'],r['node'],r['split'],r['target'],r['prediction'])
                                  ==(mode,sigma,n,split,target,prediction)]
                            values=[r['rmse_change'] for r in rows]
                            change_summary.append(dict(model=mode,noise_sigma=sigma,node=n,split=split,target=target,prediction=prediction,
                                count=len(rows),mean_rmse_change=statistics.mean(values),sd_rmse_change=statistics.stdev(values) if len(values)>1 else None))
    for n in nodes:
        rows=[r for r in process_rows if r['node']==n]
        for quantity in ('process_noise','total_clean_shift'):
            values=[r[quantity] for r in rows]
            stats.append(dict(node=n,quantity=quantity,count=len(values),mean=statistics.mean(values),sd=statistics.stdev(values),
                minimum=min(values),maximum=max(values),clip_count=sum(r['process_clipped'] for r in rows)))
    process_bounds={(r['repetition'],r['node'],r['split']):r for r in generation['process_structural_by_split']}
    crosschecks=[]
    for job in baseline['jobs']:
        for metric in job['metrics']:
            if metric['target']!='clean' or metric['mode']!='local': continue
            expected=process_bounds[job['repetition'],metric['node'],metric['split']]['clean_local_base_rmse_lower_bound']
            if not math.isclose(metric['rmse_lower_bound'],expected,abs_tol=1e-10): raise ModelError('过程组同划分局部下界核对失败')
            crosschecks.append(dict(node=metric['node'],split=metric['split'],noise_sigma=job['noise_sigma'],
                repetition=job['repetition'],rmse=metric['rmse'],structural_lower_bound=expected))
    clipping=[]
    for sigma in summary['config_snapshot']['noise_std']:
        rows=[r for r in generation['observations'] if r['group']==group and r['noise_sigma']==sigma]
        for n in nodes:
            count=sum(not 0<=r['node_values_clean'][n]+r['observation_noise'][n]<=100 for r in rows)
            clipping.append(dict(noise_sigma=sigma,node=n,count=len(rows),clipped_count=count,
                at_zero_count=sum(r['node_values_observed'][n]==0 for r in rows),
                at_100_count=sum(r['node_values_observed'][n]==100 for r in rows)))
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import rc_context
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    outputs={};modes=[('base','基础式','#367AA8'),('fixed_delta','固定δ','#32986B'),('free_delta','自由δ','#D78C36')]
    def selected(rows,mode,sigma,node='R',prediction='recursive'):
        return [r for r in rows if r['model']==mode and r['noise_sigma']==sigma and r['node']==node
            and r['split']=='holdout' and r['target']=='clean' and r['prediction']==prediction]
    def save(fig,name):
        for ext in ('png','svg'):
            stream=io.BytesIO();fig.savefig(stream,format=ext,dpi=180);outputs[name+'.'+ext]=stream.getvalue()
    with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10}):
        fig=Figure(figsize=(12,5),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,2)
        for ax,sigma in zip(axes,(0,8)):
            for i,(mode,label,color) in enumerate(modes):
                pair=selected(changes,mode,sigma)
                values=[[r[k] for r in pair] for k in ('reference_rmse','process_rmse')]
                ax.bar(np.arange(2)+(i-1)*.24,[statistics.mean(v) for v in values],width=.24,
                    yerr=[statistics.stdev(v) for v in values],color=color,capsize=3,label=label)
            ax.set(xticks=[0,1],xticklabels=['无过程波动','过程波动σ=2'],ylabel='末端R整网验证RMSE（SE点）',title=f'观测噪声σ={sigma}')
            ax.grid(axis='y',alpha=.2);ax.legend(fontsize=8)
        fig.suptitle('相同根节点、条件与划分的过程波动对照（10次重复均值±样本标准差）');save(fig,'process_vs_reference')
        fig=Figure(figsize=(12,7),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(2,len(nodes))
        for j,n in enumerate(nodes):
            for i,key in enumerate(('process_noise','total_clean_shift')):
                ax=axes[i,j];values=[r[key] for r in process_rows if r['node']==n]
                ax.hist(values,bins=26,color='#367AA8' if i==0 else '#D78C36',alpha=.8)
                ax.set(title=n+('：本地过程扰动' if i==0 else '：相对无波动状态总变化'),xlabel='SE分值变化',ylabel='基础网络数')
                ax.axvline(0,color='#777777',linewidth=1)
        fig.suptitle('本地创新与整网生成状态变化不同（每节点2500条，不按观测噪声重复计数）');save(fig,'process_innovation_and_shift')
        fig=Figure(figsize=(12,4.8),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,len(nodes))
        for ax,n in zip(axes,nodes):
            local_old=selected([r for r in reference_comparison['metrics'] if r['group']==source_group],'base',0,n,'local')
            old_bound=[r['clean_local_base_rmse_lower_bound'] for r in reference_generation['structural_by_split'] if r['node']==n and r['split']=='holdout']
            local_new=selected(comparison['metrics'],'base',0,n,'local')
            new_bound=[r['clean_local_base_rmse_lower_bound'] for r in generation['process_structural_by_split'] if r['node']==n and r['split']=='holdout']
            values=[[r['rmse'] for r in local_old],old_bound,[r['rmse'] for r in local_new],new_bound]
            ax.bar(range(4),[statistics.mean(v) for v in values],yerr=[statistics.stdev(v) for v in values],capsize=3,
                color=['#367AA8','#9AA8B6','#D78C36','#C9B394'])
            ax.set(xticks=range(4),xticklabels=['原局部\nRMSE','原下界','过程组\nRMSE','过程组\n下界'],title=n,ylabel='SE点')
            ax.grid(axis='y',alpha=.2)
        fig.suptitle('基础式同划分、实际上游与干净验证目标，观测σ=0（均值±样本标准差）');save(fig,'process_structural_bound')
    free=[r for r in summary['delta_summary'] if r['model']=='free_delta']
    lines=['# 过程波动扩展实验结果解释','',
        '本轮在第10阶段逐接受节点独立变化C/M/E的七节点网络上，另建σ_process=2的实验组。每个接受节点、每个基础网络抽取一次N(0,2²)过程创新。过程随机数与条件、根节点、观测噪声、划分使用独立子流；同一基础网络的四档观测版本共用过程创新和生成状态。',
        '', '节点先根据当前真实上游状态计算任务公式，再加本地过程创新并截断到[0,100]，得到clean状态，随后才形成观测值。这里clean意为“没有观测误差”，不表示“没有过程波动”。过程后的T进入Q、过程后的Q进入R；观测误差不进入下游。',
        '', '10次独立重复，每次250个基础网络，200训练、50验证：新增2500个过程网络、10000个配对观测版本、7500条接受节点过程创新。三模型各40组，共120组、360次节点拟合。旧无过程组保持冻结，固定δ=-5、自由δ范围[-10,10]，未裁剪FDNA预测。',
        '', '| 观测σ | 基础式R | 固定δ R | 自由δ R | 自由δ比同组基础式改善次数 |', '| ---: | ---: | ---: | ---: | ---: |']
    for sigma in summary['config_snapshot']['noise_std']:
        values=[[r['rmse'] for r in selected(comparison['metrics'],m,sigma)] for m,_,_ in modes]
        by_model={m:{r['repetition']:r['rmse'] for r in selected(comparison['metrics'],m,sigma)} for m,_,_ in modes}
        improvement=sum(by_model['base'][rep]>value for rep,value in by_model['free_delta'].items())
        lines.append(f'| {sigma} | '+' | '.join(f'{statistics.mean(v):.3f} ± {statistics.stdev(v):.3f}' for v in values)+f' | {improvement}/{len(values[0])} |')
    lines+=['','“±”为10次独立数据重复的样本标准差，不是置信区间。组内三模型使用同一批过程数据比较；与无过程组对照时，真实中间状态和目标均发生变化，不能称为同一目标上的纯观测噪声实验。',
        '', '| 节点 | 本地创新均值 | 本地创新SD | 总状态变化SD | 过程截断条数 |','| --- | ---: | ---: | ---: | ---: |']
    for n in nodes:
        innovation=next(r for r in stats if r['node']==n and r['quantity']=='process_noise')
        shift=next(r for r in stats if r['node']==n and r['quantity']=='total_clean_shift')
        lines.append(f"| {n} | {innovation['mean']:.3f} | {innovation['sd']:.3f} | {shift['sd']:.3f} | {innovation['clip_count']} |")
    outside=summary['outside_predictions'];counts=Counter((r['model'],r['node'],r['prediction'],r['split']) for r in outside)
    lines+=['',f"自由δ接受节点估计中，{sum(r['lower_bound_count'] for r in free)}/{sum(r['n'] for r in free)}次触及下界，{sum(r['upper_bound_count'] for r in free)}次触及上界，{sum(r['rank_deficient_count'] for r in free)}次局部秩不足。含δ越界预测条目{len(outside)}条，按模型、节点、局部/整网及划分保留原值；不将噪声版本或预测条目当作独立实验。",'',
        '本地过程创新的标准差设为2；下游总状态变化同时包含上游过程状态变化、本地创新、非线性机制和截断效应，因此不要求总变化的标准差也为2，且不能用两种RMSE之差直接量化传播误差。',
        '', '基础式结构下界已按含过程波动的真实上游和实际目标重新计算，并与训练/验证拟合指标逐项核对；不搬用无过程组下界，不将它用于含δ或整网误差。',
        '', '图表：process_vs_reference比较有无过程波动的整网误差；process_innovation_and_shift区分本地扰动与总状态变化；process_structural_bound比较同数据局部误差和下界。comparison目录还提供三模型噪声响应、配对改善及δ分布图。',
        '', 'process_effect_paired.csv按同模型/重复/噪声/节点/划分/目标/预测方式记录过程组与冻结组差异；该差异描述本次过程扩展，不是训练泛化改善的证据。observation_clipping.csv另列观测截断和边界堆积，不能与过程截断混为一谈。',
        '', '本轮仅覆盖变化条件下的σ_process=2，不代表全部固定条件或所有波动强度。交互效应、多输入及11节点18边独立模拟拟合仍待开展。预测改善、参数可辨识性、输出范围继续分别报告。','']
    if outside: lines+= [f"越界范围：{min(r['predicted'] for r in outside):.4f}至{max(r['predicted'] for r in outside):.4f}。",'']
    outputs.update({'结果解释.md':'\n'.join(lines),'process_effect_paired.csv':csv_rows(changes),
        'process_effect_summary.csv':csv_rows(change_summary),
        'process_statistics.csv':csv_rows(stats),'observation_clipping.csv':csv_rows(clipping),
        'structural_crosscheck.csv':csv_rows(crosschecks),
        'outside_counts.csv':csv_rows([dict(model=k[0],node=k[1],prediction=k[2],split=k[3],outside_count=v) for k,v in sorted(counts.items())]),
        '汇报来源核验.json':dumps(dict(generation_sha256=digest(generation),comparison_sha256=digest(comparison),
            reference_generation_sha256=digest(reference_generation),reference_comparison_sha256=digest(reference_comparison),
            paired_metric_rows=len(changes),structural_checks=len(crosschecks)))})
    return outputs


def main():
    p=argparse.ArgumentParser();p.add_argument('--experiment-dir',type=Path,required=True);p.add_argument('--reference-dir',type=Path,required=True)
    a=p.parse_args();root=a.experiment_dir;ref=a.reference_dir
    outputs=build_report(read_json(root/'generation/generation_report.json'),read_json(root/'base/fit_report.json'),
        read_json(root/'comparison/delta_comparison_report.json'),read_json(root/'comparison/delta_repeats_report.json'),
        read_json(ref/'generation/generation_report.json'),read_json(ref/'comparison/delta_comparison_report.json'))
    write_outputs(root/'report',outputs)


if __name__=='__main__':main()
