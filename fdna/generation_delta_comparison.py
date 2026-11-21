"""Frozen-data baseline, prespecified fixed delta and fitted delta comparison."""
import argparse
from copy import deepcopy
import io
import json
from pathlib import Path
import sys

from source_data.common import read_json, write_outputs
from .engine import evaluate
from .generation_fitting import (fit_joint_network, make_fitting_model, project_local_samples,
    _stats, _validate_config, fitting_csvs)
from .model import ModelError, _finite
from .residuals import diagnose_residuals
from .structure import digest


def audit_frozen_baseline(generation,baseline,selection):
    _finite(baseline)
    if baseline['status']!='complete' or baseline['generation_report_sha256']!=digest(generation):
        raise ModelError('基础拟合基线未完成或与冻结生成数据不一致')
    if baseline['config_snapshot']['settings']['model']!='fdna_base_eq1_eq2':
        raise ModelError('对照基线须为基础FDNA')
    expected={(g,s,r) for g in selection['groups'] for s in selection['noise_std'] for r in selection['repetitions']}
    jobs=[j for j in baseline['jobs'] if (j['group'],j['noise_sigma'],j['repetition']) in expected]
    if len(jobs)!=len(expected) or len({(j['group'],j['noise_sigma'],j['repetition']) for j in jobs})!=len(expected):
        raise ModelError('基线缺少或重复选定的实验分组')
    full=make_fitting_model(generation['network_snapshot']); result=[]
    for job in jobs:
        if job['status']!='complete': raise ModelError('选定基线分组未完成')
        rows=[r for r in generation['observations'] if (r['group'],r['noise_sigma'],r['repetition'])==
              (job['group'],job['noise_sigma'],job['repetition'])]
        model=deepcopy(full); local_predictions={}; params={}
        if (len(job['local_fits'])!=len(generation['topology']['receivers']) or
            {l['receiver'] for l in job['local_fits']}!=set(generation['topology']['receivers'])):
            raise ModelError('基线局部拟合未完整覆盖接受节点')
        for local in job['local_fits']:
            expected_model,expected_samples=project_local_samples(full,rows,local['receiver'])
            fit=local['fit']
            if (fit['status']!='complete' or digest(expected_model)!=digest(local['model']) or
                    digest(expected_samples)!=digest(fit['samples_snapshot']) or
                    fit['settings_snapshot']!=baseline['config_snapshot']['settings']):
                raise ModelError('基线样本、设置或模型与冻结数据不一致')
            diagnose_residuals(local['model'],fit)
            receiver=fit['receivers'][0]
            params.update({(p['feeder'],local['receiver']):p for p in receiver['parameters']})
            local_predictions[local['receiver']]={p['id']:p['predicted'] for split in ('train','holdout') for p in receiver['metrics'][split]['rows']}
        for edge in model['edges']:
            param=params[edge['feeder'],edge['receiver']]; edge.update(alpha=param['alpha'],beta=param['beta'])
        points=[]
        for row in rows:
            scenario=dict(id=row['observation_id'],basis='核验冻结基线；仅输入根节点',
                node_values={n:row['node_values_clean'][n] for n in generation['topology']['roots']})
            predicted={n['id']:n['value'] for n in evaluate(model,scenario)['nodes']}
            for n in generation['topology']['receivers']:
                points.append(dict(observation_id=row['observation_id'],node=n,split=row['split'],
                    actual_clean=row['node_values_clean'][n],actual_observed=row['node_values_observed'][n],
                    local_prediction=local_predictions[n][row['observation_id']],recursive_prediction=predicted[n]))
        result.append(dict(group=job['group'],noise_sigma=job['noise_sigma'],repetition=job['repetition'],
            status='complete',points=points,local_fits=job['local_fits']))
    return result


def run_comparison(generation,baseline,config):
    if set(config)!= {'format_version','id','basis','groups','noise_std','repetitions','fixed_delta','free_delta_bounds'} or config['format_version']!='1.0':
        raise ModelError('δ对照配置须明确选择、固定值及自由界限')
    _finite(config)
    selection=dict(format_version='1.0',id=config['id'],basis=config['basis'],groups=config['groups'],
        noise_std=config['noise_std'],repetitions=config['repetitions'],settings=baseline['config_snapshot']['settings'])
    _validate_config(selection)
    if len(config['noise_std'])!=1 or len(config['repetitions'])!=1:
        raise ModelError('首轮对照只选择一个噪声档位和一次重复，不混合汇总')
    receivers=generation['topology']['receivers']
    if (set(config['fixed_delta'])!=set(receivers) or set(config['free_delta_bounds'])!=set(receivers) or
        any(isinstance(v,bool) or not isinstance(v,(int,float)) for v in config['fixed_delta'].values())):
        raise ModelError('固定δ和自由界限须恰好覆盖全部接受节点')
    base_jobs=audit_frozen_baseline(generation,baseline,config)
    models={'base':base_jobs}; fits={}
    for mode in ('fixed_delta','free_delta'):
        settings=deepcopy(baseline['config_snapshot']['settings'])
        settings.update(model='fdna_additive_delta',output_policy='no_clipping')
        if mode=='fixed_delta': settings.update(delta_policy='per_receiver_fixed',delta_values=deepcopy(config['fixed_delta']))
        else: settings.update(delta_policy='per_receiver_bounded',delta_bounds=deepcopy(config['free_delta_bounds']))
        selection=dict(format_version='1.0',id=config['id']+'_'+mode,basis=config['basis'],
            groups=config['groups'],noise_std=config['noise_std'],repetitions=config['repetitions'],settings=settings)
        fit=fit_joint_network(generation,selection,allow_additive=True)
        fits[mode]=fit; models[mode]=fit['jobs']
    metrics=[]; decomposition=[]; changes=[]; parameter_changes=[]
    base_lookup={(j['group'],j['noise_sigma'],j['repetition']):j for j in base_jobs}
    for mode,jobs in models.items():
        for job in jobs:
            if job['status']!='complete': continue
            if mode!='base':
                before=base_lookup[job['group'],job['noise_sigma'],job['repetition']]
                params={(l['receiver'],p['feeder']):p for l in before['local_fits'] for p in l['fit']['receivers'][0]['parameters']}
                for local in job['local_fits']:
                    row=local['fit']['receivers'][0]
                    for p in row['parameters']:
                        prior=params[local['receiver'],p['feeder']]
                        parameter_changes.append(dict(model=mode,group=job['group'],noise_sigma=job['noise_sigma'],
                            repetition=job['repetition'],node=local['receiver'],feeder=p['feeder'],
                            baseline_alpha=prior['alpha'],alpha=p['alpha'],baseline_beta=prior['beta'],beta=p['beta'],
                            delta=row['delta'],local_rank=row['sensitivity']['local_jacobian_rank'],
                            parameter_count=row['sensitivity']['parameter_count']))
            for node in receivers:
                for split in ('train','holdout'):
                    points=[p for p in job['points'] if p['node']==node and p['split']==split]
                    for target in ('clean','observed'):
                        for prediction in ('local','recursive'):
                            metrics.append(dict(model=mode,group=job['group'],noise_sigma=job['noise_sigma'],repetition=job['repetition'],
                                node=node,split=split,target=target,prediction=prediction,
                                outside_se_range=sum(not 0<=p[prediction+'_prediction']<=100 for p in points),
                                **_stats([p['actual_'+target] for p in points],[p[prediction+'_prediction'] for p in points])))
                    el=[p['local_prediction']-p['actual_clean'] for p in points]
                    shift=[p['recursive_prediction']-p['local_prediction'] for p in points]
                    local_mse=sum(e*e for e in el)/len(el); shift_mse=sum(d*d for d in shift)/len(shift)
                    cross=2*sum(e*d for e,d in zip(el,shift))/len(el)
                    recursive_mse=sum((e+d)**2 for e,d in zip(el,shift))/len(el)
                    decomposition.append(dict(model=mode,group=job['group'],noise_sigma=job['noise_sigma'],repetition=job['repetition'],
                        node=node,split=split,count=len(el),local_mse=local_mse,input_replacement_shift_mse=shift_mse,
                        cross_term=cross,recursive_mse=recursive_mse,identity_error=abs(recursive_mse-local_mse-shift_mse-cross)))
    baseline_metric={(m['group'],m['noise_sigma'],m['repetition'],m['node'],m['split'],m['target'],m['prediction']):m for m in metrics if m['model']=='base'}
    for metric in metrics:
        if metric['model']=='base': continue
        key=tuple(metric[k] for k in ('group','noise_sigma','repetition','node','split','target','prediction'))
        before=baseline_metric[key]
        changes.append({**metric,'baseline_rmse':before['rmse'],'rmse_change':metric['rmse']-before['rmse'],
                        'baseline_mae':before['mae'],'mae_change':metric['mae']-before['mae']})
    status='complete' if all(f['status']=='complete' for f in fits.values()) else 'blocked'
    return dict(format_version='1.0',stage='stage2_delta_comparison',status=status,config_snapshot=deepcopy(config),
        generation_sha256=digest(generation),baseline_fit_sha256=digest(baseline),fits=fits,
        baseline_jobs_audited=len(base_jobs),metrics=metrics,changes=changes,error_decomposition=decomposition,parameter_changes=parameter_changes,
        policies=dict(baseline_refitted=False,generator_changed=False,split_changed=False,holdout_used_for_selection=False,
            fixed_delta_is_truth=False,prediction_clipping='none',old_base_lower_bound_applied_to_delta=False),
        limits=['固定δ为预先规定的对照值，不是独立场景或原文真值；自由δ界限为实验设定',
                '不裁剪预测，超出0—100另行计数；基础式下界不直接用于含δ结果',
                'MSE恒等式描述同一模型输入替换后的预测变化及交叉项，不是因果误差归因',
                '首轮只对照选定条件与重复，不能代替全部噪声重复或实际任务有效性验证'])


def render_summary(report):
    lines=['# 独立综合场景 首轮δ对照','',
        '使用冻结的生成数据和训练/验证划分，基础式参数不重新拟合；核验原拟合记录后重新计算预测。固定δ与联合估计δ均使用相同训练样本拟合α、β。','',
        '固定δ配置：'+json.dumps(report['config_snapshot']['fixed_delta'],ensure_ascii=False),
        '自由δ界限：'+json.dumps(report['config_snapshot']['free_delta_bounds'],ensure_ascii=False),'',
        '| 条件 | 模型 | 节点 | 训练局部RMSE | 验证局部RMSE | 验证整网RMSE | 验证整网MAE |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: |']
    keys=sorted({(m['group'],m['model'],m['node']) for m in report['metrics']})
    for group,mode,node in keys:
        rows=[m for m in report['metrics'] if m['group']==group and m['model']==mode and m['node']==node and m['target']=='clean']
        # First-round config deliberately selects one repetition and noise level.
        local=next(m for m in rows if m['split']=='holdout' and m['prediction']=='local')
        recursive=next(m for m in rows if m['split']=='holdout' and m['prediction']=='recursive')
        train=next(m for m in rows if m['split']=='train' and m['prediction']=='local')
        lines.append(f"| {group} | {mode} | {node} | {train['rmse']:.4f} | {local['rmse']:.4f} | {recursive['rmse']:.4f} | {recursive['mae']:.4f} |")
    lines+=['','## 参数与边界','']
    for mode,fit in report['fits'].items():
        for job in fit['jobs']:
            for local in job['local_fits']:
                row=local['fit']['receivers'][0]
                if row.get('delta') is None: continue
                s=row['sensitivity']
                bounds=row.get('delta_bounds'); boundary=''
                if bounds and abs(row['delta']-bounds[0])<1e-6: boundary='，触及预设下界'
                elif bounds and abs(row['delta']-bounds[1])<1e-6: boundary='，触及预设上界'
                lines.append(f"- {job['group']} / {mode} / {local['receiver']}：δ={row['delta']:.5g}{boundary}，局部秩{s['local_jacobian_rank']}/{s['parameter_count']}，不活跃参数：{', '.join(s['inactive_parameters']) or '无'}。")
    lines+=['','## 如何解释','',
        '比较独立验证误差，不能只看训练误差下降。增加δ后应同时检查参数变化、是否触及界限、局部敏感度和多起点差异；改善不代表唯一参数或真实任务有效性。',
        'error_decomposition.csv使用逐样本恒等式：e整网=e局部+Δ预测，因此MSE整网=MSE局部+mean(Δ预测²)+2mean(e局部×Δ预测)。交叉项可正可负，不能把两个RMSE直接相减当成上游传播误差。',
        '本轮只选择σ=0、重复1作初步对照；不依据验证结果改变固定δ或界限。后续重复实验需保留同一协议或另行明确配置。','',*report['limits'],'']
    return '\n'.join(lines)


def plot_comparison(report):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib import rc_context
    groups=report['config_snapshot']['groups']; nodes=['T','Q','R']; outputs={}
    with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10}):
        fig=Figure(figsize=(6*len(groups),4.8),layout='constrained'); FigureCanvasAgg(fig)
        axes=fig.subplots(1,len(groups),squeeze=False,sharey=True)[0]
        for ax,group in zip(axes,groups):
            for offset,(mode,label,color) in zip((-.25,0,.25),[('base','基础式','#367AA8'),('fixed_delta','预设固定δ','#32986B'),('free_delta','联合估计δ','#D78C36')]):
                values=[next(m['rmse'] for m in report['metrics'] if m['group']==group and m['model']==mode and m['node']==n
                    and m['split']=='holdout' and m['target']=='clean' and m['prediction']=='recursive') for n in nodes]
                ax.bar([i+offset for i in range(3)],values,width=.25,label=label,color=color)
            ax.set(title=group,xticks=range(3),xticklabels=nodes,xlabel='接受节点',ylabel='验证整网 RMSE（SE点）')
            ax.grid(axis='y',alpha=.2); ax.set_axisbelow(True); ax.legend()
        fig.suptitle('冻结数据的首轮δ对照（无噪声，重复1）')
        for ext in ('png','svg'):
            stream=io.BytesIO(); fig.savefig(stream,format=ext,dpi=200); outputs['delta_comparison.'+ext]=stream.getvalue()
    return outputs


def main(argv=None):
    parser=argparse.ArgumentParser(description='独立综合场景的冻结基线δ对照')
    for flag in ('generation-report','baseline-fit','config','output-dir'): parser.add_argument('--'+flag,type=Path,required=True)
    parser.add_argument('--plot',action='store_true'); args=parser.parse_args(argv)
    try:
        if args.output_dir.exists() and any(args.output_dir.iterdir()): raise ModelError('输出目录须为空')
        result=run_comparison(read_json(args.generation_report),read_json(args.baseline_fit),read_json(args.config))
        # Reuse CSV encoder on generic row tables.
        import csv
        outputs={'delta_comparison_report.json':json.dumps(result,ensure_ascii=False,allow_nan=False),'首轮δ对照说明.md':render_summary(result)}
        for name in ('metrics','changes','error_decomposition','parameter_changes'):
            stream=io.StringIO(newline=''); rows=result[name]
            if rows:
                writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
            outputs[name+'.csv']='\ufeff'+stream.getvalue()
        for mode,fit in result['fits'].items():
            outputs.update({mode+'_'+name:value for name,value in fitting_csvs(fit).items()})
        if args.plot and result['status']=='complete': outputs.update(plot_comparison(result))
        write_outputs(args.output_dir,outputs,protected=[args.generation_report,args.baseline_fit,args.config])
        print(json.dumps(dict(status=result['status']),ensure_ascii=False)); return 0 if result['status']=='complete' else 1
    except (ValueError,OSError,KeyError,TypeError,ImportError) as exc:
        print(json.dumps(dict(status='invalid',error=str(exc)),ensure_ascii=False)); return 2


if __name__=='__main__': sys.exit(main())
