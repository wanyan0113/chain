"""Four-strength paired summaries without pooling repeats or refitting controls."""
import argparse
from collections import defaultdict
import io
import math
from pathlib import Path
import statistics

from source_data.common import read_json,write_outputs
from .generation_delta_repeats import distribution
from .generation_varying_experiment import csv_rows,dumps
from .model import ModelError
from .structure import digest


def paired_strength_metrics(metrics,gammas,noise_std,repetitions,nodes,source_group):
    keys=('model','group','noise_sigma','repetition','node','split','target','prediction')
    indexed={tuple(r[k] for k in keys):r for r in metrics}
    if len(indexed)!=len(metrics):raise ModelError('强度汇总指标重复')
    expected={(m,g,s,r,n,sp,t,p) for m in ('base','fixed_delta','free_delta') for g in gammas for s in noise_std
        for r in repetitions for n in nodes for sp in ('train','holdout') for t in ('clean','observed') for p in ('local','recursive')}
    if set(indexed)!=expected:raise ModelError('强度汇总缺少或混入指标分组')
    summaries=[];paired=[];buckets=defaultdict(list)
    for key,row in indexed.items():
        m,g,s,rep,n,sp,t,p=key
        old=indexed[m,source_group,s,rep,n,sp,t,p]
        if row['count']!=old['count']:raise ModelError('跨强度配对样本数不一致')
        buckets[m,g,s,n,sp,t,p].append(row)
        if g!=source_group:
            paired.append(dict(model=m,group=g,gamma=gammas[g],noise_sigma=s,repetition=rep,node=n,
                split=sp,target=t,prediction=p,reference_rmse=old['rmse'],rmse=row['rmse'],rmse_change=row['rmse']-old['rmse'],
                reference_mae=old['mae'],mae=row['mae'],mae_change=row['mae']-old['mae']))
    for key,rows in sorted(buckets.items()):
        m,g,s,n,sp,t,p=key
        for q in ('rmse','mae','mean_residual','r_squared'):
            summaries.append(dict(model=m,group=g,gamma=gammas[g],noise_sigma=s,node=n,split=sp,target=t,prediction=p,
                quantity=q,expected_repeats=len(repetitions),**distribution([r[q] for r in rows if r[q] is not None])))
    return summaries,paired


def verify_sample_pairing(generation,groups,source_group):
    source={(r['base_case_id'],r['noise_sigma']):r for r in generation['observations'] if r['group']==source_group}
    if not source:raise ModelError('缺少无交互样本')
    roots=generation['topology']['roots'];checked=0
    for group in groups:
        rows=[r for r in generation['observations'] if r['group']==group]
        if len(rows)!=len(source) or len({(r['base_case_id'],r['noise_sigma']) for r in rows})!=len(source):
            raise ModelError('不同交互强度的基础样本遗漏或重复')
        for row in rows:
            old=source.get((row['base_case_id'],row['noise_sigma']))
            if old is None or row['split']!=old['split'] or row['conditions_by_node']!=old['conditions_by_node'] or row['observation_noise']!=old['observation_noise']:
                raise ModelError('交互强度样本的条件、划分或观测创新不配对')
            if any(row['node_values_clean'][n]!=old['node_values_clean'][n] for n in roots):raise ModelError('交互强度根节点改变')
            checked+=1
    return checked


def build_report(generation,new_base,new_comparison,new_summary,primary_base,primary_comparison,primary_summary,reference_generation,reference_comparison,reference_summary):
    gsha=digest(generation)
    for base,comp,summary in ((new_base,new_comparison,new_summary),(primary_base,primary_comparison,primary_summary)):
        if (comp.get('status')!='complete' or summary.get('status')!='complete' or base.get('status')!='complete'
            or comp['generation_sha256']!=gsha or base['generation_report_sha256']!=gsha
            or comp['baseline_fit_sha256']!=digest(base) or summary['comparison_sha256']!=digest(comp)):
            raise ModelError('强弱组或主组报告指纹/完成状态不一致')
    if (generation['source_varying_generation_sha256']!=digest(reference_generation)
        or reference_comparison['generation_sha256']!=digest(reference_generation)
        or reference_summary['comparison_sha256']!=digest(reference_comparison) or reference_comparison.get('status')!='complete'):
        raise ModelError('无交互冻结对照版本不一致')
    config=generation['interaction_config_snapshot'];source=config['source_group'];primary=config['primary_group']
    expected_new=set(config['gamma_groups'])-{primary}
    if set(new_comparison['config_snapshot']['groups'])!=expected_new or primary_comparison['config_snapshot']['groups']!=[primary]:
        raise ModelError('强弱与主组选择不符合预先约定')
    for other in (primary_comparison,reference_comparison):
        for key in ('noise_std','repetitions','fixed_delta','free_delta_bounds'):
            if new_comparison['config_snapshot'][key]!=other['config_snapshot'][key]:raise ModelError('跨强度协议设置改变')
        for mode in ('fixed_delta','free_delta'):
            if new_comparison['fits'][mode]['config_snapshot']['settings']!=other['fits'][mode]['config_snapshot']['settings']:
                raise ModelError('跨强度的求解设置不一致')
    gammas={source:0,**config['gamma_groups']};ordered=sorted(gammas,key=gammas.get)
    pairing_checks=verify_sample_pairing(generation,list(config['gamma_groups']),source)
    metrics=[r for c in (reference_comparison,primary_comparison,new_comparison) for r in c['metrics'] if r['group'] in gammas]
    noises=new_comparison['config_snapshot']['noise_std'];repetitions=new_comparison['config_snapshot']['repetitions']
    nodes=[n for n in generation['topology']['order'] if n in generation['topology']['receivers']]
    summaries,paired=paired_strength_metrics(metrics,gammas,noises,repetitions,nodes,source)
    changes=[]
    for group in ordered:
        if group==source:continue
        for mode in ('base','fixed_delta','free_delta'):
            for sigma in noises:
                for n in nodes:
                    for split in ('train','holdout'):
                        for target in ('clean','observed'):
                            for prediction in ('local','recursive'):
                                rows=[r for r in paired if (r['group'],r['model'],r['noise_sigma'],r['node'],r['split'],r['target'],r['prediction'])
                                      ==(group,mode,sigma,n,split,target,prediction)]
                                changes.append(dict(group=group,gamma=gammas[group],model=mode,noise_sigma=sigma,node=n,split=split,target=target,
                                    prediction=prediction,quantity='rmse_change',**distribution([r['rmse_change'] for r in rows])))
    bounds={(r['group'],r['repetition'],r['node'],r['split']):r for r in generation['interaction_structural_by_split']}
    checked=[];branches=[]
    for base in (new_base,primary_base):
        for job in base['jobs']:
            for m in job['metrics']:
                if m['target']!='clean' or m['mode']!='local':continue
                expected=bounds[job['group'],job['repetition'],m['node'],m['split']]['clean_local_base_rmse_lower_bound']
                if not math.isclose(m['rmse_lower_bound'],expected,abs_tol=1e-10):raise ModelError('强度组结构下界与局部拟合不一致')
                checked.append(dict(group=job['group'],gamma=gammas[job['group']],repetition=job['repetition'],noise_sigma=job['noise_sigma'],
                    node=m['node'],split=m['split'],local_rmse=m['rmse'],structural_lower_bound=expected))
    # Preserve branch coverage and local identifiability for the three fitted interaction strengths.
    fit_sources=[('base',new_base),('base',primary_base)]
    fit_sources.extend((m,c['fits'][m]) for c in (reference_comparison,primary_comparison,new_comparison) for m in ('fixed_delta','free_delta'))
    for mode,fit in fit_sources:
        for job in fit['jobs']:
            if job['group'] not in gammas:continue
            for local in job['local_fits']:
                row=local['fit']['receivers'][0];s=row['sensitivity']
                branches.append(dict(model=mode,group=job['group'],gamma=gammas[job['group']],noise_sigma=job['noise_sigma'],repetition=job['repetition'],
                    node=local['receiver'],local_rank=s['local_jacobian_rank'],parameter_count=s['parameter_count'],
                    inactive_parameters=','.join(s['inactive_parameters']),sod_rows=s['sod_active_rows'],
                    cod_rows=dumps(s['cod_active_rows']),excluded_tie_rows=s['excluded_tie_rows'],near_best_start_count=row['near_best_start_count'],
                    near_best_parameter_spread=dumps(row['near_best_parameter_spread'])))
    delta_rows=[r for s in (reference_summary,primary_summary,new_summary) for r in s['delta_summary'] if r['group'] in gammas]
    ranges=[r for s in (reference_summary,primary_summary,new_summary) for r in s['prediction_range'] if r['group'] in gammas]
    outside=[r for s in (reference_summary,primary_summary,new_summary) for r in s['outside_predictions'] if r['group'] in gammas]
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import rc_context
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    outputs={};modes=[('base','基础式','#367AA8'),('fixed_delta','固定δ','#32986B'),('free_delta','自由δ','#D78C36')]
    def find(mode,group,sigma,n='R',prediction='recursive',quantity='rmse'):
        return next(r for r in summaries if r['model']==mode and r['group']==group and r['noise_sigma']==sigma and r['node']==n
            and r['prediction']==prediction and r['split']=='holdout' and r['target']=='clean' and r['quantity']==quantity)
    def save(fig,name):
        for ext in ('png','svg'):
            stream=io.BytesIO();fig.savefig(stream,format=ext,dpi=180);outputs[name+'.'+ext]=stream.getvalue()
    with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10}):
        fig=Figure(figsize=(12,5),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,2)
        for ax,sigma in zip(axes,(0,8)):
            for mode,label,color in modes:
                rows=[find(mode,g,sigma) for g in ordered]
                ax.errorbar([gammas[g] for g in ordered],[r['mean'] for r in rows],yerr=[r['sd'] for r in rows],marker='o',capsize=3,label=label,color=color)
            ax.set(title=f'观测σ={sigma}',xlabel='交互强度γ',ylabel='末端R整网验证RMSE（SE点）',xticks=[gammas[g] for g in ordered]);ax.legend(fontsize=8);ax.grid(alpha=.2)
        fig.suptitle('完整四强度拟合对照（10次重复均值±样本标准差，过程波动关闭）');save(fig,'four_strength_rmse')
        fig=Figure(figsize=(12,4.8),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,len(nodes))
        for ax,n in zip(axes,nodes):
            for mode,label,color in modes:
                rows=[find(mode,g,0,n,quantity='mean_residual') for g in ordered]
                ax.errorbar([gammas[g] for g in ordered],[r['mean'] for r in rows],yerr=[r['sd'] for r in rows],marker='o',capsize=3,label=label,color=color)
            ax.axhline(0,color='#777777',linestyle='--');ax.set(title=n,xlabel='交互强度γ',ylabel='平均残差：预测−干净目标',xticks=[gammas[g] for g in ordered]);ax.grid(alpha=.2);ax.legend(fontsize=8)
        fig.suptitle('干净验证目标的整网残差方向，观测σ=0（均值±样本标准差）');save(fig,'four_strength_residual')
        fig=Figure(figsize=(12,4.8),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,len(nodes))
        for ax,n in zip(axes,nodes):
            rows=[next(r for r in delta_rows if r['model']=='free_delta' and r['group']==g and r['noise_sigma']==0 and r['node']==n) for g in ordered]
            ax.errorbar([gammas[g] for g in ordered],[r['mean'] for r in rows],yerr=[r['sd'] for r in rows],marker='o',capsize=3,color='#D78C36')
            for value in (-10,10):ax.axhline(value,color='#777777',linestyle=':')
            ax.set(title=n,xlabel='交互强度γ',ylabel='自由δ估计',xticks=[gammas[g] for g in ordered]);ax.grid(alpha=.2)
        fig.suptitle('四强度自由δ分布，观测σ=0（均值±样本标准差；预设界限不变）');save(fig,'four_strength_delta')
        fig=Figure(figsize=(12,4.8),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,len(nodes))
        for ax,n in zip(axes,nodes):
            local=[find('base',g,0,n,'local') for g in ordered]
            old=reference_generation['structural_by_split'];new=generation['interaction_structural_by_split']
            bound_values=[[r['clean_local_base_rmse_lower_bound'] for r in (old if g==source else new)
                           if r['group']==g and r['node']==n and r['split']=='holdout'] for g in ordered]
            ax.errorbar([gammas[g] for g in ordered],[r['mean'] for r in local],yerr=[r['sd'] for r in local],marker='o',capsize=3,label='局部RMSE',color='#367AA8')
            ax.errorbar([gammas[g] for g in ordered],[statistics.mean(v) for v in bound_values],yerr=[statistics.stdev(v) for v in bound_values],marker='s',capsize=3,label='同数据下界',color='#8999AA')
            ax.set(title=n,xlabel='交互强度γ',ylabel='SE点',xticks=[gammas[g] for g in ordered]);ax.grid(alpha=.2);ax.legend(fontsize=8)
        fig.suptitle('基础式局部误差与同划分结构下界，观测σ=0（均值±样本标准差）');save(fig,'four_strength_bound')
    lines=['# 交互强弱正式拟合结果解释','',
        '本轮复用第12阶段冻结的γ=0.05和0.30样本，完成两组正式FDNA拟合，没有新增样本、改变强度、重划数据或重新拟合γ=0/0.15。γ=0结果引用第10阶段，γ=0.15引用第12阶段；全部比较按同模型、噪声、重复、节点、划分及目标配对。',
        '', '新增基础式80组、固定δ80组、自由δ80组，共240组整网计算、720次接受节点拟合。每组10次独立数据重复，每次250个基础网络，200训练、50验证，观测σ=0/2/5/8。强度和噪声版本共享基础样本，不能当成新增独立推演。过程波动保持关闭。',
        '', '固定δ=-5、自由δ[-10,10]；各组均使用相同的12个起点、有界L-BFGS-B及迭代设置，不按验证误差选择强度、起点或界限。FDNA输入只有上游SE；γ和C/M/E仅作用于生成端。',
        '', '| γ | 观测σ | 基础式R | 固定δ R | 自由δ R |','| ---: | ---: | ---: | ---: | ---: |']
    for group in ordered:
        for sigma in (0,8):
            rows=[find(m,group,sigma) for m,_,_ in modes]
            lines.append(f'| {gammas[group]:.2f} | {sigma} | '+' | '.join(f"{r['mean']:.3f} ± {r['sd']:.3f}" for r in rows)+' |')
    lines+=['', '上表为末端R对干净验证目标的整网RMSE；±为10次重复的样本标准差，不是置信区间。跨强度时实际中间状态和目标均变化，RMSE差描述本实验强度变化后的表现，不能直接解释成因果传播误差。',
        '', '| γ | 自由δ触下界/节点估计数 | 触上界 | 自由δ局部秩不足 | 含δ越界预测条目 |', '| ---: | ---: | ---: | ---: | ---: |']
    for group in ordered:
        rows=[r for r in delta_rows if r['group']==group and r['model']=='free_delta']
        lines.append(f"| {gammas[group]:.2f} | {sum(r['lower_bound_count'] for r in rows)}/{sum(r['n'] for r in rows)} | {sum(r['upper_bound_count'] for r in rows)} | {sum(r['rank_deficient_count'] for r in rows)} | {sum(r['group']==group for r in outside)} |")
    lines+=['', '节点估计数包括三接受节点、四档配对噪声和十次重复，不是完全独立实验次数。越界条目还区分局部/整网及训练/验证，不能直接视为独立样本；预测继续保持不裁剪。',
        '', '自由δ改善当前误差不等于参数可辨识；触及−10不说明真实δ就是−10，也不据此事后放宽边界。rank_and_branches.csv保留本轮及主交互组基础式、四强度含δ模型的局部秩、活跃分支、不活跃参数及近等训练损失起点跨度；不是参数置信区间或全局唯一性检验。γ=0基础式的深入诊断仍见此前冻结报告。',
        '', '基础式的结构下界按每个强度实际干净上游和目标重新计算；本轮与主组共720项训练/验证局部下界逐项核对。不将这个下界用于含δ或整网RMSE。残差图用于观察偏差方向，不替代完整的逐样本残差与参数补偿分析。',
        '', '本轮完成预设交互强弱的正式拟合对照，不等于所有生成系数敏感性均完成。尚待系统改变0.90/0.78、0.62/0.38及自身条件系数，统一深入诊断，开展正式多输入与11节点18边独立生成拟合。没有改变原独立生成机制来迎合FDNA。','']
    expected_new_groups=3*len(expected_new)*len(noises)*len(repetitions)
    metadata=dict(generation_sha256=gsha,new_base_sha256=digest(new_base),new_comparison_sha256=digest(new_comparison),
        primary_comparison_sha256=digest(primary_comparison),reference_comparison_sha256=digest(reference_comparison),
        fitted_gamma_values=sorted(gammas.values()),new_expected_groups=expected_new_groups,new_local_fits=expected_new_groups*len(nodes),
        sample_pairing_checks=pairing_checks,structural_checks=len(checked),new_samples_generated=False,old_controls_refitted=False)
    outputs.update({'结果解释.md':'\n'.join(lines),'four_strength_metrics.csv':csv_rows(summaries),
        'strength_paired_changes.csv':csv_rows(paired),'strength_change_summary.csv':csv_rows(changes),
        'rank_and_branches.csv':csv_rows(branches),'all_strength_delta.csv':csv_rows(delta_rows),
        'all_strength_prediction_range.csv':csv_rows(ranges),'all_strength_outside_predictions.csv':csv_rows(outside),
        'structural_crosscheck.csv':csv_rows(checked),'汇报来源核验.json':dumps(metadata)})
    return outputs


def main():
    p=argparse.ArgumentParser()
    for name in ('experiment-dir','primary-dir','reference-dir'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();new=a.experiment_dir;primary=a.primary_dir;ref=a.reference_dir
    outputs=build_report(read_json(primary/'generation/generation_report.json'),read_json(new/'base/fit_report.json'),
        read_json(new/'comparison/delta_comparison_report.json'),read_json(new/'comparison/delta_repeats_report.json'),
        read_json(primary/'base/fit_report.json'),read_json(primary/'comparison/delta_comparison_report.json'),read_json(primary/'comparison/delta_repeats_report.json'),
        read_json(ref/'generation/generation_report.json'),read_json(ref/'comparison/delta_comparison_report.json'),read_json(ref/'comparison/delta_repeats_report.json'))
    write_outputs(new/'report',outputs)


if __name__=='__main__':main()
