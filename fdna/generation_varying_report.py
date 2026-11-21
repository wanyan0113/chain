"""Export reproducible, descriptive figures for varying external conditions."""
import argparse
import io
import json
import math
from pathlib import Path
import statistics

from source_data.common import read_json, write_outputs
from .generation_varying_experiment import csv_rows, dumps
from .model import ModelError
from .structure import digest


def build_report(generation, baseline, summary, reference):
    if baseline['generation_report_sha256']!=digest(generation) or summary['status']!='complete':
        raise ModelError('汇报数据版本不一致或实验未完成')
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import rc_context
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    outputs={}; nodes=generation['topology']['receivers']; group=generation['varying_config_snapshot']['condition_sampling']['group_id']
    sampled=generation['condition_samples']; structural=generation['structural_by_split']
    empirical=[]
    for n in nodes:
        rows=[r for r in sampled if r['node']==n]
        for key in ('C','M','E'):
            values=[r[key] for r in rows]
            empirical.append(dict(node=n,condition=key,count=len(values),minimum=min(values),maximum=max(values),
                mean=statistics.mean(values),sd=statistics.stdev(values),
                **dict(zip(('p5','p50','p95'),map(float,np.percentile(values,[5,50,95]))))))
    correlations=[]
    labels=[n+':'+k for n in nodes for k in ('C','M','E')]
    keyed={(r['base_case_id'],r['node']):r for r in sampled}
    matrix=np.array([[keyed[r['base_case_id'],n][k] for n in nodes for k in ('C','M','E')] for r in generation['root_inputs']])
    corr=np.corrcoef(matrix,rowvar=False)
    for i,a in enumerate(labels):
        for j,b in enumerate(labels):
            if j>i: correlations.append(dict(first=a,second=b,pearson_r=float(corr[i,j])))
    checked=[]
    bounds={(r['repetition'],r['node'],r['split']):r for r in structural}
    for job in baseline['jobs']:
        for metric in job['metrics']:
            if metric['target']!='clean' or metric['mode']!='local': continue
            expected=bounds[job['repetition'],metric['node'],metric['split']]['clean_local_base_rmse_lower_bound']
            if not math.isclose(metric['rmse_lower_bound'],expected,abs_tol=1e-10):
                raise ModelError('生成端下界与同输入同划分拟合端不一致')
            checked.append(dict(repetition=job['repetition'],noise_sigma=job['noise_sigma'],node=metric['node'],
                split=metric['split'],local_rmse=metric['rmse'],structural_lower_bound=expected))
    # Equal-width bins are fixed from the registered ranges, not chosen by fit quality.
    ranges=generation['varying_config_snapshot']['condition_sampling']['ranges']
    clean_runs=[r for r in generation['clean_runs'] if r['group']==group]
    point_lookup={(j['repetition'],p['base_case_id'],p['node']):p for j in baseline['jobs']
        if j['noise_sigma']==0 for p in j['points']}
    strata=[]
    for n in nodes:
        for key,(lo,hi) in ranges.items():
            for bin_index in range(3):
                lower=lo+(hi-lo)*bin_index/3; upper=lo+(hi-lo)*(bin_index+1)/3
                for split in ('train','holdout'):
                    rows=[r for r in clean_runs if r['split']==split and lower<=r['conditions_by_node'][n][key]
                          and (r['conditions_by_node'][n][key]<upper or bin_index==2 and r['conditions_by_node'][n][key]<=upper)]
                    deficit=[max(0,min(r['node_details'][n]['parent_clean'].values())-r['node_values_clean'][n]) for r in rows]
                    residual=[point_lookup[r['repetition'],r['base_case_id'],n]['local_prediction']-r['node_values_clean'][n] for r in rows]
                    strata.append(dict(node=n,condition=key,bin_index=bin_index,lower=lower,upper=upper,split=split,count=len(rows),
                        below_min_parent_fraction=sum(d>1e-10 for d in deficit)/len(rows) if rows else None,
                        structural_lower_bound=math.sqrt(statistics.mean(d*d for d in deficit)) if rows else None,
                        base_local_clean_rmse_sigma0=math.sqrt(statistics.mean(d*d for d in residual)) if rows else None))
    def save(fig,name):
        for ext in ('png','svg'):
            stream=io.BytesIO(); fig.savefig(stream,format=ext,dpi=180); outputs[name+'.'+ext]=stream.getvalue()
    def metric(table,model,g,sigma,node='R'):
        return next(r for r in table['metrics'] if r['group']==g and r['model']==model and r['noise_sigma']==sigma
            and r['node']==node and r['split']=='holdout' and r['target']=='clean' and r['prediction']=='recursive' and r['quantity']=='rmse')
    modes=[('base','基础式','#367AA8'),('fixed_delta','固定δ=-5','#32986B'),('free_delta','自由δ','#D78C36')]
    with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10}):
        fig=Figure(figsize=(12,5),layout='constrained'); FigureCanvasAgg(fig); axes=fig.subplots(1,2)
        groups=[(reference,'unit_conditions','不额外衰减'),(reference,'fixed_conditions','固定条件'),(summary,group,'变化条件')]
        # The frozen group IDs are validated by metric lookup; never silently substitute missing rows.
        for ax,sigma in zip(axes,(0,8)):
            for i,(mode,label,color) in enumerate(modes):
                rows=[metric(t,mode,g,sigma) for t,g,_ in groups]
                ax.bar(np.arange(3)+(i-1)*.24,[r['mean'] for r in rows],width=.24,
                    yerr=[r['sd'] for r in rows],capsize=3,label=label,color=color)
            ax.set(xticks=range(3),xticklabels=[x[2] for x in groups],ylabel='末端R整网验证RMSE（SE点）',title=f'观测噪声σ={sigma}')
            ax.legend(fontsize=8); ax.grid(axis='y',alpha=.2)
        fig.suptitle('冻结对照与变化条件：10次重复均值 ± 样本标准差'); save(fig,'conditions_comparison')
        fig=Figure(figsize=(12,4.8),layout='constrained'); FigureCanvasAgg(fig); axes=fig.subplots(1,len(nodes),squeeze=False)[0]
        for ax,n in zip(axes,nodes):
            rows=[r for r in structural if r['node']==n and r['split']=='holdout']
            actual=[r['local_rmse'] for r in checked if r['node']==n and r['split']=='holdout' and r['noise_sigma']==0]
            lower=[r['clean_local_base_rmse_lower_bound'] for r in rows]
            ax.bar([0,1],[statistics.mean(actual),statistics.mean(lower)],yerr=[statistics.stdev(actual),statistics.stdev(lower)],capsize=4,color=['#367AA8','#9AA8B6'])
            ax.set(xticks=[0,1],xticklabels=['基础式局部RMSE','结构下界'],title=n,ylabel='SE点')
            ax.grid(axis='y',alpha=.2)
        fig.suptitle('同一干净验证目标及真实上游输入，σ=0（均值±样本标准差）'); save(fig,'structural_bound')
        fig=Figure(figsize=(12,8),layout='constrained'); FigureCanvasAgg(fig); axes=fig.subplots(len(nodes),3)
        for i,n in enumerate(nodes):
            for j,k in enumerate(('C','M','E')):
                ax=axes[i,j]; ax.hist([r[k] for r in sampled if r['node']==n],bins=18,color='#367AA8',alpha=.8)
                ax.set(title=n+' / '+k,xlabel='抽样值',ylabel='基础样本数')
        fig.suptitle('逐接受节点独立条件抽样（每节点2500组；不按噪声重复计数）'); save(fig,'condition_distributions')
    lines=['# 变化外部条件实验：结果解释','',
        '本轮采用第四次修订补充版的逐接受节点独立抽样：C∼U(0.4,1)、M∼U(0,1)、E∼U(0,0.8)。C/M/E及不同接受节点使用独立随机子流；有限样本的经验相关不要求恰好为0。',
        '', '10次独立数据重复，每次250个基础网络（200训练、50验证）。新增2500个干净变化条件网络、10000个配对噪声观测版本；7500组接受节点条件只抽取一次，四档噪声共用。旧两组条件的数据逐项核对不变，旧拟合结果仅引用，不重新拟合。',
        '', '新增基础式、固定δ、自由δ各40组整网计算，共120组、360次接受节点拟合。固定δ=-5，自由δ界限[-10,10]；每节点/条件组/噪声/重复估计一个常数，不逐样本估计。C/M/E仅进入生成器，FDNA仅读取上游SE。过程波动和交互仍关闭。',
        '', '| σ | 基础式R | 固定δ R | 自由δ R | 自由δ误差降低次数 |', '| ---: | ---: | ---: | ---: | ---: |']
    for sigma in summary['config_snapshot']['noise_std']:
        rows=[metric(summary,m,group,sigma) for m,_,_ in modes]
        change=next(r for r in summary['paired_model_changes'] if r['model']=='free_delta' and r['noise_sigma']==sigma
            and r['node']=='R' and r['split']=='holdout' and r['target']=='clean' and r['prediction']=='recursive' and r['quantity']=='rmse')
        lines.append(f"| {sigma} | "+' | '.join(f"{r['mean']:.3f} ± {r['sd']:.3f}" for r in rows)+f" | {change['decreased_count']}/{change['n']} |")
    free=[r for r in summary['delta_summary'] if r['model']=='free_delta']
    lines+=['',f"自由δ有{sum(r['lower_bound_count'] for r in free)}/{sum(r['n'] for r in free)}次接受节点估计触及下界、{sum(r['upper_bound_count'] for r in free)}次触及上界；{sum(r['rank_deficient_count'] for r in free)}次局部秩不足。含δ模型越界预测条目共{len(summary['outside_predictions'])}条（局部与整网分别列出，不视作独立观测）。",'',
        '## 下界及解释边界','',
        'structural_by_split.csv按重复、接受节点及训练/验证分别列出干净目标低于最低真实供给SE的比例与基础式局部RMSE下界；本轮逐项对照拟合端同输入同目标下界通过。下界不是最优可达误差，也不用于含δ或整网递推。','',
        'δ带来的误差下降不代表参数唯一可辨识；触界不证明真实δ等于界限，也不据验证结果扩大范围。不同条件同时变化，因此不能仅凭本实验将误差归因于C、M或E中的某一个因素。局部与整网误差之差不直接等于传播误差，分解保留交叉项。预测保持未裁剪。','',
        '变化条件组与原固定组同时存在条件均值和离散程度的差异，组间误差差异不能单独解释为“条件波动造成的影响”。condition_strata.csv按预设范围等宽三分箱，描述每个条件区间的低于最低上游比例、结构下界和基础式局部误差；这些是当前联合抽样下的描述性结果，不是控制其他因素的单因素实验。','',
        '## 图表怎么使用','',
        '- conditions_comparison：与冻结的旧两组结果比较，误差棒为重复间样本标准差，不是置信区间。',
        '- delta_repeat_rmse、paired_delta_change、delta_estimates：各节点的噪声响应、同次配对改善及δ触界情况。',
        '- structural_bound：基础式局部误差与同划分结构下界，不能拿它解释整网全部误差。',
        '- condition_distributions、condition_statistics.csv、condition_correlations.csv：抽样范围、分布及经验相关检查。',
        '', '本轮完成变化条件的首轮联合实验。尚未完成过程波动σ=2、交互γ=0.15、输入测量误差及11节点18边独立生成/拟合；不在本轮增加新的生成机制。','']
    parameters=[p for j in baseline['jobs'] for p in j['parameters']]
    if parameters and all(p['alpha']==1 and p['beta']==0 for p in parameters):
        lines+=['## 为什么基础式噪声曲线重合','',
            '本轮40组基础拟合的全部入边参数均取到α=1、β=0，基础式因此输出最低上游SE。四档噪声使用相同干净输入和相同干净验证目标，所以对干净目标的预测误差相同；接受节点观测目标确实按各档σ加噪，观测目标指标仍分别保存。这说明当前拟合受边界与模型结构限制，不说明噪声对FDNA一般没有影响，也不证明边界参数是唯一真实依赖强度。','']
    outside=summary['outside_predictions']
    if outside:
        lines += [f"本轮越界预测数值范围为{min(r['predicted'] for r in outside):.4f}至{max(r['predicted'] for r in outside):.4f}。",'']
    outputs.update({'结果解释.md':'\n'.join(lines),'condition_statistics.csv':csv_rows(empirical),
        'condition_correlations.csv':csv_rows(correlations),'condition_strata.csv':csv_rows(strata),'structural_fit_crosscheck.csv':csv_rows(checked),
        '汇报来源核验.json':dumps(dict(generation_sha256=digest(generation),baseline_sha256=digest(baseline),
            summary_sha256=digest(summary),frozen_reference_summary_sha256=digest(reference),structural_checks=len(checked)))})
    return outputs


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--experiment-dir',type=Path,required=True); p.add_argument('--reference-summary',type=Path,required=True)
    p.add_argument('--output-dir',type=Path)
    a=p.parse_args(); root=a.experiment_dir
    outputs=build_report(read_json(root/'generation/generation_report.json'),read_json(root/'base/fit_report.json'),
        read_json(root/'comparison/delta_repeats_report.json'),read_json(a.reference_summary))
    write_outputs(a.output_dir or root/'report',outputs)


if __name__=='__main__': main()
