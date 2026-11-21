"""Known-model noisy recovery, fixed offset, free offset and a rank-deficient probe."""
import argparse
from collections import defaultdict
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator
from source_data.common import read_json, write_outputs
from .additive import ADDITIVE_MODEL, evaluate_additive
from .engine import MODEL, evaluate
from .fitting import _libraries, fit_parameters
from .generation_fitting import _stats
from .generation_robustness import describe
from .model import ModelError, _finite
from .recovery import recovery_model, independent_forward
from .residuals import diagnose_residuals
from .structure import digest


def validate_config(config):
    _finite(config)
    schema=json.loads(Path(__file__).with_name('recovery_extended_schema.json').read_text(encoding='utf-8'))
    errors=list(Draft202012Validator(schema).iter_errors(config))
    if errors: raise ModelError('扩展恢复配置不符合契约：'+errors[0].message)
    low,high=config['delta_bounds']
    if low>=high or 0 not in config['noise_std']:
        raise ModelError('须保留σ=0基线，且δ下界严格小于上界')
    ids=[c['id'] for c in config['cases']]
    if len(ids)!=len(set(ids)): raise ModelError('恢复案例编号重复')
    if any(not low<=c['delta']<=high for c in [*config['cases'],config['probe']]):
        raise ModelError('生成δ真值须位于声明的联合拟合界限内')
    probe=config['probe']
    if probe['alpha']!=[0,0] or probe['input_upper']+max(probe['beta'])>=100:
        raise ModelError('不可辨识探针须α=0且全部CoD候选严格低于100')


def solver_settings(config,mode,delta):
    settings=dict(format_version='1.0',id='recovery_'+mode,basis='人工真值仅供生成与比较，不作起点；验证集不参与优化。',
        model=MODEL if mode=='base' else ADDITIVE_MODEL,solver='L-BFGS-B',seed=config['solver_seed'],
        starts=config['starts'],max_nfev=config['max_nfev'],delta_policy='excluded_for_base_check')
    if mode!='base':
        settings['output_policy']='no_clipping'
        if mode=='fixed_delta': settings.update(delta_policy='per_receiver_fixed',delta_values={'R':delta})
        elif mode=='free_delta': settings.update(delta_policy='per_receiver_bounded',delta_bounds={'R':config['delta_bounds'][:]})
        else: raise ModelError('未知恢复模式')
    return settings


def truth_coverage(inputs,truth,mode,delta_span,np):
    jac=[]; counts=dict(sod=0,cod_A=0,cod_B=0,ties=0)
    for a,b in inputs:
        _,sod,ca,cb=independent_forward(a,b,truth['alpha'],truth['beta'])
        row=[0.,0.,0.,0.]; smooth=True
        if sod<min(ca,cb)-1e-7:
            counts['sod']+=1; row[:2]=[-(100-a)/2,-(100-b)/2]
        elif ca<min(sod,cb)-1e-7: counts['cod_A']+=1; row[2]=100
        elif cb<min(sod,ca)-1e-7: counts['cod_B']+=1; row[3]=100
        else: counts['ties']+=1; smooth=False
        if mode=='free_delta': row.append(delta_span if smooth else 0.)
        jac.append(row)
    singular=np.linalg.svd(np.asarray(jac),compute_uv=False)
    rank=int(np.sum(singular>max(1e-7,float(singular[0])*1e-7)))
    return dict(counts=counts,rank=rank,parameter_count=4+int(mode=='free_delta'),global_uniqueness_established=False)


def fit_one(config,truth,mode,rep,sigma,inputs,splits,z,*,probe=False):
    np,_,_=_libraries()
    delta=0 if mode=='base' else truth['delta']
    model=recovery_model(('probe' if probe else truth['id'])+'_'+mode)
    settings=solver_settings(config,mode,delta)
    forward=deepcopy(model)
    for e,a,b in zip(forward['edges'],truth['alpha'],truth['beta']): e.update(alpha=a,beta=b)
    extension=dict(model=ADDITIVE_MODEL,basis='人工已知δ，不是原文参数',delta_values={'R':delta},output_policy='no_clipping')
    clean=[]; observed=[]; maximum=0.
    for a,b in inputs:
        y=independent_forward(float(a),float(b),truth['alpha'],truth['beta'])[0]+delta
        scenario=dict(id='independent_forward_check',basis='独立标量公式与核心实现对照',node_values=dict(A=float(a),B=float(b)))
        core=evaluate(forward,scenario) if mode=='base' else evaluate_additive(forward,scenario,extension)
        value=next(n['value'] for n in core['nodes'] if n['id']=='R')
        maximum=max(maximum,abs(y-value))
        if not 0<=y<=100: raise ModelError('生成真值超出SE范围；不裁剪或重新抽样来掩盖配置问题')
        clean.append(y)
    observed=np.clip(np.asarray(clean)+sigma*z,0,100).tolist()
    samples=dict(format_version='1.0',id=model['id']+f'_r{rep}_s{sigma}',data_kind='method_check',
        basis='独立标量公式生成；观测噪声后裁剪；固定δ与联合δ使用相同目标。',case_sha256=digest(model),records=[
            dict(id=f'rep{rep}:base{i}',split=split,node_values=dict(A=float(a),B=float(b),R=float(y)))
            for i,((a,b),split,y) in enumerate(zip(inputs,splits,observed))])
    fit=fit_parameters(model,samples,settings)
    audited=diagnose_residuals(model,fit)
    row=fit['receivers'][0]
    comparison=[]
    for i,p in enumerate(row.get('parameters',[])):
        for key in ('alpha','beta'):
            known=truth[key][i]; error=p[key]-known
            comparison.append(dict(feeder=p['feeder'],parameter=key,known=known,fitted=p[key],
                absolute_error=abs(error),relative_error=abs(error)/abs(known) if known else None,estimated=True))
    if mode!='base' and row.get('delta') is not None:
        comparison.append(dict(feeder='',parameter='delta',known=delta,fitted=row['delta'],
            absolute_error=abs(row['delta']-delta),relative_error=abs(row['delta']-delta)/abs(delta) if delta else None,
            estimated=mode=='free_delta'))
    points=[]; metrics=[]
    for split in ('train','holdout'):
        lookup={p['id']:p for p in (row.get('metrics',{}).get(split) or {}).get('rows',[])}
        for i,sample in enumerate(samples['records']):
            if sample['split']!=split or sample['id'] not in lookup: continue
            points.append(dict(id=sample['id'],split=split,A=float(inputs[i][0]),B=float(inputs[i][1]),
                actual_clean=clean[i],actual_observed=observed[i],predicted=lookup[sample['id']]['predicted'],
                observation_clipped=not 0<=clean[i]+sigma*float(z[i])<=100))
        selected=[p for p in points if p['split']==split]
        if selected:
            for target in ('clean','observed'):
                metrics.append(dict(split=split,target=target,
                    outside_se_range=sum(not 0<=p['predicted']<=100 for p in selected),
                    **_stats([p['actual_'+target] for p in selected],[p['predicted'] for p in selected])))
    training=[inputs[i] for i,s in enumerate(splits) if s=='train']
    coverage=truth_coverage(training,truth,mode,config['delta_bounds'][1]-config['delta_bounds'][0],np)
    errors={k:max((p['absolute_error'] for p in comparison if p['parameter']==k),default=None) for k in ('alpha','beta','delta')}
    criteria=None
    if sigma==0:
        clean_metric=next((m for m in metrics if m['split']=='holdout' and m['target']=='clean'),None)
        criteria=dict(formula_equivalent=maximum<=config['acceptance']['forward_absolute_error'],
            solver_converged=fit['status']=='complete',
            truth_rank_full=coverage['rank']==coverage['parameter_count'],
            fitted_rank_full=row.get('sensitivity',{}).get('local_jacobian_rank')==coverage['parameter_count'],
            alpha_recovered=errors['alpha'] is not None and errors['alpha']<=config['acceptance']['alpha_absolute_error'],
            beta_recovered=errors['beta'] is not None and errors['beta']<=config['acceptance']['beta_absolute_error'],
            delta_recovered=mode!='free_delta' or (errors['delta'] is not None and errors['delta']<=config['acceptance']['beta_absolute_error']),
            clean_prediction=clean_metric is not None and clean_metric['rmse']<=config['acceptance']['prediction_rmse'])
    sums=[]
    if probe and row.get('delta') is not None:
        sums=[dict(feeder=p['feeder'],known_beta_plus_delta=truth['beta'][i]+delta,
                   fitted_beta_plus_delta=p['beta']+row['delta']) for i,p in enumerate(row['parameters'])]
    return dict(case='identifiability_probe' if probe else truth['id'],mode=mode,repetition=rep,noise_sigma=sigma,
        status=fit['status'],truth=deepcopy(truth),generation_delta=delta,model=model,fit=fit,
        residual_audit=dict(status=audited['status'],fit_report_sha256=audited['fit_report_sha256'],
            series=[dict(split=s['split'],metrics=s['metrics']) for s in audited['series']]),
        max_forward_error=maximum,truth_coverage=coverage,parameter_comparison=comparison,
        maximum_parameter_errors=errors,noiseless_criteria=criteria,metrics=metrics,points=points,
        identifiable_combinations=sums,
        interpretation='rank_deficient_parameter_recovery_not_required' if probe else
            'noiseless_recovery_passed' if criteria is not None and all(criteria.values()) else
            'noiseless_recovery_needs_review' if criteria is not None else 'noisy_estimation_not_exact_recovery')


def summarize(jobs,np):
    groups=defaultdict(list)
    for job in jobs: groups[job['mode'],job['case'],job['noise_sigma']].append(job)
    summaries=[]
    for (mode,case,sigma),rows in sorted(groups.items()):
        completed=[r for r in rows if r['status']=='complete']
        for name in ('clean_rmse','observed_rmse','alpha_error','beta_error','delta_error'):
            if name=='delta_error' and mode!='free_delta': continue
            if name.endswith('rmse'):
                target=name.split('_')[0]
                values=[next(m['rmse'] for m in r['metrics'] if m['split']=='holdout' and m['target']==target) for r in completed]
            else: values=[r['maximum_parameter_errors'][name.split('_')[0]] for r in completed]
            quantiles=dict(zip(('p5','p50','p95'),map(float,np.percentile(values,[5,50,95])))) if values else dict(p5=None,p50=None,p95=None)
            summaries.append(dict(mode=mode,case=case,noise_sigma=sigma,quantity=name,
                expected_repeats=len(rows),**describe(values),**quantiles))
    return summaries


def run_extended_recovery(config,*,progress=None):
    validate_config(config); np,_,_=_libraries()
    jobs=[]
    for rep,seed in enumerate(np.random.SeedSequence(config['seed']).spawn(config['repetitions']),1):
        streams=seed.spawn(3)
        train=np.random.default_rng(streams[0]).uniform(0,100,(config['random_train'],2))
        holdout=np.random.default_rng(streams[1]).uniform(0,100,(config['random_holdout'],2))
        inputs=np.concatenate([train,holdout]); splits=['train']*len(train)+['holdout']*len(holdout)
        z=np.random.default_rng(streams[2]).standard_normal(len(inputs))
        for truth in config['cases']:
            for sigma in config['noise_std']:
                for mode in ('base','fixed_delta','free_delta'):
                    job=fit_one(config,truth,mode,rep,sigma,inputs,splits,z)
                    jobs.append(job)
                    if progress is not None: progress(dict(completed=len(jobs),repetition=rep,case=truth['id'],noise_sigma=sigma,mode=mode,status=job['status']))
    # Independent, intentionally unidentifiable fixture: only CoD branches are active.
    probe=deepcopy(config['probe']); rng=np.random.default_rng(np.random.SeedSequence([config['seed'],917]))
    inputs=rng.uniform(0,probe['input_upper'],(config['random_train']+config['random_holdout'],2))
    splits=['train']*config['random_train']+['holdout']*config['random_holdout']
    diagnostic=fit_one(config,probe,'free_delta',1,0,inputs,splits,np.zeros(len(inputs)),probe=True)
    regular_ok=all(j['status']=='complete' and (j['noiseless_criteria'] is None or all(j['noiseless_criteria'].values())) for j in jobs)
    metric=next(m for m in diagnostic['metrics'] if m['split']=='holdout' and m['target']=='clean')
    probe_ok=diagnostic['status']=='complete' and diagnostic['truth_coverage']['rank']<5 and metric['rmse']<=config['acceptance']['prediction_rmse']
    return dict(format_version='1.0',stage='stage1_extended_noise_and_delta',status='complete' if regular_ok and probe_ok else 'blocked',
        config_snapshot=deepcopy(config),input_sha256=digest(config),jobs=jobs,identifiability_probe=diagnostic,
        probe_expected_behavior_passed=probe_ok,summary=summarize(jobs,np),
        counts=dict(expected_jobs=len(config['cases'])*len(config['noise_std'])*3*config['repetitions'],
            converged_jobs=sum(j['status']=='complete' for j in jobs),
            noiseless_jobs=sum(j['noise_sigma']==0 for j in jobs),
            noiseless_recovery_passed=sum(j['noiseless_criteria'] is not None and all(j['noiseless_criteria'].values()) for j in jobs)),
        policies=dict(truth_alpha_beta_used_for_initialization=False,fixed_delta_is_generation_truth=True,
            joint_delta_truth_used_for_initialization=False,holdout_used_for_optimization=False,
            fixed_and_free_delta_share_samples=True,noise_pairing='shared_standard_normal_scaled_by_sigma',
            clean_generation_clipping='forbidden_out_of_range_rejected',observed_target_clipping='0_to_100',prediction_clipping='none',
            optimizer_seed='fixed_across_repeats',truth_used_only_for_generation_comparison_and_fixed_delta=True),
        limits=['这是已知模型的程序与可辨识性检查，不是独立场景的δ改善实验，也不是原文参数复现',
                '基础模式生成δ=0的数据，固定/联合δ模式生成相同的非零δ数据，不将不同目标上的误差当作模型优劣对照',
                '含噪声组报告估计误差，不要求参数精确恢复；观测裁剪可能引入偏移',
                '均值、样本标准差及经验分位数不是置信区间，局部满秩不证明全局唯一'])


def csv_outputs(report):
    tables={'recovery_summary.csv':report['summary'],
        'recovery_metrics.csv':[dict(case=j['case'],mode=j['mode'],repetition=j['repetition'],noise_sigma=j['noise_sigma'],status=j['status'],**m) for j in report['jobs'] for m in j['metrics']],
        'parameter_errors.csv':[dict(case=j['case'],mode=j['mode'],repetition=j['repetition'],noise_sigma=j['noise_sigma'],**p) for j in report['jobs'] for p in j['parameter_comparison']],
        'convergence.csv':[dict(case=j['case'],mode=j['mode'],repetition=j['repetition'],noise_sigma=j['noise_sigma'],
            status=j['status'],interpretation=j['interpretation'],rank=j['fit']['receivers'][0].get('sensitivity',{}).get('local_jacobian_rank'),
            parameter_count=j['truth_coverage']['parameter_count']) for j in report['jobs']]}
    outputs={}
    for name,rows in tables.items():
        stream=io.StringIO(newline=''); writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows); outputs[name]='\ufeff'+stream.getvalue()
    return outputs


def render_summary(report):
    c=report['counts']; cfg=report['config_snapshot']
    lines=['# 第一阶段补充 含噪声与δ参数恢复','',
        f"{c['converged_jobs']}/{c['expected_jobs']}组求解收敛；无噪声恢复{c['noiseless_recovery_passed']}/{c['noiseless_jobs']}组通过预设数值检查。",'',
        f"三组人工真值，{cfg['repetitions']}次独立数据重复，噪声σ={cfg['noise_std']}。每次使用{cfg['random_train']}训练记录、{cfg['random_holdout']}独立验证记录，{cfg['starts']}个中性/随机起点；α、β和联合估计δ的真值均不作为起点。",'',
        'base：生成δ=0的数据并拟合α、β。fixed_delta：用人工非零δ生成数据，将δ固定为该真值，仅拟合α、β。free_delta：使用与fixed_delta完全相同的数据，联合拟合α、β、δ。后两组可以检查已知偏移是否帮助恢复参数，但不能据不同生成目标的误差比较基础式与扩展式的实际适用性。','',
        '| 模式 | 真值组 | σ | 指标 | 成功次数 | 均值 | 样本SD | P5 | P50 | P95 |',
        '| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    def show(v): return '未定义' if v is None else f'{v:.5g}'
    for r in report['summary']:
        lines.append(f"| {r['mode']} | {r['case']} | {r['noise_sigma']} | {r['quantity']} | {r['n']}/{r['expected_repeats']} | "+' | '.join(show(r[k]) for k in ('mean','sd','p5','p50','p95'))+' |')
    p=report['identifiability_probe']; row=p['fit']['receivers'][0]
    metric=next(m for m in p['metrics'] if m['split']=='holdout' and m['target']=='clean')
    lines+=['','## 参数不可辨识的检查例','',
        f"另设α₁=α₂=0、β=(20,35)、δ=-5，两个上游在[0,{cfg['probe']['input_upper']}]抽样，使CoD始终控制输出。验证干净RMSE={metric['rmse']:.5g}，真值处局部秩={p['truth_coverage']['rank']}/5，拟合处局部秩={row['sensitivity']['local_jacobian_rank']}/5。",'',
        '该例不要求分别恢复全部参数：α在当前输入区间内可不活跃，β和δ还可发生补偿。应检查β+δ等可识别组合及预测结果，不能因参数单点不同就判程序错误。该检查也不证明其他输入范围内的预测一致。','',
        '## 读图与范围','',
        'recovery_noise.png：横轴为观测噪声σ；各行分别显示干净验证目标RMSE、最大α绝对误差、最大β绝对误差；三列分别为三组真值。线为10次数据重复均值，阴影为样本标准差。固定δ由配置给定，不报告其恢复误差。',
        'delta_recovery.png：联合估计δ的均值±样本标准差，与人工真值虚线比较；右图展示不可辨识例中的β、δ单点与β+δ组合。所有参数真值只用于生成或事后比较，不读取验证表现选择参数。',
        '干净输出越界时停止，不通过裁剪或重抽样隐藏问题；仅观测目标加噪后裁剪到[0,100]，拟合预测不裁剪。点级裁剪标记保存在完整JSON，训练与验证、干净与观测目标分别评估。','',*report['limits'],'']
    return '\n'.join(lines)


def plot_results(report):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib import rc_context
    modes=[('base','基础式恢复','#367AA8'),('fixed_delta','固定真值δ','#32986B'),('free_delta','联合估计δ','#D78C36')]
    outputs={}; cases=report['config_snapshot']['cases']
    with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':9}):
        fig=Figure(figsize=(4.3*len(cases),9.4),layout='constrained'); FigureCanvasAgg(fig)
        axes=fig.subplots(3,len(cases),squeeze=False)
        for col,truth in enumerate(cases):
            for axis,(quantity,label) in zip(axes[:,col],[('clean_rmse','干净验证目标 RMSE'),('alpha_error','最大α绝对误差'),('beta_error','最大β绝对误差')]):
                for mode,legend,color in modes:
                    rows=sorted((r for r in report['summary'] if r['case']==truth['id'] and r['mode']==mode and r['quantity']==quantity and r['mean'] is not None),key=lambda r:r['noise_sigma'])
                    axis.plot([r['noise_sigma'] for r in rows],[r['mean'] for r in rows],'o-',label=legend,color=color)
                    shade=[r for r in rows if r['sd'] is not None]
                    axis.fill_between([r['noise_sigma'] for r in shade],[r['mean']-r['sd'] for r in shade],[r['mean']+r['sd'] for r in shade],alpha=.13,color=color)
                axis.set(title=truth['id'],xlabel='观测噪声σ（SE点）',ylabel=label,xticks=report['config_snapshot']['noise_std'])
                axis.grid(alpha=.2); axis.legend(fontsize=8)
        fig.suptitle('已知模型参数恢复（均值 ± 样本标准差；基础式与非零δ组的生成目标不同）')
        for ext in ('png','svg'):
            stream=io.BytesIO(); fig.savefig(stream,format=ext,dpi=200); outputs['recovery_noise.'+ext]=stream.getvalue()
        fig=Figure(figsize=(12,4.8),layout='constrained'); FigureCanvasAgg(fig); left,right=fig.subplots(1,2)
        for truth in cases:
            jobs=[j for j in report['jobs'] if j['case']==truth['id'] and j['mode']=='free_delta' and j['status']=='complete']
            sigmas=report['config_snapshot']['noise_std']; rows=[]
            for sigma in sigmas:
                values=[j['fit']['receivers'][0]['delta'] for j in jobs if j['noise_sigma']==sigma]
                rows.append(describe(values))
            trace=left.errorbar(sigmas,[r['mean'] for r in rows],yerr=[r['sd'] or 0 for r in rows],marker='o',capsize=3,label=truth['id'])
            left.axhline(truth['delta'],color=trace.lines[0].get_color(),linestyle='--',alpha=.45)
        left.set(title='联合估计δ：同色虚线为人工真值',xlabel='观测噪声σ（SE点）',ylabel='δ估计值',xticks=report['config_snapshot']['noise_std']); left.legend(); left.grid(alpha=.2)
        probe=report['identifiability_probe']; row=probe['fit']['receivers'][0]
        labels=['βA','βB','δ','βA+δ','βB+δ']
        known=probe['truth']['beta']+[probe['truth']['delta']]+[r['known_beta_plus_delta'] for r in probe['identifiable_combinations']]
        fitted=[p['beta'] for p in row['parameters']]+[row['delta']]+[r['fitted_beta_plus_delta'] for r in probe['identifiable_combinations']]
        x=list(range(5)); right.scatter(x,known,label='人工真值',marker='o',s=55,facecolors='none',edgecolors='#367AA8')
        right.scatter(x,fitted,label='拟合值',marker='x',s=50,color='#D78C36')
        right.set(xticks=x,xticklabels=labels,title='不可辨识例：单点与可识别组合',ylabel='SE点'); right.legend(); right.grid(alpha=.2)
        for ext in ('png','svg'):
            stream=io.BytesIO(); fig.savefig(stream,format=ext,dpi=200); outputs['delta_recovery.'+ext]=stream.getvalue()
    return outputs


def main(argv=None):
    parser=argparse.ArgumentParser(description='第一阶段含噪声、固定δ和联合δ恢复检查')
    parser.add_argument('--config',type=Path,required=True); parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--plot',action='store_true'); args=parser.parse_args(argv)
    try:
        if args.output_dir.exists() and any(args.output_dir.iterdir()): raise ModelError('结果目录须为空，避免覆盖基线')
        def progress(row):
            if row['completed']%12==0: print(json.dumps(row,ensure_ascii=False),flush=True)
        report=run_extended_recovery(read_json(args.config),progress=progress)
        outputs={'recovery_report.json':json.dumps(report,ensure_ascii=False,allow_nan=False),
            '恢复实验说明.md':render_summary(report),**csv_outputs(report)}
        if args.plot: outputs.update(plot_results(report))
        write_outputs(args.output_dir,outputs,protected=[args.config])
        print(json.dumps(dict(status=report['status'],counts=report['counts'],probe_ok=report['probe_expected_behavior_passed']),ensure_ascii=False),flush=True)
        return 0 if report['status']=='complete' else 1
    except (ValueError,OSError,TypeError,KeyError,ImportError) as exc:
        print(json.dumps(dict(status='invalid',error=str(exc)),ensure_ascii=False),flush=True); return 2


if __name__=='__main__': sys.exit(main())
