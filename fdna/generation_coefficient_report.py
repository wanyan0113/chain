"""Compare terminal weights using paired frozen samples and unchanged controls."""
import argparse
import io
import math
from pathlib import Path

from source_data.common import read_json, write_outputs
from .generation_coefficient_experiment import validate_protocol
from .generation_interaction_sensitivity_report import paired_strength_metrics, verify_sample_pairing
from .generation_varying_experiment import csv_rows, dumps
from .model import ModelError
from .structure import digest


def build_report(generation, base, comparison, summary, reference, old_base, old_comparison, old_summary):
    validate_protocol(generation,base['config_snapshot'],comparison['config_snapshot'],reference,old_base,old_comparison)
    for g,b,c,s in ((generation,base,comparison,summary),(reference,old_base,old_comparison,old_summary)):
        if (any(x.get('status')!='complete' for x in (b,c,s)) or b['generation_report_sha256']!=digest(g)
                or c['generation_sha256']!=digest(g) or c['baseline_fit_sha256']!=digest(b)
                or s['comparison_sha256']!=digest(c)):
            raise ModelError('系数汇总来源版本或状态不一致')
    cfg=generation['coefficient_config_snapshot'];source=cfg['reference_experiment']['condition_sampling']['group_id']
    levels={source:generation['node_configs_snapshot']['nodes']['R']['weights']['Q'],
        **{g:w['Q'] for g,w in cfg['weight_groups'].items()}}
    ordered=sorted(levels,key=levels.get);paired_count=verify_sample_pairing(generation,list(cfg['weight_groups']),source)
    metrics=[r for c in (old_comparison,comparison) for r in c['metrics'] if r['group'] in levels]
    fc=base['config_snapshot']
    experiment=cfg['reference_experiment']['base_experiment']
    repeats=len(fc['repetitions']);jobs=3*len(cfg['weight_groups'])*len(fc['noise_std'])*repeats
    summaries,paired=paired_strength_metrics(metrics,levels,fc['noise_std'],fc['repetitions'],['T','Q','R'],source)
    # Reuse the checked generic pairing calculation; expose a coefficient name, never gamma.
    for r in summaries+paired:r['q_weight']=r.pop('gamma')
    unchanged=0
    for mode,new,old in [('base',base,old_base)]+[(m,comparison['fits'][m],old_comparison['fits'][m]) for m in ('fixed_delta','free_delta')]:
        lookup={(j['noise_sigma'],j['repetition']):j for j in old['jobs'] if j['group']==source}
        for job in new['jobs']:
            oldjob=lookup[job['noise_sigma'],job['repetition']]
            for node in ('T','Q'):
                a=next(v for v in job['local_fits'] if v['receiver']==node)['fit']['receivers'][0]
                b=next(v for v in oldjob['local_fits'] if v['receiver']==node)['fit']['receivers'][0]
                if a['parameters']!=b['parameters'] or a['delta']!=b['delta']:
                    raise ModelError('未变化节点的拟合参数意外改变')
                for metric in job['metrics']:
                    if metric['node']!=node:continue
                    oldmetric=next(v for v in oldjob['metrics'] if all(v[k]==metric[k] for k in ('node','split','target','mode')))
                    if any(not math.isclose(metric[k],oldmetric[k],abs_tol=1e-12,rel_tol=0) for k in ('rmse','mae','mean_residual')):
                        raise ModelError('未变化节点的预测指标意外改变')
                    unchanged+=1
    bounds={(r['group'],r['repetition'],r['node'],r['split']):r for r in generation['coefficient_structural_by_split']}
    checked=[]
    for job in base['jobs']:
        for m in job['metrics']:
            if m['target']!='clean' or m['mode']!='local':continue
            bound=bounds[job['group'],job['repetition'],m['node'],m['split']]['clean_local_base_rmse_lower_bound']
            if not math.isclose(bound,m['rmse_lower_bound'],abs_tol=1e-10):raise ModelError('同数据结构下界不符')
            checked.append(dict(group=job['group'],repetition=job['repetition'],noise_sigma=job['noise_sigma'],**m))
    delta=[r for s in (old_summary,summary) for r in s['delta_summary'] if r['group'] in levels]
    outside=[r for s in (old_summary,summary) for r in s['outside_predictions'] if r['group'] in levels]
    def find(mode,group,sigma,node='R',prediction='recursive',quantity='rmse'):
        return next(r for r in summaries if (r['model'],r['group'],r['noise_sigma'],r['node'],r['split'],r['target'],r['prediction'],r['quantity'])
            ==(mode,group,sigma,node,'holdout','clean',prediction,quantity))
    lines=['# 末端贡献权重敏感性：首批结果','',
        '本批只改变R的Q/D_aux权重对：0.52/0.48、冻结基线0.62/0.38、0.72/0.28。两权重和为1，属于一个自由度；±0.10是预设绝对扰动，不是置信范围或经验证集筛选的最优权重。',
        '', f"复用变化条件组根节点、逐节点C/M/E、划分和观测噪声创新。过程波动与交互关闭，原组不重拟合。每个权重组{repeats}次独立数据重复，每次{experiment['base_cases']}个基础网络（{experiment['train_count']}训练、{experiment['base_cases']-experiment['train_count']}验证），{len(fc['noise_std'])}档观测噪声为配对版本。新增{jobs}组模型整网计算、{jobs*3}次接受节点拟合。",
        '', f"基础式δ=0，固定δ及自由δ界限见实验预先约定；{fc['settings']['starts']}个起点及全部求解设置保持一致。FDNA只读上游SE，生成权重不是α、β真值。",
        '', '| Q权重 | D_aux权重 | 观测σ | 基础式R | 固定δ R | 自由δ R |','| ---: | ---: | ---: | ---: | ---: | ---: |']
    display_noises=list(dict.fromkeys((fc['noise_std'][0],fc['noise_std'][-1])))
    def show(r):return f"{r['mean']:.3f}"+(f" ± {r['sd']:.3f}" if r['sd'] is not None else '（单次，无SD）')
    for g in ordered:
        for sigma in display_noises:
            lines.append(f'| {levels[g]:.2f} | {1-levels[g]:.2f} | {sigma} | '+' | '.join(
                show(find(m,g,sigma)) for m in ('base','fixed_delta','free_delta'))+' |')
    lines+=['',f'上表为R对干净验证目标的整网RMSE，±为{repeats}次重复的样本标准差，不是置信区间；单次重复不报告SD。跨权重时R目标发生变化；误差差值用于敏感性描述，不是对真实依赖强度的因果估计。',
        '', '| Q权重 | 自由δ触下界/节点估计数 | 自由δ局部秩不足 | 含δ越界预测条目 |','| ---: | ---: | ---: | ---: |']
    for g in ordered:
        rows=[r for r in delta if r['group']==g and r['model']=='free_delta']
        lines.append(f"| {levels[g]:.2f} | {sum(r['lower_bound_count'] for r in rows)}/{sum(r['n'] for r in rows)} | {sum(r['rank_deficient_count'] for r in rows)} | {sum(r['group']==g for r in outside)} |")
    lines+=['','节点估计数包括节点与配对噪声版本，越界条目还区分局部/整网及划分，均不能当成独立试验次数。预测不裁剪。预测改善不代表参数唯一；触界不代表真值位于边界。',
        '',f'未变化的T、Q在三模型上的参数与{unchanged}项预测指标和冻结结果一致。新组{paired_count}个观测版本配对通过，{len(checked)}项同数据基础式局部结构下界核对通过。',
        '', '这批只完成归一化末端权重对敏感性。0.90/0.78互补系数及条件函数系数尚待分批实验，多输入和11节点18边也尚未完成；不能称为系统敏感性全部完成。','']
    outputs={'结果解释.md':'\n'.join(lines),'coefficient_metrics.csv':csv_rows(summaries),'paired_weight_changes.csv':csv_rows(paired),
        'generation_shifts.csv':csv_rows(generation['coefficient_shifts']),'structural_crosscheck.csv':csv_rows(checked),
        'all_weights_delta.csv':csv_rows(delta),'all_weights_outside_predictions.csv':csv_rows(outside),
        '汇报来源核验.json':dumps(dict(status='complete',generation_sha256=digest(generation),comparison_sha256=digest(comparison),
            reference_comparison_sha256=digest(old_comparison),paired_observation_checks=paired_count,
            unchanged_node_metric_checks=unchanged,structural_checks=len(checked)))}
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import rc_context
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    modes=[('base','基础式','#367AA8'),('fixed_delta','固定δ','#32986B'),('free_delta','自由δ','#D78C36')]
    with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10}):
        for quantity,name,label in [('rmse','weight_rmse','整网验证RMSE（SE点）'),('mean_residual','weight_residual','平均残差：预测−目标（SE点）')]:
            fig=Figure(figsize=(5.5*len(display_noises),4.5),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,len(display_noises),squeeze=False)[0]
            for ax,sigma in zip(axes,display_noises):
                for mode,title,color in modes:
                    rows=[find(mode,g,sigma,quantity=quantity) for g in ordered]
                    ax.errorbar([levels[g] for g in ordered],[r['mean'] for r in rows],yerr=[r['sd'] or 0 for r in rows],marker='o',capsize=3,label=title,color=color)
                ax.set(title=f'R节点，观测σ={sigma}',xlabel='Q对R的生成权重（D_aux权重=1−Q权重）',ylabel=label,xticks=[levels[g] for g in ordered]);ax.grid(alpha=.2);ax.legend()
            fig.suptitle(f'末端权重敏感性（干净验证目标，{repeats}次重复；误差棒为样本标准差，单次无SD）')
            for ext in ('png','svg'):
                stream=io.BytesIO();fig.savefig(stream,format=ext,dpi=180);outputs[name+'.'+ext]=stream.getvalue()
    return outputs


def main():
    p=argparse.ArgumentParser()
    for flag in ('experiment-dir','reference-dir'):p.add_argument('--'+flag,type=Path,required=True)
    a=p.parse_args();new=a.experiment_dir;old=a.reference_dir
    def load(d):return [read_json(d/f) for f in ('generation/generation_report.json','base/fit_report.json',
        'comparison/delta_comparison_report.json','comparison/delta_repeats_report.json')]
    write_outputs(new/'report',build_report(*load(new),*load(old)))


if __name__=='__main__':main()
