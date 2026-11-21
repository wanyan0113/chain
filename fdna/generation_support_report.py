"""One-at-a-time complementary support sensitivity with frozen paired controls."""
import argparse
import io
import json
import math
from pathlib import Path

from source_data.common import read_json, write_outputs
from .generation_coefficient_experiment import validate_protocol
from .generation_interaction_sensitivity_report import paired_strength_metrics, verify_sample_pairing
from .generation_delta_repeats import distribution
from .generation_varying_experiment import csv_rows, dumps
from .model import ModelError
from .structure import digest


def build_report(generation, base, comparison, summary, reference, old_base, old_comparison, old_summary, *, condition=False):
    validate_protocol(generation,base['config_snapshot'],comparison['config_snapshot'],reference,old_base,old_comparison)
    for g,b,c,s in ((generation,base,comparison,summary),(reference,old_base,old_comparison,old_summary)):
        if (any(x.get('status')!='complete' for x in (g,b,c,s)) or b['generation_report_sha256']!=digest(g)
                or c['generation_sha256']!=digest(g) or c['baseline_fit_sha256']!=digest(b)
                or s['comparison_sha256']!=digest(c)):
            raise ModelError('互补系数汇总来源版本或完成状态不一致')
    cfg=generation['coefficient_config_snapshot'];groups=cfg['condition_groups' if condition else 'support_groups']
    source=cfg['reference_experiment']['condition_sampling']['group_id'];fc=base['config_snapshot']
    original=(generation['node_configs_snapshot']['nodes']['R']['condition_function'] if condition
        else generation['node_configs_snapshot']['nodes']['T']['support_coefficients'])
    labels={'task_gain':'任务增益','environment_penalty':'环境惩罚'} if condition else {'A':'A','B':'B'}
    pairing=verify_sample_pairing(generation,list(groups),source)
    allmetrics=[r for c in (old_comparison,comparison) for r in c['metrics']]
    factors={};summaries=[];paired=[]
    for factor in labels:
        levels={source:original[factor],**({g:v['value'] for g,v in groups.items() if v['factor']==factor} if condition
            else {g:v[factor] for g,v in groups.items() if v[factor]!=original[factor]})}
        if len(levels)!=3:raise ModelError('每个互补系数须有预设高低两组及冻结基线')
        factors[factor]=levels
        s,p=paired_strength_metrics([r for r in allmetrics if r['group'] in levels],levels,
            fc['noise_std'],fc['repetitions'],['T','Q','R'],source)
        for row in s+p:row.update(factor=factor,coefficient=row.pop('gamma'))
        summaries.extend(s);paired.extend(p)
    bounds={(r['group'],r['repetition'],r['node'],r['split']):r for r in generation['coefficient_structural_by_split']}
    checked=[];branches=[]
    for job in base['jobs']:
        for m in job['metrics']:
            if m['target']!='clean' or m['mode']!='local':continue
            bound=bounds[job['group'],job['repetition'],m['node'],m['split']]['clean_local_base_rmse_lower_bound']
            if not math.isclose(bound,m['rmse_lower_bound'],rel_tol=0,abs_tol=1e-10):raise ModelError('同数据局部结构下界不符')
            checked.append(dict(group=job['group'],repetition=job['repetition'],noise_sigma=job['noise_sigma'],**m))
    fits=[('base',base),('base',old_base)]+[(m,c['fits'][m]) for c in (comparison,old_comparison) for m in ('fixed_delta','free_delta')]
    unchanged_checks=0
    if condition:
        for mode,new,old in [('base',base,old_base)]+[(m,comparison['fits'][m],old_comparison['fits'][m]) for m in ('fixed_delta','free_delta')]:
            lookup={(j['noise_sigma'],j['repetition']):j for j in old['jobs'] if j['group']==source}
            for job in new['jobs']:
                previous=lookup[job['noise_sigma'],job['repetition']]
                for n in ('T','Q'):
                    a=next(r for r in job['local_fits'] if r['receiver']==n)['fit']['receivers'][0]
                    b=next(r for r in previous['local_fits'] if r['receiver']==n)['fit']['receivers'][0]
                    if a['parameters']!=b['parameters'] or a['delta']!=b['delta']:raise ModelError('未改变的T/Q参数出现漂移')
                    for m in job['metrics']:
                        if m['node']!=n:continue
                        before=next(r for r in previous['metrics'] if all(r[k]==m[k] for k in ('node','split','target','mode')))
                        if any(not math.isclose(m[k],before[k],abs_tol=1e-12,rel_tol=0) for k in ('rmse','mae','mean_residual')):
                            raise ModelError('未改变的T/Q指标出现漂移')
                        unchanged_checks+=1
    for mode,fit in fits:
        for job in fit['jobs']:
            if job['group'] not in set(groups)|{source}:continue
            for local in job['local_fits']:
                r=local['fit']['receivers'][0];s=r['sensitivity']
                branches.append(dict(model=mode,group=job['group'],noise_sigma=job['noise_sigma'],repetition=job['repetition'],
                    node=local['receiver'],local_rank=s['local_jacobian_rank'],parameter_count=s['parameter_count'],
                    inactive_parameters=','.join(s['inactive_parameters']),sod_rows=s['sod_active_rows'],cod_rows=dumps(s['cod_active_rows']),
                    excluded_tie_rows=s['excluded_tie_rows'],near_best_start_count=r['near_best_start_count'],
                    near_best_parameter_spread=dumps(r['near_best_parameter_spread'])))
    deltas=[r for s in (old_summary,summary) for r in s['delta_summary'] if r['group'] in set(groups)|{source}]
    outside=[r for s in (old_summary,summary) for r in s['outside_predictions'] if r['group'] in set(groups)|{source}]
    shifts=[]
    for group in groups:
        for n in ('T','Q','R'):
            for rep in fc['repetitions']:
                rows=[r for r in generation['coefficient_shifts'] if r['group']==group and r['node']==n and r['repetition']==rep and r['split']=='holdout']
                shifts.append(dict(group=group,node=n,repetition=rep,count=len(rows),mean_shift=math.fsum(r['shift'] for r in rows)/len(rows)))
    def find(factor,model,group,sigma,node='R',prediction='recursive',quantity='rmse'):
        return next(r for r in summaries if (r['factor'],r['model'],r['group'],r['noise_sigma'],r['node'],r['split'],r['target'],r['prediction'],r['quantity'])
            ==(factor,model,group,sigma,node,'holdout','clean',prediction,quantity))
    def show(r):return f"{r['mean']:.3f}"+(f" ± {r['sd']:.3f}" if r['sd'] is not None else '（单次，无SD）')
    display_noises=list(dict.fromkeys((fc['noise_std'][0],fc['noise_std'][-1])))
    repeats=len(fc['repetitions']);jobs=3*len(groups)*len(fc['noise_std'])*repeats
    experiment=cfg['reference_experiment']['base_experiment']
    title='末端条件函数系数敏感性' if condition else '互补支持系数敏感性'
    intro=('采用逐一扰动（OAT）：R任务项为0.90+0.10M、冻结0.80+0.20M、0.70+0.30M；环境项为1−0.30E、冻结1−0.40E、1−0.50E。每次只改变一个因素；任务基础项联动为1−增益，并非第二个独立变量。±0.10为预设绝对扰动，不依据验证误差选系数。' if condition else
        '采用逐一扰动（OAT）：A=0.80/0.90/1.00（B固定0.78）；B=0.68/0.78/0.88（A固定0.90）。两个支持系数不要求和为1。A=1.00是系数合法区间的端点，不是输入SE恒为100。±0.10是预设绝对扰动，不据验证误差选系数。')
    propagation=('所有组共用根输入、逐接受节点C/M/E、划分和观测创新；过程波动与交互关闭。只改R条件函数的生成系数，不改C/M/E样本分布；T/Q输入、输出和拟合参数应不变。条件函数系数敏感性与先前C/M/E取值随机变化实验不是同一实验。在本次基础项联动的参数化下，M≤1时增益越大，任务乘数反而越低；环境惩罚越大，环境乘数越低。' if condition else
        '所有组共用根输入、逐接受节点C/M/E、划分和观测创新；过程波动与交互关闭。只改T的一个支持系数，T干净输出传给Q，Q干净输出传给R。Q/R生成配置不变，但其输入和实际目标会变化，不能要求它们的拟合参数不变。')
    remaining=('本批只完成R任务增益与环境惩罚系数的预设逐一扰动。T/Q条件函数系数及自身能力项尚未全部覆盖，不代表所有生成系数系统敏感性完成。正式多输入拟合和11节点18边扩展尚待开展。' if condition else
        '本批检验预设范围内的两个互补系数，未检验A/B联合改变或相互作用。已完成的R贡献权重敏感性仍见第15包；条件函数系数、多输入正式拟合和11节点18边扩展尚待开展。')
    lines=['# '+title+'结果','',intro,
        '',f"每组{repeats}次数据重复，每次{experiment['base_cases']}个基础网络，{experiment['train_count']}训练、{experiment['base_cases']-experiment['train_count']}验证，观测噪声{fc['noise_std']}。新增{jobs}组模型整网计算、{jobs*3}次接受节点拟合，冻结基线不重拟合。",
        '',propagation,
        '',f"FDNA仅读取上游SE，生成系数不是α、β真值。基础式、固定δ=-5及自由δ[-10,10]沿用来源的{fc['settings']['starts']}个起点和全部求解设置。预测不裁剪。",
        '', '| 扰动系数 | 值 | 观测σ | 基础式R | 固定δ R | 自由δ R |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for f,levels in factors.items():
        for g in sorted(levels,key=levels.get):
            for sigma in display_noises:
                lines.append(f'| {labels[f]} | {levels[g]:.2f} | {sigma} | '+' | '.join(show(find(f,m,g,sigma)) for m in ('base','fixed_delta','free_delta'))+' |')
    lines+=['',f'上表是末端R对干净验证目标的整网RMSE，±为{repeats}次重复的样本标准差，不是置信区间。两张因素表引用同一冻结基线，不能将基线重复计为独立实验。跨系数时T/Q/R目标都可能变化，RMSE差不是单独的因果传播误差。',
        '', '| 组 | 自由δ触下界/节点估计数 | 自由δ局部秩不足 | 含δ越界预测条目 |','| --- | ---: | ---: | ---: |']
    for g in [source,*groups]:
        rows=[r for r in deltas if r['group']==g and r['model']=='free_delta']
        lines.append(f"| {g} | {sum(r['lower_bound_count'] for r in rows)}/{sum(r['n'] for r in rows)} | {sum(r['rank_deficient_count'] for r in rows)} | {sum(r['group']==g for r in outside)} |")
    mean_comparisons=[]
    for f,levels in factors.items():
        for g in levels:
            if g==source:continue
            for sigma in fc['noise_std']:
                mean_comparisons.append({m:find(f,m,g,sigma)['mean'] for m in ('base','fixed_delta','free_delta')})
    lines+=['',f"在{len(mean_comparisons)}个新系数组×噪声配置中，固定δ、自由δ的R整网验证RMSE重复均值低于基础式的配置数分别为"
        f"{sum(r['fixed_delta']<r['base'] for r in mean_comparisons)}和{sum(r['free_delta']<r['base'] for r in mean_comparisons)}。"
        '这是同一批配对数据的配置层描述，不是独立实验次数或统计显著性结论；具体逐重复变化见CSV。']
    lines+=['','节点估计及越界预测条目包含配对噪声、模型、节点及局部/整网等维度，不是独立推演数。δ改善预测不证明唯一识别，触界不代表真值位于边界，局部秩不证明全局不唯一。',
        '',f'{pairing}个新组观测版本配对通过，{len(checked)}项同数据基础式局部结构下界核对通过。下界只用于实际上游输入、同目标同划分的基础式局部预测，不直接用于含δ或整网误差。',
        '',remaining,'']
    if condition:lines+=['',f'未改变的T/Q在三模型上的参数及{unchanged_checks}项误差指标与冻结基线一致。','']
    if condition:lines=[line.replace('跨系数时T/Q/R目标都可能变化','跨系数时R目标变化，T/Q目标不变') for line in lines]
    outputs={'结果解释.md':'\n'.join(lines),'support_metrics.csv':csv_rows(summaries),'paired_support_changes.csv':csv_rows(paired),
        'generation_shifts.csv':csv_rows(generation['coefficient_shifts']),'generation_repeat_shifts.csv':csv_rows(shifts),
        'structural_crosscheck.csv':csv_rows(checked),'rank_and_branches.csv':csv_rows(branches),
        'all_support_delta.csv':csv_rows(deltas),'all_support_outside_predictions.csv':csv_rows(outside),
        '汇报来源核验.json':dumps(dict(status='complete',generation_sha256=digest(generation),comparison_sha256=digest(comparison),
            reference_comparison_sha256=digest(old_comparison),paired_observation_checks=pairing,structural_checks=len(checked),
            new_model_jobs=jobs,new_receiver_fits=jobs*3,baseline_refitted=False))}
    if condition:
        meta=json.loads(outputs['汇报来源核验.json'])
        meta['unchanged_node_metric_checks']=unchanged_checks
        outputs['汇报来源核验.json']=dumps(meta)
        outputs={k.replace('support','condition'):v for k,v in outputs.items()}
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import rc_context
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    def save(fig,name):
        for ext in ('png','svg'):
            stream=io.BytesIO();fig.savefig(stream,format=ext,dpi=180);outputs[name+'.'+ext]=stream.getvalue()
    modes=[('base','基础式','#367AA8'),('fixed_delta','固定δ','#32986B'),('free_delta','自由δ','#D78C36')]
    with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10}):
        for quantity,name,label in [('rmse','support_rmse','整网验证RMSE（SE点）'),('mean_residual','support_residual','平均残差：预测−目标（SE点）')]:
            fig=Figure(figsize=(6*len(display_noises),8.4),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(2,len(display_noises),squeeze=False)
            for i,(f,levels) in enumerate(factors.items()):
                ordered=sorted(levels,key=levels.get)
                for ax,sigma in zip(axes[i],display_noises):
                    for model,title,color in modes:
                        rows=[find(f,model,g,sigma,quantity=quantity) for g in ordered]
                        ax.errorbar([levels[g] for g in ordered],[r['mean'] for r in rows],yerr=[r['sd'] or 0 for r in rows],marker='o',capsize=3,label=title,color=color)
                    ax.set(title=f'仅改变{labels[f]}，R节点，观测σ={sigma}',xlabel=(f'R的{labels[f]}系数' if condition else f'{f}对T的互补支持系数'),ylabel=label,xticks=[levels[g] for g in ordered]);ax.grid(alpha=.2)
            fig.legend(*axes[0,0].get_legend_handles_labels(),loc='outside lower center',ncol=3)
            fig.suptitle(f'{title}（{repeats}次重复，误差棒为样本标准差，单次无SD）');save(fig,name.replace('support','condition') if condition else name)
        fig=Figure(figsize=(13,7.5),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(2,3)
        for i,(f,levels) in enumerate(factors.items()):
            ordered=sorted(levels,key=levels.get)
            for ax,n in zip(axes[i],('T','Q','R')):
                rows=[find(f,'base',g,fc['noise_std'][0],n,'local') for g in ordered]
                bound_rows=reference['structural_by_split']+generation['coefficient_structural_by_split']
                values=[distribution([r['clean_local_base_rmse_lower_bound'] for r in bound_rows if r['group']==g and r['node']==n and r['split']=='holdout']) for g in ordered]
                for data,title,color in [(rows,'基础式局部RMSE','#367AA8'),(values,'同数据结构下界','#8999AA')]:
                    ax.errorbar([levels[g] for g in ordered],[r['mean'] for r in data],yerr=[r['sd'] or 0 for r in data],marker='o',capsize=3,label=title,color=color)
                ax.set(title=f'仅改变{labels[f]} / {n}',xlabel=f'{labels[f]}系数',ylabel='SE点',xticks=[levels[g] for g in ordered]);ax.grid(alpha=.2);ax.legend(fontsize=8)
        fig.suptitle(f"基础式局部误差及结构下界（观测σ={fc['noise_std'][0]}，均值±样本标准差，单次无SD）");save(fig,'condition_bound' if condition else 'support_bound')
    return outputs


def main():
    p=argparse.ArgumentParser()
    for flag in ('experiment-dir','reference-dir'):p.add_argument('--'+flag,type=Path,required=True)
    a=p.parse_args()
    def load(d):return [read_json(d/f) for f in ('generation/generation_report.json','base/fit_report.json',
        'comparison/delta_comparison_report.json','comparison/delta_repeats_report.json')]
    write_outputs(a.experiment_dir/'report',build_report(*load(a.experiment_dir),*load(a.reference_dir)))


if __name__=='__main__':main()
