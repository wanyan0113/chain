"""Paired observation-noise experiments over independently resampled networks."""
import argparse
from collections import defaultdict
import csv
import io
import json
from pathlib import Path
import statistics
import sys

from source_data.common import read_json, write_outputs
from .generation_network_check import run_network_check
from .generation_fitting import fit_joint_network, fitting_csvs
from .model import ModelError, _finite
from .structure import digest


def describe(values):
    if not values:
        return dict(n=0,mean=None,sd=None,minimum=None,maximum=None)
    return dict(n=len(values),mean=statistics.mean(values),
                sd=statistics.stdev(values) if len(values)>1 else None,
                minimum=min(values),maximum=max(values))


def summarize_repeats(fit,generation):
    """Replicate is the summary unit; never pool paired observations as repeats."""
    _finite(fit)
    if fit['generation_report_sha256']!=digest(generation):
        raise ModelError('重复实验拟合与生成版本不一致')
    config=fit['config_snapshot']; reps=config['repetitions']
    if 0 not in config['noise_std']:
        raise ModelError('配对噪声分析需要σ=0基线')
    expected={(g,s,r) for g in config['groups'] for s in config['noise_std'] for r in reps}
    jobs={(j['group'],j['noise_sigma'],j['repetition']):j for j in fit['jobs']}
    if len(jobs)!=len(fit['jobs']) or set(jobs)!=expected:
        raise ModelError('重复实验分组遗漏或重复，不能只汇总成功组')
    status='complete' if all(j['status']=='complete' for j in jobs.values()) else 'blocked'
    if fit['status']!=status: raise ModelError('整体状态与分组状态不一致')
    receivers=fit['topology']['receivers']
    metric_values=defaultdict(dict); parameter_values=defaultdict(list); sensitivity=[]
    for (group,sigma,rep),job in jobs.items():
        if job['status']!='complete': continue
        metrics={(m['node'],m['target'],m['mode']):m for m in job['metrics'] if m['split']=='holdout'}
        wanted={(n,t,m) for n in receivers for t in ('clean','observed') for m in ('local','recursive')}
        if len(metrics)!=sum(m['split']=='holdout' for m in job['metrics']) or set(metrics)!=wanted:
            raise ModelError('完整拟合组须逐节点保留两类目标与两种验证预测指标')
        for (node,target,mode),metric in metrics.items():
            if metric['count']!=job['counts']['holdout']:
                raise ModelError('验证指标数量与整网验证数量不一致')
            metric_values[group,sigma,node,target,mode][rep]=metric['rmse']
        for p in job['parameters']:
            for parameter in ('alpha','beta'):
                parameter_values[group,sigma,p['receiver'],p['feeder'],parameter].append(p[parameter])
        for local in job['local_fits']:
            s=local['fit']['receivers'][0].get('sensitivity')
            if s:
                sensitivity.append(dict(group=group,noise_sigma=sigma,repetition=rep,node=local['receiver'],
                    rank=s['local_jacobian_rank'],parameter_count=s['parameter_count'],
                    inactive_parameters=','.join(s['inactive_parameters'])))
    failed=[]
    for (group,sigma,rep),job in jobs.items():
        if job['status']=='complete': continue
        for local in job['local_fits']:
            row=local['fit']['receivers'][0]
            if row['status']=='fitted': continue
            attempt=min(row.get('attempts',[]),key=lambda a:a['rmse'],default={})
            failed.append(dict(group=group,noise_sigma=sigma,repetition=rep,node=local['receiver'],
                status=row['status'],best_training_rmse=attempt.get('rmse'),message=attempt.get('message','无可用求解结果')))
    metrics=[]; paired=[]
    for group in config['groups']:
        for sigma in config['noise_std']:
            for node in receivers:
                for target in ('clean','observed'):
                    for mode in ('local','recursive'):
                        key=group,sigma,node,target,mode
                        values=metric_values[key]
                        metrics.append(dict(group=group,noise_sigma=sigma,node=node,target=target,mode=mode,
                            expected_repeats=len(reps),**describe(list(values.values()))))
                        baseline=metric_values[group,0,node,target,mode]
                        matched=sorted(values.keys() & baseline.keys())
                        paired.append(dict(group=group,noise_sigma=sigma,node=node,target=target,mode=mode,
                            expected_pairs=len(reps),**describe([values[r]-baseline[r] for r in matched])))
    parameters=[]
    for (group,sigma,node,feeder,parameter),values in sorted(parameter_values.items()):
        upper=1 if parameter=='alpha' else 100
        parameters.append(dict(group=group,noise_sigma=sigma,node=node,feeder=feeder,parameter=parameter,
            expected_repeats=len(reps),near_lower_count=sum(v<=1e-6 for v in values),
            near_upper_count=sum(upper-v<=1e-6 for v in values),**describe(values)))
    clipping=[]
    for group in config['groups']:
        for sigma in config['noise_std']:
            rows=[r for r in generation['observations'] if r['group']==group and
                  r['noise_sigma']==sigma and r['repetition'] in reps and r['split']=='holdout']
            for node in generation['topology']['order']:
                count=sum(not 0<=r['node_values_clean'][node]+r['observation_noise'][node]<=100 for r in rows)
                clipping.append(dict(group=group,noise_sigma=sigma,node=node,count=len(rows),clipped_count=count,
                    clipped_fraction=count/len(rows),mean_observation_shift=statistics.mean(
                        r['node_values_observed'][node]-r['node_values_clean'][node] for r in rows)))
    return dict(format_version='1.0',stage='second_stage_step_4_noise_repeats',status=status,
        generation_sha256=digest(generation),fit_sha256=digest(fit),config_snapshot=config,
        expected_jobs=len(expected),complete_jobs=sum(j['status']=='complete' for j in jobs.values()),
        generation_counts=generation['counts'],generation_config_snapshot=generation['config_snapshot'],topology=fit['topology'],
        metrics=metrics,paired_rmse_changes=paired,parameter_spread=parameters,failed_fits=failed,
        sensitivity=sensitivity,observation_clipping=clipping,
        policies=dict(summary_unit='independent_data_repetition',uncertainty='sample_sd_not_confidence_interval',
            paired_baseline='same_repetition_sigma_zero',optimizer_seed='fixed_across_repetitions',
            local_feeders='clean',recursive_roots='clean',noisy_targets='fit_only_then_evaluate_clean_and_observed',
            parameter_truth_available=False),
        limits=['观测噪声只污染拟合目标，未检验供给端测量误差；过程波动与交互项由生成配置单独记录',
                '误差带是独立数据重复的样本标准差，不是置信区间；噪声档位和条件组彼此配对',
                '参数离散范围是拟合结果的描述，不是参数真值误差或统计置信区间',
                '仅七节点人工机制网络，未扩展到原文11节点18边或实际实验数据'])


def summary_csvs(report):
    outputs={}
    for name in ('metrics','paired_rmse_changes','parameter_spread','sensitivity','observation_clipping','failed_fits'):
        rows=report[name]; stream=io.StringIO(newline='')
        if rows:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        outputs[name+'.csv']='\ufeff'+stream.getvalue()
    return outputs


def render_summary(report):
    fc=report['config_snapshot']; gc=report['generation_config_snapshot']; repeats=len(fc['repetitions'])
    lines=['# 第二阶段第四步：噪声与重复实验','',
        f"本轮完成{report['complete_jobs']}/{report['expected_jobs']}个分组拟合。每个分组对三个接受节点分别求解，再由四个干净根节点驱动整网递推。",'',
        f"{len(fc['groups'])}组固定条件、观测噪声σ={fc['noise_std']}，{repeats}次独立数据重复，每次{gc['base_cases']}组根节点输入，{gc['train_count']}训练、{gc['base_cases']-gc['train_count']}验证。每次重复内，不同条件及噪声版本使用同一根节点、划分和标准正态扰动；跨重复重新抽样。",
        f"优化器随机种子固定、每次{fc['settings']['starts']}个起点，结果波动主要描述数据重复的影响；不单独代表优化器随机性。过程噪声σ={gc['process_std']}，交互强度γ={gc['interaction_gamma']}。",'',
        '## 验证结果（干净目标）','',
        '| 条件 | σ | 节点 | 成功重复数 | 局部RMSE 均值±SD | 整网RMSE 均值±SD | 整网配对增量 均值±SD（配对数） |',
        '| --- | ---: | --- | ---: | ---: | ---: | ---: |']
    lookup={(m['group'],m['noise_sigma'],m['node'],m['mode']):m for m in report['metrics'] if m['target']=='clean'}
    delta={(m['group'],m['noise_sigma'],m['node']):m for m in report['paired_rmse_changes'] if m['target']=='clean' and m['mode']=='recursive'}
    def show(row):
        return '无结果' if row['mean'] is None else f"{row['mean']:.3f} ± {row['sd']:.3f}" if row['sd'] is not None else f"{row['mean']:.3f}（单次）"
    for g in report['config_snapshot']['groups']:
        for s in report['config_snapshot']['noise_std']:
            for n in report['topology']['order']:
                if n not in report['topology']['receivers']: continue
                lines.append(f"| {g} | {s} | {n} | {lookup[g,s,n,'recursive']['n']}/{repeats} | {show(lookup[g,s,n,'local'])} | {show(lookup[g,s,n,'recursive'])} | {show(delta[g,s,n])}（{delta[g,s,n]['n']}） |")
    lines+=['','## 图表解释','',
        'noise_rmse：横轴为训练目标观测噪声σ（SE点），纵轴为独立验证集对干净目标的RMSE。蓝线为局部预测，橙线为仅输入根节点的整网递推；带状区域是独立数据重复的均值±样本标准差，可能重叠，不表示置信区间。',
        'paired_noise_change：同一次重复内，有噪声拟合的验证RMSE减去无噪声拟合的验证RMSE，再对配对差值求均值和标准差。正值表示本次噪声使预测变差，负值表示变好；不能预设误差必然随噪声单调上升。',
        '每格图对应一个条件组和接受节点。T、Q、R分别为目标探知与追踪、信息融合与协调、任务执行。所有图表均来自实际计算。','',
        '## 如何解释参数','',
        'parameter_spread.csv给出每条边α、β的跨重复均值、样本标准差、最小最大值及接近界限的次数。生成机制没有FDNA参数真值，因此这些不是恢复误差。sensitivity.csv保留逐次局部敏感度；秩不足、不活跃参数或边界估计不能直接解释为真实依赖强度。','',
        'observation_clipping.csv另存验证观测裁剪比例和平均偏移。裁剪后噪声不再保证零均值，不能把噪声档位当作实际残差标准差。','',
        '## 本轮范围','',*report['limits'],'',
        '完整拟合结果保留训练/验证、干净/观测目标两种评估。主要图表使用干净目标，以区分模型误差与验证目标本身的测量噪声。失败组保留计数，不悄悄从分母删除；阶段状态为blocked时不可称为全组完成。failed_fits.csv登记未完成节点及最佳训练目标起点的终止原因；部分组失败时，图中均值仅代表已完成分组，不能宣称全10次稳定。','']
    return '\n'.join(lines)


def plot_summary(report):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib import rc_context
    groups=report['config_snapshot']['groups']
    nodes=[n for n in report['topology']['order'] if n in report['topology']['receivers']]
    outputs={}
    for key,filename,title,ylabel in [
        ('metrics','noise_rmse','观测噪声与验证预测误差','干净目标 RMSE（SE点）'),
        ('paired_rmse_changes','paired_noise_change','同次重复相对无噪声基线的误差变化','配对 ΔRMSE（SE点）')]:
        with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10}):
            fig=Figure(figsize=(4.3*len(nodes),3.7*len(groups)),layout='constrained'); FigureCanvasAgg(fig)
            axes=fig.subplots(len(groups),len(nodes),squeeze=False,sharex=True)
            for i,g in enumerate(groups):
                for j,n in enumerate(nodes):
                    ax=axes[i,j]
                    for mode,label,color in [('local','局部预测','#367AA8'),('recursive','整网递推','#D78C36')]:
                        rows=sorted((r for r in report[key] if r['group']==g and r['node']==n and
                            r['target']=='clean' and r['mode']==mode and r['mean'] is not None),key=lambda r:r['noise_sigma'])
                        x=[r['noise_sigma'] for r in rows]; y=[r['mean'] for r in rows]
                        ax.plot(x,y,'o-',label=label,color=color,linewidth=1.6)
                        shaded=[r for r in rows if r['sd'] is not None]
                        ax.fill_between([r['noise_sigma'] for r in shaded],
                            [r['mean']-r['sd'] for r in shaded],[r['mean']+r['sd'] for r in shaded],alpha=.15,color=color)
                    if key=='paired_rmse_changes': ax.axhline(0,color='#777777',linestyle='--',linewidth=1)
                    ax.set(title=g+' / '+n,xlabel='观测噪声 σ（SE点）',ylabel=ylabel,
                           xticks=report['config_snapshot']['noise_std'])
                    ax.grid(alpha=.2); ax.legend(fontsize=9)
            fig.suptitle(title+'（跨重复均值 ± 样本标准差）')
            for ext in ('png','svg'):
                stream=io.BytesIO(); fig.savefig(stream,format=ext,dpi=200)
                outputs[filename+'.'+ext]=stream.getvalue()
    return outputs


def main(argv=None):
    parser=argparse.ArgumentParser(description='综合网络的配对噪声与数据重复实验')
    for arg in ('network','nodes','generation-config','fit-config','output-dir'):
        parser.add_argument('--'+arg,type=Path,required=True)
    parser.add_argument('--plot',action='store_true')
    args=parser.parse_args(argv)
    try:
        # Reject existing results before starting a potentially long run.
        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise ModelError('重复实验结果目录须为空，避免覆盖已有结果')
        generation=run_network_check(read_json(args.network),read_json(args.nodes),read_json(args.generation_config))
        print(json.dumps(dict(stage='generated',counts=generation['counts']),ensure_ascii=False),flush=True)
        fit=fit_joint_network(generation,read_json(args.fit_config),
            progress=lambda job:print(json.dumps(job,ensure_ascii=False),flush=True))
        report=summarize_repeats(fit,generation)
        outputs={
            'generation_report.json':json.dumps(generation,ensure_ascii=False,allow_nan=False),
            'fit_report.json':json.dumps(fit,ensure_ascii=False,allow_nan=False),
            'robustness_report.json':json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),
            '噪声与重复实验说明.md':render_summary(report),**summary_csvs(report),**fitting_csvs(fit)}
        if args.plot: outputs.update(plot_summary(report))
        write_outputs(args.output_dir,outputs,protected=[args.network,args.nodes,args.generation_config,args.fit_config])
        print(json.dumps(dict(status=report['status'],complete_jobs=report['complete_jobs'],expected_jobs=report['expected_jobs']),ensure_ascii=False),flush=True)
        return 0 if report['status']=='complete' else 1
    except (ValueError,OSError,KeyError,TypeError,ImportError) as exc:
        print(json.dumps(dict(status='invalid',error=str(exc)),ensure_ascii=False),flush=True)
        return 2


if __name__=='__main__': sys.exit(main())
