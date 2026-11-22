"""Prespecified 3/5-input recovery and deliberately rank-deficient validation."""
import argparse
from copy import deepcopy
from pathlib import Path
import math
import numpy as np

from .engine import evaluate
from .additive import evaluate_additive, ADDITIVE_MODEL
from .fitting import fit_parameters
from .generation_delta_diagnostics import training_geometry
from .generation_fitting import _stats
from .model import ModelError
from .structure import digest
from .workflow_common import make_model,settings,scalar_forward,parameter_flags,csv_text,write_bundle,dumps


def validate(config):
    if config['input_sizes']!=[3,5] or config['repetitions']<1 or min(config['train_count'],config['holdout_count'])<16:
        raise ModelError('核心验证须覆盖3/5输入并保留训练与留出样本')
    if len(config['alpha'])!=5 or len(config['beta'])!=5 or not 0 in config['noise_std']:
        raise ModelError('真值维度或无噪声对照缺失')
    if any(not 0<a<1 for a in config['alpha']) or any(not 0<b<100 for b in config['beta']):
        raise ModelError('恢复组采用内点真值')
    for key in ('train_count','holdout_count','starts','repetitions','max_nfev'):
        if isinstance(config[key],bool) or not isinstance(config[key],int) or config[key]<1: raise ModelError('非法整数配置')
    lo,hi=config['delta_bounds']
    if not lo<config['delta']<hi or not all(0<=s<=8 for s in config['noise_std']): raise ModelError('非法δ或噪声配置')


def input_design(rng,count,n,case,design):
    if case=='diagnostic': return rng.uniform(*design['probe'],(count,n))
    values=rng.uniform(*design['high'],(count,n))
    # Balanced high-all and isolated low feeder blocks; independent coordinates.
    for i in range(count):
        block=i%(n+1)
        if block: values[i,block-1]=rng.uniform(*design['single_low'])
    return values[rng.permutation(count)]


def truth_geometry(inputs,alpha,beta,free,span):
    n=len(alpha); jac=[]; counts={'sod':0,'ties':0,**{f'cod:F{i+1}':0 for i in range(n)}}
    for x in inputs:
        sod=math.fsum(a*v+100*(1-a) for a,v in zip(alpha,x))/n
        cod=np.asarray(x)+beta; order=np.argsort(cod); row=[0.]*(2*n+int(free))
        if sod<cod[order[0]]-1e-7:
            row[:n]=[-(100-v)/n for v in x]; counts['sod']+=1
        elif cod[order[0]]<sod-1e-7 and cod[order[1]]-cod[order[0]]>1e-7:
            row[n+order[0]]=100.;counts[f'cod:F{order[0]+1}']+=1
        else:
            counts['ties']+=1;jac.append(row);continue
        if free: row[-1]=span
        jac.append(row)
    singular=np.linalg.svd(jac,compute_uv=False)
    rank=int(np.sum(singular>max(1e-7,float(singular[0])*1e-7)))
    return dict(rank=rank,parameter_count=2*n+int(free),branches=counts,global_uniqueness_established=False)


def run(config,progress=None):
    validate(config); jobs=[]
    for n in config['input_sizes']:
        feeders=[f'F{i+1}' for i in range(n)]
        network=dict(id=f'recovery_{n}',nodes=feeders+['R'],edges=[dict(feeder=f,receiver='R') for f in feeders])
        for case in ('recovery','diagnostic'):
            alpha=config['alpha'][:n] if case=='recovery' else [0.]*n
            beta=config['beta'][:n]
            for rep,seed in enumerate(np.random.SeedSequence([config['seed'],n,int(case=='diagnostic')]).spawn(config['repetitions']),1):
                streams=seed.spawn(3)
                train=input_design(np.random.default_rng(streams[0]),config['train_count'],n,case,config['design'])
                holdout=input_design(np.random.default_rng(streams[1]),config['holdout_count'],n,case,config['design'])
                inputs=np.concatenate([train,holdout]); z=np.random.default_rng(streams[2]).standard_normal(len(inputs))
                for sigma in config['noise_std']:
                    for mode in ('base','fixed_delta','free_delta'):
                        model=make_model(network); delta=0. if mode=='base' else config['delta']
                        clean=[scalar_forward(list(map(float,x)),alpha,beta,delta) for x in inputs]
                        if any(not 0<=y<=100 for y in clean): raise ModelError('干净真值越界，不裁剪或重抽样')
                        observed=np.clip(np.asarray(clean)+sigma*z,0,100)
                        records=[dict(id=f'r{rep}:b{i}',split='train' if i<len(train) else 'holdout',
                            node_values={**dict(zip(feeders,map(float,x))),'R':float(y)}) for i,(x,y) in enumerate(zip(inputs,observed))]
                        samples=dict(format_version='1.0',id=f'{case}_{n}_{rep}_{sigma}_{mode}',data_kind='method_check',
                            basis='独立标量公式；同一重复与噪声版本共用输入及划分；真值不作初值。',case_sha256=digest(model),records=records)
                        setting=settings(mode,['R'],seed=config['solver_seed'],starts=config['starts'],max_nfev=config['max_nfev'],delta=config['delta'],delta_bounds=config['delta_bounds'])
                        fit=fit_parameters(model,samples,setting); row=fit['receivers'][0]
                        true_model=deepcopy(model)
                        for edge,a,b in zip(true_model['edges'],alpha,beta): edge.update(alpha=a,beta=b)
                        forward_error=0.
                        # Check every generated record against the independent core engine.
                        for x,y in zip(inputs,clean):
                            scene=dict(id='scalar_crosscheck',basis='独立标量与核心逐节点公式核对',node_values=dict(zip(feeders,map(float,x))))
                            core=evaluate(true_model,scene) if mode=='base' else evaluate_additive(true_model,scene,dict(model=ADDITIVE_MODEL,basis='人工δ',delta_values={'R':delta},output_policy='no_clipping'))
                            forward_error=max(forward_error,abs(next(v['value'] for v in core['nodes'] if v['id']=='R')-y))
                        points=[];metrics=[]
                        for split in ('train','holdout'):
                            pred={r['id']:r['predicted'] for r in (row.get('metrics',{}).get(split) or {}).get('rows',[])}
                            for i,r in enumerate(records):
                                if r['split']==split and r['id'] in pred:
                                    points.append(dict(id=r['id'],split=split,inputs=dict(zip(feeders,map(float,inputs[i]))),clean=clean[i],observed=float(observed[i]),predicted=pred[r['id']]))
                            chosen=[p for p in points if p['split']==split]
                            if chosen:
                                for target in ('clean','observed'):
                                    metrics.append(dict(split=split,target=target,**_stats([p[target] for p in chosen],[p['predicted'] for p in chosen])))
                        comparison=[]
                        for i,p in enumerate(row.get('parameters',[])):
                            for name,truth in [('alpha',alpha[i]),('beta',beta[i])]:
                                comparison.append(dict(feeder=p['feeder'],parameter=name,truth=truth,estimate=p[name],absolute_error=abs(p[name]-truth)))
                        if mode=='free_delta': comparison.append(dict(feeder='',parameter='delta',truth=delta,estimate=row['delta'],absolute_error=abs(row['delta']-delta)))
                        geometry=truth_geometry(train,alpha,beta,mode=='free_delta',config['delta_bounds'][1]-config['delta_bounds'][0])
                        policy=dict(near_training_rmse_tolerance=1e-6,prediction_equivalence_tolerance=1e-6,null_step_fraction=.1)
                        diagnostic=training_geometry(fit,policy) if fit['status']=='complete' else None
                        flags=parameter_flags(row)
                        criteria=dict(forward_equivalent=forward_error<=config['acceptance']['forward_absolute_error'],converged=fit['status']=='complete')
                        if sigma==0:
                            prediction_pass=next((m['rmse'] for m in metrics if m['split']=='holdout' and m['target']=='clean'),float('inf'))<=config['acceptance']['prediction_rmse']
                            if case=='recovery':
                                criteria['holdout_prediction']=prediction_pass
                                criteria['truth_local_rank_full']=geometry['rank']==geometry['parameter_count']
                                criteria['fitted_local_rank_full']=row.get('sensitivity',{}).get('local_jacobian_rank')==geometry['parameter_count']
                                for name in ('alpha','beta')+ (('delta',) if mode=='free_delta' else ()):
                                    criteria[name+'_recovered']=max(p['absolute_error'] for p in comparison if p['parameter']==name)<=config['acceptance'][name+'_absolute_error']
                            else:
                                criteria['truth_rank_deficient']=geometry['rank']<geometry['parameter_count']
                                criteria['constraint_warning_emitted']='LOCAL_RANK_DEFICIENT' in flags
                                criteria['prediction_and_probe_reported']=bool(diagnostic and metrics and diagnostic['null_probe']['kind'])
                        job=dict(input_count=n,case=case,repetition=rep,noise_sigma=sigma,mode=mode,truth=dict(alpha=alpha,beta=beta,delta=delta),
                            generation_sha256=digest(dict(inputs=inputs.tolist(),clean=clean,observed=observed.tolist(),splits=[r['split'] for r in records])),
                            fit=fit,truth_geometry=geometry,diagnostic=diagnostic,flags=flags,parameter_comparison=comparison,
                            metrics=metrics,points=points,criteria=criteria,passed=all(criteria.values()),maximum_forward_error=forward_error)
                        jobs.append(job)
                        if progress: progress(dict(job=len(jobs),inputs=n,case=case,mode=mode,sigma=sigma,repetition=rep,passed=job['passed']))
    return dict(stage='multi_input_core_validation',status='complete' if all(j['passed'] for j in jobs) else 'needs_review',
        config_snapshot=deepcopy(config),config_sha256=digest(config),jobs=jobs,
        counts=dict(jobs=len(jobs),passed=sum(j['passed'] for j in jobs),failed=sum(not j['passed'] for j in jobs)),
        limits=['局部满秩和指定案例参数恢复不证明全局唯一或真实任务有效。','含噪组检查估计与收敛，不要求精确恢复。','基础式与含δ模式的生成目标不同，不据此比较模型优劣。','有限扰动仅支持当前训练与留出输入上的近似等价。'])


def outputs(report):
    rows=[]; parameters=[]; metrics=[]; points=[]
    for j in report['jobs']:
        head={k:j[k] for k in ('input_count','case','repetition','noise_sigma','mode')}
        rows.append(dict(**head,passed=j['passed'],criteria=j['criteria'],flags=j['flags'],truth_rank=j['truth_geometry']['rank'],parameter_count=j['truth_geometry']['parameter_count'],fitted_rank=j['fit']['receivers'][0].get('sensitivity',{}).get('local_jacobian_rank')))
        parameters.extend(dict(**head,**p) for p in j['parameter_comparison'])
        metrics.extend(dict(**head,**m) for m in j['metrics'])
        points.extend(dict(**head,**p) for p in j['points'])
    text=['# 3输入与5输入核心拟合验证','',f"状态：{report['status']}；{report['counts']}",'',
        '每种规模均保留恢复组与CoD控制诊断组，采用基础式、已知固定δ和自由δ。含噪组不要求精确参数恢复。',
        f"{report['config_snapshot']['repetitions']}次独立重复、每次{report['config_snapshot']['train_count']}条训练和{report['config_snapshot']['holdout_count']}条留出记录；噪声配置{report['config_snapshot']['noise_std']}，不是全参数空间实验。",
        '真值不传入起点；固定δ为已知偏移自检，和自由δ共用目标；基础式目标的δ为0，不作不同目标上的模型优劣比较。','',
        '| 输入数 | 案例 | 模型 | 无噪声通过/总数 | 含噪通过/总数 |','| --- | --- | --- | ---: | ---: |']
    for n in (3,5):
        for case in ('recovery','diagnostic'):
            for mode in ('base','fixed_delta','free_delta'):
                selected=[j for j in report['jobs'] if (j['input_count'],j['case'],j['mode'])==(n,case,mode)]
                stat=[]
                for sigma in (0,2):
                    sub=[j for j in selected if j['noise_sigma']==sigma];stat.append(f"{sum(j['passed'] for j in sub)}/{len(sub)}")
                text.append(f"| {n} | {case} | {mode} | {stat[0]} | {stat[1]} |")
    text+=['','诊断组验收要求正确告警并报告预测及扰动状态，不要求参数恢复、留出误差为零或单条扰动必然可行。诊断组的低预测误差与参数约束不足可以同时存在。局部满秩、近等多初值或单条有限扰动均不证明全局唯一。完整起点、分支、边界、扰动及逐样本预测保存在JSON与CSV中。']
    return {'report.json':dumps(report),'验收结果.csv':csv_text(rows),'参数恢复.csv':csv_text(parameters),'误差指标.csv':csv_text(metrics),'逐条预测.csv':csv_text(points),'结果说明.md':'\n'.join(text)+'\n'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()): raise ModelError('输出目录非空')
    import json
    report=run(json.loads(args.config.read_text(encoding='utf-8')),lambda p:print(dumps(p).strip(),flush=True))
    write_bundle(args.output_dir,outputs(report));print(dumps(report['counts']))
    return 0 if report['status']=='complete' else 1

if __name__=='__main__': raise SystemExit(main())
