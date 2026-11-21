"""Repeat-level paired delta contrasts with explicit failed-group accounting."""
import argparse
from collections import defaultdict
import csv
import io
import json
from pathlib import Path
import sys

from source_data.common import read_json, write_outputs
from .generation_delta_comparison import run_comparison
from .generation_robustness import describe
from .model import ModelError, _finite
from .structure import digest


def distribution(values):
    result=describe(values)
    if values:
        import numpy as np
        result.update(zip(('p5','p50','p95'),map(float,np.percentile(values,[5,50,95]))))
    else: result.update(p5=None,p50=None,p95=None)
    return result


def summarize_comparison(comparison):
    _finite(comparison)
    config=comparison['config_snapshot']; repeats=config['repetitions']
    expected={(g,s,r) for g in config['groups'] for s in config['noise_std'] for r in repeats}
    if comparison['baseline_jobs_audited']!=len(expected):
        raise ModelError('冻结基线分组计数与重复配置不一致')
    nodes=sorted(comparison['fits']['fixed_delta']['topology']['receivers'])
    if not nodes or len(nodes)!=len(set(nodes)):
        raise ModelError('接受节点清单缺失或重复')
    job_lookup={}; failed=[]; diagnostics=[]; parameters=defaultdict(list); outside_points=[]
    for mode in ('fixed_delta','free_delta'):
        fit=comparison['fits'][mode]
        if sorted(fit['topology']['receivers'])!=nodes:
            raise ModelError('不同δ模型的接受节点清单不一致')
        if fit['generation_report_sha256']!=comparison['generation_sha256']:
            raise ModelError('δ拟合与冻结数据版本不一致')
        jobs={(j['group'],j['noise_sigma'],j['repetition']):j for j in fit['jobs']}
        if len(jobs)!=len(fit['jobs']) or set(jobs)!=expected:
            raise ModelError('δ拟合分组遗漏或重复，不得只汇总成功组')
        if fit['status']!=('complete' if all(j['status']=='complete' for j in jobs.values()) else 'blocked'):
            raise ModelError('δ模型整体状态与分组状态不一致')
        job_lookup[mode]=jobs
        for key,job in jobs.items():
            if job['status'] not in ('complete','blocked'): raise ModelError('不支持的拟合分组状态')
            seen=set()
            for local in job['local_fits']:
                node=local['receiver']; row=local['fit']['receivers'][0]
                if node in seen: raise ModelError('接受节点拟合重复')
                seen.add(node)
                bounds=row.get('delta_bounds'); delta=row.get('delta'); sens=row.get('sensitivity',{})
                selected=row.get('attempt_selection',{}).get('index',0)
                attempt=row.get('attempts',[])[selected] if row.get('attempts') else {}
                diagnostics.append(dict(model=mode,group=key[0],noise_sigma=key[1],repetition=key[2],node=node,
                    job_status=job['status'],fit_status=row['status'],delta=delta,
                    delta_lower=bounds[0] if bounds else None,delta_upper=bounds[1] if bounds else None,
                    at_lower=bool(bounds and delta is not None and abs(delta-bounds[0])<=1e-6),
                    at_upper=bool(bounds and delta is not None and abs(delta-bounds[1])<=1e-6),
                    local_rank=sens.get('local_jacobian_rank'),parameter_count=sens.get('parameter_count'),
                    inactive_parameters=','.join(sens.get('inactive_parameters',[]))))
                if row['status']!='fitted':
                    failed.append(dict(model=mode,group=key[0],noise_sigma=key[1],repetition=key[2],node=node,
                        status=row['status'],message=attempt.get('message','无完整求解记录')))
                if job['status']=='complete':
                    for p in row['parameters']:
                        for name in ('alpha','beta'):
                            parameters[mode,key[0],key[1],node,p['feeder'],name].append(p[name])
            if seen!=set(nodes): raise ModelError('δ拟合未完整覆盖接受节点')
            if job['status']=='complete':
                for point in job['points']:
                    for prediction in ('local','recursive'):
                        value=point[prediction+'_prediction']
                        if not 0<=value<=100:
                            outside_points.append(dict(model=mode,group=key[0],noise_sigma=key[1],repetition=key[2],
                                observation_id=point['observation_id'],node=point['node'],split=point['split'],
                                prediction=prediction,predicted=value,actual_clean=point['actual_clean'],
                                actual_observed=point['actual_observed']))
    status='complete' if all(f['status']=='complete' for f in comparison['fits'].values()) else 'blocked'
    if comparison['status']!=status: raise ModelError('对照整体状态不一致')
    # Validate every complete job, including train/holdout, target and prediction kinds.
    wanted={(n,sp,t,p) for n in nodes for sp in ('train','holdout') for t in ('clean','observed') for p in ('local','recursive')}
    by_job=defaultdict(dict)
    for metric in comparison['metrics']:
        key=metric['model'],metric['group'],metric['noise_sigma'],metric['repetition']
        identity=tuple(metric[k] for k in ('node','split','target','prediction'))
        if metric['model'] not in ('base','fixed_delta','free_delta') or key[1:] not in expected or identity in by_job[key]:
            raise ModelError('指标出现重复或未配置分组')
        sample_count=job_lookup['fixed_delta'][key[1:]]['counts'][metric['split']]
        if metric['count']!=sample_count or not 0<=metric['outside_se_range']<=sample_count:
            raise ModelError('指标样本数或越界计数与冻结划分不一致')
        by_job[key][identity]=metric
    for mode in ('base','fixed_delta','free_delta'):
        for key in expected:
            complete=mode=='base' or job_lookup[mode][key]['status']=='complete'
            rows=by_job[mode,*key]
            if (complete and set(rows)!=wanted) or (not complete and rows):
                raise ModelError('完整分组的指标不完整，或失败分组混入预测指标')
    metric_groups=defaultdict(list); paired_groups=defaultdict(list); ranges=defaultdict(lambda:[0,0])
    for (mode,group,sigma,rep),rows in by_job.items():
        for identity,metric in rows.items():
            node,split,target,prediction=identity
            for quantity in ('rmse','mae','mean_residual','r_squared'):
                value=metric[quantity]
                if value is not None:
                    metric_groups[mode,group,sigma,node,split,target,prediction,quantity].append(value)
                if mode!='base':
                    baseline=by_job['base',group,sigma,rep][identity][quantity]
                    if value is not None and baseline is not None:
                        paired_groups[mode,group,sigma,node,split,target,prediction,quantity].append(value-baseline)
            if target=='clean':
                counts=ranges[mode,group,sigma,node,split,prediction]
                counts[0]+=metric['outside_se_range']; counts[1]+=metric['count']
    metrics=[]; changes=[]
    for mode in ('base','fixed_delta','free_delta'):
        for group in config['groups']:
            for sigma in config['noise_std']:
                for node in nodes:
                    for split in ('train','holdout'):
                        for target in ('clean','observed'):
                            for prediction in ('local','recursive'):
                                for quantity in ('rmse','mae','mean_residual','r_squared'):
                                    key=mode,group,sigma,node,split,target,prediction,quantity
                                    head=dict(zip(('model','group','noise_sigma','node','split','target','prediction','quantity'),key))
                                    metrics.append(dict(**head,expected_repeats=len(repeats),**distribution(metric_groups[key])))
                                    if mode!='base':
                                        values=paired_groups[key]
                                        changes.append(dict(**head,expected_pairs=len(repeats),
                                            decreased_count=sum(v < -1e-10 for v in values),
                                            increased_count=sum(v > 1e-10 for v in values),
                                            **distribution(values)))
    delta_summary=[]
    for mode in ('fixed_delta','free_delta'):
        for group in config['groups']:
            for sigma in config['noise_std']:
                for node in nodes:
                    rows=[r for r in diagnostics if (r['model'],r['group'],r['noise_sigma'],r['node'])==(mode,group,sigma,node)]
                    good=[r for r in rows if r['job_status']=='complete']
                    delta_summary.append(dict(model=mode,group=group,noise_sigma=sigma,node=node,
                        expected_repeats=len(repeats),node_fitted_count=sum(r['fit_status']=='fitted' for r in rows),
                        lower_bound_count=sum(r['at_lower'] for r in good),upper_bound_count=sum(r['at_upper'] for r in good),
                        rank_deficient_count=sum(r['local_rank']<r['parameter_count'] for r in good),
                        **distribution([r['delta'] for r in good])))
    parameter_summary=[dict(model=k[0],group=k[1],noise_sigma=k[2],node=k[3],feeder=k[4],parameter=k[5],
        expected_repeats=len(repeats),**distribution(values)) for k,values in sorted(parameters.items())]
    outside=[dict(model=k[0],group=k[1],noise_sigma=k[2],node=k[3],split=k[4],prediction=k[5],
        outside_count=v[0],count=v[1],outside_fraction=v[0]/v[1] if v[1] else None) for k,v in sorted(ranges.items())]
    if len(outside_points)!=sum(r['outside_count'] for r in outside):
        raise ModelError('逐条越界预测与指标计数不一致')
    decomposition=defaultdict(list)
    for row in comparison['error_decomposition']:
        if row['identity_error']>1e-8: raise ModelError('MSE分解恒等式未通过')
        for quantity in ('local_mse','input_replacement_shift_mse','cross_term','recursive_mse'):
            decomposition[row['model'],row['group'],row['noise_sigma'],row['node'],row['split'],quantity].append(row[quantity])
    decomp=[dict(model=k[0],group=k[1],noise_sigma=k[2],node=k[3],split=k[4],quantity=k[5],
        expected_repeats=len(repeats),**distribution(values)) for k,values in sorted(decomposition.items())]
    counts=dict(baseline_groups=len(expected),new_expected_groups=2*len(expected),
        new_complete_groups=sum(j['status']=='complete' for jobs in job_lookup.values() for j in jobs.values()),
        new_local_fits=sum(len(j['local_fits']) for jobs in job_lookup.values() for j in jobs.values()))
    return dict(format_version='1.0',stage='stage2_delta_paired_repeats',status=status,
        comparison_sha256=digest(comparison),config_snapshot=config,counts=counts,nodes=nodes,
        sample_sizes=[dict(train=a,holdout=b) for a,b in sorted({(j['counts']['train'],j['counts']['holdout'])
            for jobs in job_lookup.values() for j in jobs.values()})],
        metrics=metrics,paired_model_changes=changes,delta_summary=delta_summary,parameter_spread=parameter_summary,
        parameter_diagnostics=diagnostics,failed_fits=failed,prediction_range=outside,outside_predictions=outside_points,error_decomposition=decomp,
        policies=dict(unit='independent_data_repetition',pairing='same_group_noise_repetition_split_and_target',
            failed_groups_excluded_from_means_but_kept_in_denominators=True,
            uncertainty='sample_sd_and_empirical_percentiles_not_confidence_intervals',
            fixed_delta_is_truth=False,delta_bounds_changed=False,prediction_clipping='none',baseline_refitted=False),
        limits=['接受节点观测目标加噪，供给输入及递推根节点仍用干净值；尚未检验输入测量误差',
                '条件作用范围和逐样本抽样方式以对应生成报告为准；本摘要不推断条件的单因素因果作用',
                '参数触界及秩不足须单独解释；预测改善不证明参数唯一或实际任务有效',
                '跨重复标准差和分位数不是置信区间，不将配对版本当作独立实验'])


def summary_csvs(report):
    outputs={}
    for key in ('metrics','paired_model_changes','delta_summary','parameter_spread','parameter_diagnostics',
                'failed_fits','prediction_range','outside_predictions','error_decomposition'):
        rows=report[key]; stream=io.StringIO(newline='')
        if rows:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        outputs[key+'.csv']='\ufeff'+stream.getvalue()
    return outputs


def render_summary(report):
    cfg=report['config_snapshot']; counts=report['counts']; total=len(cfg['repetitions'])
    lines=['# 独立综合场景 δ配对噪声与重复实验','',
        f"核验并复用{counts['baseline_groups']}组冻结基础式结果；新增固定δ/自由δ共{counts['new_complete_groups']}/{counts['new_expected_groups']}组完成，涉及{counts['new_local_fits']}次接受节点拟合。",'',
        f"条件组={cfg['groups']}，σ={cfg['noise_std']}，独立数据重复={total}次。训练/验证网络数量={report['sample_sizes']}；使用相同基础输入、目标和划分作模型配对对照。",
        '固定δ='+json.dumps(cfg['fixed_delta'],ensure_ascii=False)+'；自由δ界限='+json.dumps(cfg['free_delta_bounds'],ensure_ascii=False)+'。均沿用首轮设置，没有依据验证结果改变。','',
        '| 条件 | σ | 节点 | 基础整网RMSE | 固定δ整网RMSE | 自由δ整网RMSE | 自由δ配对变化 | 降低次数/配对数 |',
        '| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |']
    def show(row):
        if row['mean'] is None: return '无结果'
        return f"{row['mean']:.3f} ± {row['sd']:.3f}" if row['sd'] is not None else f"{row['mean']:.3f}（单次）"
    def find(table,mode,group,sigma,node):
        return next(r for r in report[table] if r['model']==mode and r['group']==group and r['noise_sigma']==sigma
            and r['node']==node and r['split']=='holdout' and r['target']=='clean' and r['prediction']=='recursive' and r['quantity']=='rmse')
    for group in cfg['groups']:
        for sigma in cfg['noise_std']:
            for node in ('T','Q','R'):
                values=[find('metrics',m,group,sigma,node) for m in ('base','fixed_delta','free_delta')]
                paired=find('paired_model_changes','free_delta',group,sigma,node)
                lines.append(f"| {group} | {sigma} | {node} | "+' | '.join(show(v) for v in values)+
                    f" | {show(paired)} | {paired['decreased_count']}/{paired['n']} |")
    lines+=['','“±”是独立数据重复的样本标准差。配对变化=同次重复含δRMSE−基础式RMSE，负值表示降低；其SD由逐次差值计算，不是两个SD相减。失败分组不进入均值，但保留在预期分母与failed_fits.csv中。','',
        '## δ与可辨识性','', '| 条件 | σ | 节点 | 自由δ均值±SD | 成功数 | 触下界 | 触上界 | 秩不足 |',
        '| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |']
    for row in report['delta_summary']:
        if row['model']!='free_delta': continue
        lines.append(f"| {row['group']} | {row['noise_sigma']} | {row['node']} | {show(row)} | {row['n']}/{row['expected_repeats']} | {row['lower_bound_count']} | {row['upper_bound_count']} | {row['rank_deficient_count']} |")
    lines+=['','## 图表与数据','',
        'delta_repeat_rmse.png：横轴为观测噪声σ，纵轴为对干净验证目标的整网RMSE；三条线对应基础式、预设固定δ及自由估计δ，阴影为均值±样本标准差。各格纵轴范围不同，优先在同一格内比较。',
        'paired_delta_change.png：固定/自由δ相对同次基础式的RMSE变化，负值表示改善，虚线为无变化；阴影不代表置信区间。',
        'delta_estimates.png：自由δ均值±SD与固定值、预设上下界对照。触界次数见表，不据结果临时扩大界限。',
        'metrics.csv另列训练/验证、干净/观测目标、局部/整网、RMSE/MAE/平均残差/普通R²；parameter_diagnostics.csv保留逐次秩及不活跃参数；parameter_spread.csv是估计参数分布，不是恢复误差。',
        'prediction_range.csv按模型统计越界预测次数，目标类型不重复计数；outside_predictions.csv逐条列出编号和数值，预测没有裁剪。局部和整网预测分别记录，T两种预测相同，不能将相同条目当作独立观测。error_decomposition.csv保留局部MSE、输入替换预测变化平方项及交叉项的跨重复摘要；交叉项可正可负，不将RMSE差解释为传播误差。','',*report['limits'],'']
    return '\n'.join(lines)


def plot_summary(report):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib import rc_context
    groups=report['config_snapshot']['groups']; nodes=['T','Q','R']; outputs={}
    modes=[('base','基础式','#367AA8'),('fixed_delta','预设固定δ','#32986B'),('free_delta','自由估计δ','#D78C36')]
    with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10}):
        for table,name,title,ylabel in [('metrics','delta_repeat_rmse','δ对照的整网验证误差','干净目标 RMSE（SE点）'),
                ('paired_model_changes','paired_delta_change','同次重复相对基础式的变化','配对 ΔRMSE（SE点）')]:
            fig=Figure(figsize=(13,max(7.4,3.5*len(groups))),layout='constrained'); FigureCanvasAgg(fig)
            axes=fig.subplots(len(groups),3,squeeze=False,sharex=True)
            for i,g in enumerate(groups):
                for j,n in enumerate(nodes):
                    ax=axes[i,j]
                    for mode,label,color in modes:
                        if table=='paired_model_changes' and mode=='base': continue
                        rows=sorted((r for r in report[table] if r['model']==mode and r['group']==g and r['node']==n
                            and r['split']=='holdout' and r['target']=='clean' and r['prediction']=='recursive' and r['quantity']=='rmse'
                            and r['mean'] is not None),key=lambda r:r['noise_sigma'])
                        ax.plot([r['noise_sigma'] for r in rows],[r['mean'] for r in rows],'o-',label=label,color=color)
                        shade=[r for r in rows if r['sd'] is not None]
                        ax.fill_between([r['noise_sigma'] for r in shade],[r['mean']-r['sd'] for r in shade],
                            [r['mean']+r['sd'] for r in shade],alpha=.13,color=color)
                    if table=='paired_model_changes': ax.axhline(0,color='#777777',linestyle='--',linewidth=1)
                    ax.set(title=g+' / '+n,xlabel='观测噪声σ（SE点）',ylabel=ylabel,xticks=report['config_snapshot']['noise_std'])
                    ax.grid(alpha=.2); ax.legend(fontsize=8)
            fig.suptitle(title+'（独立数据重复的均值 ± 样本标准差）')
            for ext in ('png','svg'):
                stream=io.BytesIO(); fig.savefig(stream,format=ext,dpi=200); outputs[name+'.'+ext]=stream.getvalue()
        fig=Figure(figsize=(13,max(7.2,3.5*len(groups))),layout='constrained'); FigureCanvasAgg(fig); axes=fig.subplots(len(groups),3,squeeze=False)
        for i,g in enumerate(groups):
            for j,n in enumerate(nodes):
                ax=axes[i,j]; cfg=report['config_snapshot']
                rows=sorted((r for r in report['delta_summary'] if r['model']=='free_delta' and r['group']==g and r['node']==n and r['mean'] is not None),key=lambda r:r['noise_sigma'])
                ax.errorbar([r['noise_sigma'] for r in rows],[r['mean'] for r in rows],yerr=[r['sd'] or 0 for r in rows],marker='o',capsize=3,label='自由δ均值±SD',color='#D78C36')
                for value,label,style,color in [(cfg['fixed_delta'][n],'固定δ','--','#32986B'),
                    (cfg['free_delta_bounds'][n][0],'自由δ下界',':','#777777'),(cfg['free_delta_bounds'][n][1],'自由δ上界',':','#777777')]:
                    ax.axhline(value,label=label,linestyle=style,color=color,linewidth=1)
                ax.set(title=g+' / '+n,xlabel='观测噪声σ（SE点）',ylabel='δ（SE点）',xticks=cfg['noise_std'])
                ax.grid(alpha=.2); ax.legend(fontsize=7)
        fig.suptitle('自由δ估计及预设界限（误差棒为样本标准差）')
        for ext in ('png','svg'):
            stream=io.BytesIO(); fig.savefig(stream,format=ext,dpi=200); outputs['delta_estimates.'+ext]=stream.getvalue()
    return outputs


def main(argv=None):
    parser=argparse.ArgumentParser(description='冻结数据上的δ配对噪声与重复实验')
    for arg in ('generation-report','baseline-fit','config','output-dir'): parser.add_argument('--'+arg,type=Path,required=True)
    parser.add_argument('--plot',action='store_true'); args=parser.parse_args(argv)
    try:
        if args.output_dir.exists() and any(args.output_dir.iterdir()): raise ModelError('结果目录须为空，避免覆盖基线')
        result=run_comparison(read_json(args.generation_report),read_json(args.baseline_fit),read_json(args.config),
            allow_repeats=True,progress=lambda row:print(json.dumps(row,ensure_ascii=False),flush=True))
        report=summarize_comparison(result)
        outputs={'delta_comparison_report.json':json.dumps(result,ensure_ascii=False,allow_nan=False),
            'delta_repeats_report.json':json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),
            'δ噪声与重复实验说明.md':render_summary(report),**summary_csvs(report)}
        if args.plot: outputs.update(plot_summary(report))
        write_outputs(args.output_dir,outputs,protected=[args.generation_report,args.baseline_fit,args.config])
        print(json.dumps(dict(status=report['status'],counts=report['counts']),ensure_ascii=False),flush=True)
        return 0 if report['status']=='complete' else 1
    except (ValueError,OSError,TypeError,KeyError,ImportError) as exc:
        print(json.dumps(dict(status='invalid',error=str(exc)),ensure_ascii=False),flush=True); return 2


if __name__=='__main__': sys.exit(main())
