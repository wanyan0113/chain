"""Boundary-aware capability gain sensitivity on paired network samples."""
import argparse
import io
import math
from pathlib import Path

from source_data.common import read_json,write_outputs
from .generation_coefficients import affected_nodes
from .generation_coefficient_experiment import validate_protocol
from .generation_interaction_sensitivity_report import paired_strength_metrics,verify_sample_pairing
from .generation_delta_repeats import distribution
from .generation_varying_experiment import csv_rows,dumps
from .model import ModelError
from .structure import digest


def build_report(generation,base,comparison,summary,reference,old_base,old_comparison,old_summary):
    validate_protocol(generation,base['config_snapshot'],comparison['config_snapshot'],reference,old_base,old_comparison)
    for g,b,c,s in ((generation,base,comparison,summary),(reference,old_base,old_comparison,old_summary)):
        if (any(r.get('status')!='complete' for r in (g,b,c,s)) or b['generation_report_sha256']!=digest(g)
            or c['generation_sha256']!=digest(g) or c['baseline_fit_sha256']!=digest(b) or s['comparison_sha256']!=digest(c)):
            raise ModelError('能力增益敏感性来源指纹或状态不一致')
    cfg=generation['coefficient_config_snapshot'];groups=cfg['condition_groups']
    source=cfg['reference_experiment']['condition_sampling']['group_id'];fc=base['config_snapshot']
    originals=generation['node_configs_snapshot']['nodes'];factors={};aggregates=[];paired=[]
    if any(v['factor']!='capability_gain' for v in groups.values()):raise ModelError('本汇报只接受能力增益组')
    metrics=[r for c in (old_comparison,comparison) for r in c['metrics']]
    for node in ('T','Q','R'):
        h=originals[node]['condition_function']['capability_gain']
        levels={source:h,**{g:v['value'] for g,v in groups.items() if v['node']==node}}
        expected=2 if h in (0,1) else 3
        if len(levels)!=expected:raise ModelError('边界须预设单侧组，内部须预设高低两组')
        if h in (0,1) and any((v>=h if h==1 else v<=h) for g,v in levels.items() if g!=source):
            raise ModelError('边界扰动方向非法')
        factors[node]=levels
        rows,changes=paired_strength_metrics([r for r in metrics if r['group'] in levels],levels,
            fc['noise_std'],fc['repetitions'],['T','Q','R'],source)
        for r in rows+changes:r.update(perturbed_node=node,capability_gain=r.pop('gamma'))
        aggregates.extend(rows);paired.extend(changes)
    pairing=verify_sample_pairing(generation,list(groups),source)
    bounds={(r['group'],r['repetition'],r['node'],r['split']):r for r in generation['coefficient_structural_by_split']}
    checked=[]
    for j in base['jobs']:
        for m in j['metrics']:
            if m['mode']!='local' or m['target']!='clean':continue
            expected=bounds[j['group'],j['repetition'],m['node'],m['split']]['clean_local_base_rmse_lower_bound']
            if not math.isclose(expected,m['rmse_lower_bound'],abs_tol=1e-10,rel_tol=0):raise ModelError('实际数据结构下界不符')
            checked.append(dict(group=j['group'],repetition=j['repetition'],noise_sigma=j['noise_sigma'],**m))
    unchanged=0;invariant_parameters=0
    for mode,new,old in [('base',base,old_base)]+[(m,comparison['fits'][m],old_comparison['fits'][m]) for m in ('fixed_delta','free_delta')]:
        lookup={(j['noise_sigma'],j['repetition']):j for j in old['jobs'] if j['group']==source}
        for j in new['jobs']:
            before=lookup[j['noise_sigma'],j['repetition']]
            affected=affected_nodes(generation['network_snapshot'],groups[j['group']]['node'])
            for n in set(generation['topology']['receivers'])-affected:
                a=next(l for l in j['local_fits'] if l['receiver']==n)['fit']['receivers'][0]
                b=next(l for l in before['local_fits'] if l['receiver']==n)['fit']['receivers'][0]
                if a['parameters']!=b['parameters'] or a['delta']!=b['delta']:raise ModelError('不受影响节点拟合参数漂移')
                invariant_parameters+=1
                for m in j['metrics']:
                    if m['node']!=n:continue
                    old_m=next(r for r in before['metrics'] if all(r[k]==m[k] for k in ('node','split','target','mode')))
                    if any(not math.isclose(m[k],old_m[k],abs_tol=1e-12,rel_tol=0) for k in ('rmse','mae','mean_residual')):
                        raise ModelError('不受影响节点误差指标漂移')
                    unchanged+=1
    delta=[r for s in (old_summary,summary) for r in s['delta_summary'] if r['group'] in set(groups)|{source}]
    outside=[r for s in (old_summary,summary) for r in s['outside_predictions'] if r['group'] in set(groups)|{source}]
    branches=[]
    for mode,fit in [('base',base),('base',old_base)]+[(m,c['fits'][m]) for c in (comparison,old_comparison) for m in ('fixed_delta','free_delta')]:
        for j in fit['jobs']:
            if j['group'] not in set(groups)|{source}:continue
            for l in j['local_fits']:
                r=l['fit']['receivers'][0];s=r['sensitivity']
                branches.append(dict(model=mode,group=j['group'],noise_sigma=j['noise_sigma'],repetition=j['repetition'],
                    node=l['receiver'],local_rank=s['local_jacobian_rank'],parameter_count=s['parameter_count'],
                    inactive_parameters=','.join(s['inactive_parameters']),sod_rows=s['sod_active_rows'],cod_rows=dumps(s['cod_active_rows']),
                    excluded_tie_rows=s['excluded_tie_rows'],
                    near_best_start_count=r['near_best_start_count'],near_best_parameter_spread=dumps(r['near_best_parameter_spread'])))
    def find(node,mode,group,sigma,target_node='R',prediction='recursive',quantity='rmse'):
        return next(r for r in aggregates if (r['perturbed_node'],r['model'],r['group'],r['noise_sigma'],r['node'],r['split'],r['target'],r['prediction'],r['quantity'])
            ==(node,mode,group,sigma,target_node,'holdout','clean',prediction,quantity))
    def show(r):return f"{r['mean']:.3f}"+(f" ± {r['sd']:.3f}" if r['sd'] is not None else '（单次，无SD）')
    repeats=len(fc['repetitions']);jobs=3*len(groups)*len(fc['noise_std'])*repeats
    experiment=cfg['reference_experiment']['base_experiment']
    level_description='；'.join(f"{n}冻结h={levels[source]:.2f}，预设"+'/'.join(f'{v:.2f}' for g,v in levels.items() if g!=source)
        for n,levels in factors.items())
    noises=list(dict.fromkeys((fc['noise_std'][0],fc['noise_std'][-1])))
    lines=['# 自身能力条件函数代表性敏感性','',
        '改变的是能力条件乘数(1−h)+hC，不是C的抽样值。C<1时，h越大乘数越低；C=1时各h乘数均为1。'+level_description+'。边界采用单侧扰动；基础项严格联动为1−h，不将越界候选截断后当成对称实验。',
        '',f"本批{len(groups)}个新组、三模型共{jobs}组整网计算、{jobs*3}次接受节点拟合。每组{repeats}次独立数据重复，每次{experiment['base_cases']}个基础网络（{experiment['train_count']}训练、{experiment['base_cases']-experiment['train_count']}验证），{len(fc['noise_std'])}档观测噪声为配对版本。冻结基线不重拟合，根输入、C/M/E、划分和噪声创新复用。过程波动与交互关闭；固定δ=-5、自由δ[-10,10]、{fc['settings']['starts']}个起点及其余求解设置保持。",
        '', 'T的变化可传至T/Q/R，Q的变化可传至Q/R，R的变化只作用于R。不受影响节点重新拟合后核验一致；受影响节点不强行固定拟合参数。',
        '', '| 被扰动节点 | h | 观测σ | 基础式末端R | 固定δ末端R | 自由δ末端R |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for n,levels in factors.items():
        for g in sorted(levels,key=levels.get):
            for sigma in noises:lines.append(f'| {n} | {levels[g]:.2f} | {sigma} | '+' | '.join(show(find(n,m,g,sigma)) for m in ('base','fixed_delta','free_delta'))+' |')
    q_order=sorted(factors['Q'],key=factors['Q'].get);lo,hi=q_order[0],q_order[-1];sigma=noises[0]
    q_interpretation=(f"例如Q的h从{factors['Q'][lo]:.2f}变为{factors['Q'][hi]:.2f}时（观测σ={sigma}），"
        f"基础式末端RMSE均值由{find('Q','base',lo,sigma)['mean']:.3f}变为{find('Q','base',hi,sigma)['mean']:.3f}，"
        f"自由δ由{find('Q','free_delta',lo,sigma)['mean']:.3f}变为{find('Q','free_delta',hi,sigma)['mean']:.3f}。"
        '能力乘数的单调变化不能直接代替拟合误差的变化方向；需分别看各模型的实际预测、目标和参数诊断，不将某个系数认定为普遍更优。')
    lines+=['', '上表为末端R对干净验证目标的整网RMSE；±是重复间样本标准差，不是置信区间。三个节点面板引用同一冻结基线，不可重复计作独立实验。跨组实际状态与目标可能变化，RMSE差不是单独的因果传播误差。',
        '',q_interpretation,
        '', '| 组 | 自由δ触下界/节点估计数 | 自由δ局部秩不足 | 含δ越界预测条目 |','| --- | ---: | ---: | ---: |']
    for g in [source,*groups]:
        rows=[r for r in delta if r['group']==g and r['model']=='free_delta']
        lines.append(f"| {g} | {sum(r['lower_bound_count'] for r in rows)}/{sum(r['n'] for r in rows)} | {sum(r['rank_deficient_count'] for r in rows)} | {sum(r['group']==g for r in outside)} |")
    lines+=['','估计数包括接受节点、配对噪声及重复；越界条目另含模型、划分和局部/整网维度，不代表独立实验次数。预测改善、参数识别、输出范围分开报告；不按验证结果调整h或δ界限。',
        '',f'{pairing}个观测版本配对，{len(checked)}项同数据基础式局部结构下界核对通过；不受影响节点的{invariant_parameters}次局部拟合参数及{unchanged}项指标与来源一致。下界不直接用于含δ或整网误差。',
        '', '本批完成T/Q/R能力项的代表性扰动；T/Q任务项和环境项的八个组已预先登记，但尚未完成正式拟合。系数联合变化和更广范围不在本批内。新组完整有限扰动/参数补偿及输入质量诊断仍需另行补齐；之后再做多输入已知参数恢复、正式多输入及11节点18边实验。','']
    out={'结果解释.md':'\n'.join(lines),'capability_metrics.csv':csv_rows(aggregates),'paired_capability_changes.csv':csv_rows(paired),
        'generation_shifts.csv':csv_rows(generation['coefficient_shifts']),'structural_crosscheck.csv':csv_rows(checked),
        'rank_and_branches.csv':csv_rows(branches),'all_capability_delta.csv':csv_rows(delta),'outside_predictions.csv':csv_rows(outside),
        '汇报来源核验.json':dumps(dict(status='complete',generation_sha256=digest(generation),comparison_sha256=digest(comparison),
            reference_comparison_sha256=digest(old_comparison),new_model_jobs=jobs,new_receiver_fits=jobs*3,
            paired_observation_checks=pairing,structural_checks=len(checked),invariant_local_fits=invariant_parameters,unchanged_metric_checks=unchanged))}
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import rc_context
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    def save(fig,name):
        for ext in ('png','svg'):
            b=io.BytesIO();fig.savefig(b,format=ext,dpi=180);out[name+'.'+ext]=b.getvalue()
    modes=[('base','基础式','#367AA8'),('fixed_delta','固定δ','#32986B'),('free_delta','自由δ','#D78C36')]
    with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10}):
        for quantity,name,ylabel in [('rmse','capability_rmse','末端R整网验证RMSE（SE点）'),('mean_residual','capability_residual','平均残差：预测−目标（SE点）')]:
            fig=Figure(figsize=(14,4*len(noises)),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(len(noises),3,squeeze=False)
            for i,sigma in enumerate(noises):
                for ax,(n,levels) in zip(axes[i],factors.items()):
                    order=sorted(levels,key=levels.get)
                    for mode,label,color in modes:
                        rows=[find(n,mode,g,sigma,quantity=quantity) for g in order]
                        ax.errorbar([levels[g] for g in order],[r['mean'] for r in rows],yerr=[r['sd'] or 0 for r in rows],marker='o',capsize=3,label=label,color=color)
                    ax.set(title=f'仅改变{n}能力项，观测σ={sigma}',xlabel=f'{n}能力增益h',ylabel=ylabel,xticks=[levels[g] for g in order]);ax.grid(alpha=.2)
            fig.legend(*axes[0,0].get_legend_handles_labels(),loc='outside lower center',ncol=3)
            fig.suptitle(f'能力条件系数代表性敏感性（{repeats}次重复；误差棒为样本标准差，单次无SD）');save(fig,name)
        fig=Figure(figsize=(14,4.5),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,3)
        for ax,(n,levels) in zip(axes,factors.items()):
            order=sorted(levels,key=levels.get);actual=[find(n,'base',g,fc['noise_std'][0],n,'local') for g in order]
            values=[distribution([r['clean_local_base_rmse_lower_bound'] for r in reference['structural_by_split']+generation['coefficient_structural_by_split']
                if r['group']==g and r['node']==n and r['split']=='holdout']) for g in order]
            for rows,label,color in [(actual,'基础式局部RMSE','#367AA8'),(values,'同数据结构下界','#8999AA')]:
                ax.errorbar([levels[g] for g in order],[r['mean'] for r in rows],yerr=[r['sd'] or 0 for r in rows],marker='o',capsize=3,label=label,color=color)
            ax.set(title=f'被扰动节点{n}',xlabel='能力增益h',ylabel='SE点',xticks=[levels[g] for g in order]);ax.grid(alpha=.2)
        fig.legend(*axes[0].get_legend_handles_labels(),loc='outside lower center',ncol=2)
        fig.suptitle(f"基础式局部误差及同数据下界（观测σ={fc['noise_std'][0]}，均值±样本标准差）");save(fig,'capability_bound')
    return out


def main():
    p=argparse.ArgumentParser()
    for flag in ('experiment-dir','reference-dir'):p.add_argument('--'+flag,type=Path,required=True)
    a=p.parse_args()
    def load(d):return [read_json(d/f) for f in ('generation/generation_report.json','base/fit_report.json',
        'comparison/delta_comparison_report.json','comparison/delta_repeats_report.json')]
    write_outputs(a.experiment_dir/'report',build_report(*load(a.experiment_dir),*load(a.reference_dir)))


if __name__=='__main__':main()
