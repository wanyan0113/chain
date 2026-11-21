"""Audit frozen predictions, residuals, near-loss starts and feasible null directions."""
import argparse
from collections import defaultdict
from copy import deepcopy
import csv
import io
import json
import math
from pathlib import Path
import sys

from source_data.common import read_json, write_outputs
from .additive import ADDITIVE_MODEL, evaluate_additive
from .engine import evaluate
from .generation_delta_repeats import distribution
from .generation_fitting import audit_generation, make_fitting_model, project_local_samples, _stats
from .model import ModelError, _finite, validate_model
from .residuals import diagnose_residuals
from .structure import digest


def validate_config(config):
    _finite(config)
    if (set(config)!={'format_version','id','basis','near_training_rmse_tolerance',
                     'prediction_equivalence_tolerance','null_step_fraction','plot_noise_std','illustration'} or
        config['format_version']!='1.0' or any(not isinstance(config[k],str) or not config[k].strip() for k in ('id','basis'))):
        raise ModelError('诊断配置须明确容差、扰动幅度及示例选择')
    for name in ('near_training_rmse_tolerance','prediction_equivalence_tolerance','null_step_fraction'):
        v=config[name]
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not 0<v<=1:
            raise ModelError('诊断容差与步长须在(0,1]内')
    if (not isinstance(config['plot_noise_std'],list) or not config['plot_noise_std'] or
        any(isinstance(v,bool) or not isinstance(v,(int,float)) or v<0 for v in config['plot_noise_std']) or
        len(set(config['plot_noise_std']))!=len(config['plot_noise_std'])):
        raise ModelError('绘图噪声档位须明确且不重复')
    if set(config['illustration'])!={'model','group','noise_sigma','repetition','node'}:
        raise ModelError('近等起点示例须明确模型、条件、噪声、重复与节点')


def predict_physical(parameters,inputs,n,delta_fixed):
    import numpy as np
    delta=parameters[-1] if len(parameters)==2*n+1 else delta_fixed
    return np.minimum(np.mean(100-np.asarray(parameters[:n])*(100-inputs),axis=1),
                      np.min(inputs+np.asarray(parameters[n:2*n]),axis=1))+delta


def training_geometry(fit,config):
    """Select by training loss only; probe a local null direction without optimizing."""
    import numpy as np
    row=fit['receivers'][0]; feeders=row['feeders']; n=len(feeders)
    free=fit['settings_snapshot']['delta_policy']=='per_receiver_bounded'
    delta=row['delta'] if row['delta'] is not None else 0.
    bounds=row['delta_bounds'] if free else [0.,0.]
    span=bounds[1]-bounds[0]
    physical=np.array([p['alpha'] for p in row['parameters']]+[p['beta'] for p in row['parameters']]+([delta] if free else []))
    scale=np.array([1.]*n+[100.]*n+([span] if free else [])); offset=np.array([0.]*(2*n)+([bounds[0]] if free else []))
    scaled=(physical-offset)/scale
    names=['alpha:'+f for f in feeders]+['beta:'+f for f in feeders]+(['delta'] if free else [])
    records=fit['samples_snapshot']['records']
    data={s:np.array([[r['node_values'][f] for f in feeders] for r in records if r['split']==s]) for s in ('train','holdout')}
    train=data['train']; predicted={s:predict_physical(physical,x,n,delta) for s,x in data.items()}
    selected=row['attempt_selection']['index']; loss=row['attempts'][selected]['rmse']
    near=[(i,a) for i,a in enumerate(row['attempts']) if a['success'] and a['rmse']<=loss+config['near_training_rmse_tolerance']]
    starts=[]
    for i,attempt in near:
        values=np.asarray(attempt['parameters'])
        if values.shape!=physical.shape or np.any((values-offset)/scale < -1e-12) or np.any((values-offset)/scale > 1+1e-12):
            raise ModelError('起点结果参数维度或界限无效')
        target=np.asarray([r['node_values'][row['receiver']] for r in records if r['split']=='train'])
        loss_check=float(np.sqrt(np.mean((predict_physical(values,data['train'],n,delta)-target)**2)))
        assert_close(loss_check,attempt['rmse'],'起点训练损失与参数重算不一致')
        differences={s:float(np.max(np.abs(predict_physical(values,x,n,delta)-predicted[s]))) for s,x in data.items()}
        starts.append(dict(attempt=i,training_rmse=attempt['rmse'],parameters=values.tolist(),
                           max_train_prediction_change=differences['train'],max_holdout_prediction_change=differences['holdout']))
    sod=np.mean(100-physical[:n]*(100-train),axis=1); terms=train+physical[n:2*n]; cod=np.min(terms,axis=1)
    smooth_sod=sod<cod-1e-7
    unique=(np.sort(terms,axis=1)[:,1]-cod>1e-7) if n>1 else np.ones(len(train),dtype=bool)
    smooth_cod=(cod<sod-1e-7)&unique
    jac=np.zeros((len(train),len(physical))); jac[smooth_sod,:n]=-(100-train[smooth_sod])/n
    limiting=np.argmin(terms,axis=1)
    for i in range(n): jac[smooth_cod&(limiting==i),n+i]=100
    if free: jac[smooth_sod|smooth_cod,-1]=span
    _,singular,vh=np.linalg.svd(jac,full_matrices=False)
    rank=int(np.sum(singular>(float(singular[0])*1e-7 if len(singular) and singular[0] else 1e-7)))
    inactive=[names[i] for i in range(len(names)) if np.linalg.norm(jac[:,i])<1e-7]
    if rank!=row['sensitivity']['local_jacobian_rank'] or inactive!=row['sensitivity']['inactive_parameters']:
        raise ModelError('训练Jacobian重算与保存的敏感度不一致')
    probe=dict(kind='full_local_rank',feasible=False,step=0.,equivalent_on_sampled_inputs=False,
        max_train_prediction_change=None,max_holdout_prediction_change=None,parameters_before=physical.tolist(),parameters_after=None)
    if rank<len(physical):
        null=vh[rank:]; vector=None
        if free:
            direction=null.T@null[:,-1]
            if np.linalg.norm(direction)>1e-8:
                vector=direction; probe['kind']='delta_compensation_direction'
        if vector is None and inactive:
            vector=np.zeros(len(physical)); vector[names.index(inactive[0])]=1
            probe['kind']='inactive_parameter_direction'
        if vector is None:
            vector=null[0].copy(); probe['kind']='other_local_null_direction'
        vector=vector/np.max(np.abs(vector)); vector[np.abs(vector)<1e-12]=0
        def room(direction):
            return min([(1-scaled[i])/v if v>0 else scaled[i]/(-v) for i,v in enumerate(direction) if v!=0])
        if room(-vector)>room(vector): vector=-vector
        available=max(0.,float(room(vector))); step=min(config['null_step_fraction'],available/2)
        probe.update(step=step,scaled_direction=vector.tolist())
        if step>1e-8:
            candidate=(scaled+step*vector)*scale+offset
            if np.any(scaled+step*vector < -1e-12) or np.any(scaled+step*vector > 1+1e-12):
                raise ModelError('诊断扰动越过声明参数界限')
            changes={s:float(np.max(np.abs(predict_physical(candidate,x,n,delta)-predicted[s]))) for s,x in data.items()}
            probe.update(feasible=True,parameters_after=candidate.tolist(),max_train_prediction_change=changes['train'],
                max_holdout_prediction_change=changes['holdout'],
                equivalent_on_sampled_inputs=max(changes.values())<=config['prediction_equivalence_tolerance'])
    return dict(rank=rank,parameter_count=len(physical),inactive_parameters=inactive,parameter_names=names,
        selected_parameters=physical.tolist(),selected_training_rmse=loss,near_start_count=len(starts),near_starts=starts,
        parameter_spread=(np.ptp(np.asarray([a['parameters'] for a in starts]),axis=0).tolist() if starts else None),null_probe=probe)


def assert_close(value,expected,description):
    if value is None or expected is None:
        if value is not expected: raise ModelError(description)
    elif not math.isclose(value,expected,rel_tol=1e-9,abs_tol=1e-8): raise ModelError(description)


def scalar_network(incoming,root_values,deltas,order):
    """Independent scalar DAG equations on already validated inputs; no clipping."""
    values={n:dict(value=v) for n,v in root_values.items()}
    for node in order:
        if node in values: continue
        edges=incoming[node]
        sod=math.fsum(100-e['alpha']*(100-values[e['feeder']]['value']) for e in edges)/len(edges)
        cod=min(values[e['feeder']]['value']+e['beta'] for e in edges)
        values[node]=dict(value=min(sod,cod)+deltas[node],sod_mean=sod,cod_min=cod)
    return values


def analyze_diagnostics(generation,baseline,comparison,summary,config,*,progress=None):
    validate_config(config)
    if comparison['status']!='complete' or summary['status']!='complete':
        raise ModelError('本诊断入口需要完整的冻结对照结果，失败组须先单独审查')
    if (comparison['generation_sha256']!=digest(generation) or comparison['baseline_fit_sha256']!=digest(baseline)
        or summary['comparison_sha256']!=digest(comparison)):
        raise ModelError('诊断输入版本不一致')
    audit_generation(generation)
    selection=comparison['config_snapshot']
    if set(config['plot_noise_std'])-set(selection['noise_std']): raise ModelError('绘图档位不在冻结结果中')
    expected={(g,s,r) for g in selection['groups'] for s in selection['noise_std'] for r in selection['repetitions']}
    if baseline['status']!='complete' or baseline['config_snapshot']['settings']['model']!='fdna_base_eq1_eq2':
        raise ModelError('冻结基础式未完成或模型类型不一致')
    base_jobs=[j for j in baseline['jobs'] if (j['group'],j['noise_sigma'],j['repetition']) in expected]
    models={'base':base_jobs}
    models.update({mode:comparison['fits'][mode]['jobs'] for mode in ('fixed_delta','free_delta')})
    for jobs in models.values():
        keys={(j['group'],j['noise_sigma'],j['repetition']) for j in jobs}
        if len(keys)!=len(jobs) or keys!=expected: raise ModelError('诊断分组遗漏或重复')
    full=make_fitting_model(generation['network_snapshot']); receivers=generation['topology']['receivers']
    rows_by_job=defaultdict(list)
    for row in generation['observations']: rows_by_job[row['group'],row['noise_sigma'],row['repetition']].append(row)
    original_metrics={tuple(m[k] for k in ('model','group','noise_sigma','repetition','node','split','target','prediction')):m for m in comparison['metrics']}
    if len(original_metrics)!=len(comparison['metrics']): raise ModelError('冻结指标重复')
    all_points=[]; residual_metrics=[]; geometries=[]; decomposition=[]; audit_count=0; core_checks=0
    for mode,jobs in models.items():
        for job in jobs:
            key=job['group'],job['noise_sigma'],job['repetition']; rows=rows_by_job[key]
            if job['status']!='complete': raise ModelError('诊断分组尚未完成')
            context=dict(model=mode,group=key[0],noise_sigma=key[1],repetition=key[2])
            model=deepcopy(full); local_values={}; local_rows={}; deltas={}
            if len(job['local_fits'])!=len(receivers) or {l['receiver'] for l in job['local_fits']}!=set(receivers):
                raise ModelError('局部拟合节点遗漏或重复')
            for local in job['local_fits']:
                node=local['receiver']; fit=local['fit']; record=fit['receivers'][0]
                expected_model,expected_samples=project_local_samples(full,rows,node)
                settings=deepcopy(baseline['config_snapshot']['settings'])
                if mode!='base':
                    settings.update(model=ADDITIVE_MODEL,output_policy='no_clipping')
                    if mode=='fixed_delta': settings.update(delta_policy='per_receiver_fixed',delta_values={node:selection['fixed_delta'][node]})
                    else: settings.update(delta_policy='per_receiver_bounded',delta_bounds={node:selection['free_delta_bounds'][node]})
                if (digest(expected_model)!=digest(local['model']) or digest(expected_samples)!=digest(fit['samples_snapshot'])
                    or settings!=fit['settings_snapshot'] or fit['status']!='complete'):
                    raise ModelError('局部样本或设置与冻结实验不一致')
                diagnose_residuals(local['model'],fit)
                audit_count+=1; local_rows[node]=record; deltas[node]=record['delta'] if mode!='base' else 0.
                local_values[node]={p['id']:p['predicted'] for sp in ('train','holdout') for p in record['metrics'][sp]['rows']}
                params={p['feeder']:p for p in record['parameters']}
                for edge in model['edges']:
                    if edge['receiver']==node: edge.update(alpha=params[edge['feeder']]['alpha'],beta=params[edge['feeder']]['beta'])
                geometries.append(dict(**context,node=node,**training_geometry(fit,config)))
            saved={(p['observation_id'],p['node']):p for p in job['points']}
            if len(saved)!=len(job['points']) or len(saved)!=len(rows)*len(receivers): raise ModelError('冻结预测遗漏或重复')
            extension=dict(model=ADDITIVE_MODEL,basis='诊断重算，沿用冻结δ',delta_values=deltas,output_policy='no_clipping')
            validate_model(model)
            incoming={n:[e for e in model['edges'] if e['receiver']==n] for n in receivers}
            points=[]
            for index,row in enumerate(rows):
                scenario=dict(id=row['observation_id'],basis='诊断重算，仅提供干净根节点',
                    node_values={n:row['node_values_clean'][n] for n in generation['topology']['roots']})
                recursive=scalar_network(incoming,scenario['node_values'],deltas,generation['topology']['order'])
                if index in (0,len(rows)-1):
                    calculated=evaluate(model,scenario) if mode=='base' else evaluate_additive(model,scenario,extension)
                    for n in calculated['nodes']: assert_close(recursive[n['id']]['value'],n['value'],'独立DAG与核心计算不一致')
                    core_checks+=1
                for node in receivers:
                    old=saved[row['observation_id'],node]
                    assert_close(old['actual_clean'],row['node_values_clean'][node],'干净目标与冻结数据不一致')
                    assert_close(old['actual_observed'],row['node_values_observed'][node],'观测目标与冻结数据不一致')
                    parents=[e for e in model['edges'] if e['receiver']==node]
                    for prediction in ('local','recursive'):
                        inputs={e['feeder']:(row['node_values_clean'][e['feeder']] if prediction=='local' else recursive[e['feeder']]['value']) for e in parents}
                        sod=math.fsum(100-e['alpha']*(100-inputs[e['feeder']]) for e in parents)/len(parents)
                        cod=min(inputs[e['feeder']]+e['beta'] for e in parents)
                        before=min(sod,cod); value=before+deltas[node]
                        expected=local_values[node][row['observation_id']] if prediction=='local' else recursive[node]['value']
                        assert_close(value,expected,'独立标量重算与核心预测不一致')
                        assert_close(value,old[prediction+'_prediction'],'保存预测与参数重算不一致')
                        points.append(dict(**context,observation_id=row['observation_id'],node=node,split=row['split'],prediction=prediction,
                            actual_clean=row['node_values_clean'][node],actual_observed=row['node_values_observed'][node],predicted=value,
                            residual_clean=value-row['node_values_clean'][node],residual_observed=value-row['node_values_observed'][node],
                            base_before_delta=before,delta=deltas[node],sod=sod,cod=cod,
                            branch='SoD' if sod<cod-1e-7 else 'CoD' if cod<sod-1e-7 else 'tie',
                            upper_headroom=100-before,outside=not 0<=value<=100))
            for node in receivers:
                for split in ('train','holdout'):
                    for target in ('clean','observed'):
                        for prediction in ('local','recursive'):
                            selected=[p for p in points if p['node']==node and p['split']==split and p['prediction']==prediction]
                            stats=_stats([p['actual_'+target] for p in selected],[p['predicted'] for p in selected])
                            metric_key=mode,*key,node,split,target,prediction
                            if metric_key not in original_metrics: raise ModelError('冻结指标遗漏')
                            for name in stats: assert_close(stats[name],original_metrics[metric_key][name],'逐条残差汇总与冻结指标不一致')
                            residual_metrics.append(dict(**context,node=node,split=split,target=target,prediction=prediction,**stats,
                                positive_fraction=sum(p['residual_'+target]>1e-8 for p in selected)/len(selected),
                                negative_fraction=sum(p['residual_'+target]<-1e-8 for p in selected)/len(selected)))
                    local={p['observation_id']:p for p in points if p['node']==node and p['split']==split and p['prediction']=='local'}
                    downstream=[p for p in points if p['node']==node and p['split']==split and p['prediction']=='recursive']
                    local_mse=math.fsum(local[p['observation_id']]['residual_clean']**2 for p in downstream)/len(downstream)
                    shift_mse=math.fsum((p['predicted']-local[p['observation_id']]['predicted'])**2 for p in downstream)/len(downstream)
                    cross=2*math.fsum(local[p['observation_id']]['residual_clean']*(p['predicted']-local[p['observation_id']]['predicted']) for p in downstream)/len(downstream)
                    recursive_mse=math.fsum(p['residual_clean']**2 for p in downstream)/len(downstream)
                    if abs(recursive_mse-local_mse-shift_mse-cross)>1e-8: raise ModelError('MSE恒等式不成立')
                    decomposition.append(dict(**context,node=node,split=split,local_mse=local_mse,input_shift_mse=shift_mse,
                        cross_term=cross,recursive_mse=recursive_mse))
            all_points.extend(points)
            if progress is not None: progress(dict(**context,audited_local_fits=audit_count))
    if len(residual_metrics)!=len(original_metrics): raise ModelError('残差核验覆盖不完整')
    grouped=defaultdict(list)
    for row in residual_metrics:
        key=tuple(row[k] for k in ('model','group','noise_sigma','node','split','target','prediction'))
        for quantity in ('rmse','mae','mean_residual','positive_fraction','negative_fraction'):
            grouped[key+(quantity,)].append(row[quantity])
    aggregates=[dict(zip(('model','group','noise_sigma','node','split','target','prediction','quantity'),k))|
        distribution(values) for k,values in sorted(grouped.items())]
    illustration=[g for g in geometries if all(g[k]==v for k,v in config['illustration'].items())]
    if len(illustration)!=1: raise ModelError('示例选择与冻结结果不唯一对应')
    outside=[p for p in all_points if p['outside']]
    if len(outside)!=len(summary['outside_predictions']): raise ModelError('越界条目与冻结汇总不一致')
    report=dict(format_version='1.0',stage='stage2_delta_residual_parameter_diagnostics',status='complete',
        config_snapshot=deepcopy(config),comparison_config_snapshot=deepcopy(selection),
        input_sha256=digest(dict(generation=digest(generation),baseline=digest(baseline),comparison=digest(comparison),summary=digest(summary),config=config)),
        counts=dict(audited_local_fits=audit_count,core_network_spot_checks=core_checks,recomputed_prediction_rows=len(all_points),verified_metric_rows=len(residual_metrics),
            outside_prediction_rows=len(outside)),residual_summary=aggregates,residual_metrics=residual_metrics,
        geometries=geometries,error_decomposition=decomposition,illustration=illustration[0],outside_predictions=outside,
        policies=dict(refitted=False,clipping=False,delta_bounds_changed=False,near_starts_selected_by='training_rmse_only',
            null_step_selected_by='prespecified_fraction_and_parameter_bounds_not_holdout',
            uncertainty='data_repeat_sample_sd_not_confidence_interval'),
        limits=['残差图汇集重复内验证条目，仅作分布描述，不将配对条目当成独立实验',
                '近等训练损失不保证所有输入上的预测等价；留出预测差单独报告，不用于筛选起点',
                '局部Jacobian零方向可能受参数边界或分支切换限制；只报告预设扰动实际产生的变化',
                '诊断仅覆盖输入报告明确选择的条件和网络；不由当前诊断推断其他条件或网络实验已完成'])
    return report,all_points


def csv_table(rows,fieldnames=None):
    stream=io.StringIO(newline='')
    if rows or fieldnames:
        writer=csv.DictWriter(stream,fieldnames=fieldnames or list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    return '\ufeff'+stream.getvalue()


def csv_outputs(report,points):
    geometry=[]; starts=[]; probes=[]
    for row in report['geometries']:
        head={k:row[k] for k in ('model','group','noise_sigma','repetition','node')}
        probe=row['null_probe']
        geometry.append(dict(**head,rank=row['rank'],parameter_count=row['parameter_count'],
            inactive_parameters=','.join(row['inactive_parameters']),near_start_count=row['near_start_count'],
            max_near_train_prediction_change=max((r['max_train_prediction_change'] for r in row['near_starts']),default=None),
            max_near_holdout_prediction_change=max((r['max_holdout_prediction_change'] for r in row['near_starts']),default=None),
            null_kind=probe['kind'],null_feasible=probe['feasible'],null_equivalent=probe['equivalent_on_sampled_inputs']))
        for attempt in row['near_starts']:
            for name,value in zip(row['parameter_names'],attempt['parameters']):
                starts.append(dict(**head,attempt=attempt['attempt'],parameter=name,value=value,
                    training_rmse=attempt['training_rmse'],max_train_prediction_change=attempt['max_train_prediction_change'],
                    max_holdout_prediction_change=attempt['max_holdout_prediction_change']))
        for i,name in enumerate(row['parameter_names']):
            probes.append(dict(**head,parameter=name,before=probe['parameters_before'][i],
                after=probe['parameters_after'][i] if probe['parameters_after'] is not None else None,
                kind=probe['kind'],feasible=probe['feasible'],equivalent=probe['equivalent_on_sampled_inputs'],
                step=probe['step'],max_train_prediction_change=probe['max_train_prediction_change'],
                max_holdout_prediction_change=probe['max_holdout_prediction_change']))
    return {'residual_points.csv':csv_table(points),'residual_repeat_metrics.csv':csv_table(report['residual_metrics']),
        'residual_summary.csv':csv_table(report['residual_summary']),'parameter_geometry.csv':csv_table(geometry),
        'near_start_parameters.csv':csv_table(starts),'null_direction_probes.csv':csv_table(probes),
        'outside_predictions.csv':csv_table(report['outside_predictions'],list(points[0])),
        'error_decomposition.csv':csv_table(report['error_decomposition'])}


def render_summary(report):
    lines=['# δ对照的残差 参数敏感度与越界诊断','',
        '本轮只核验和分析冻结结果，不重新拟合，不裁剪预测，也不调整δ界限。残差定义为预测值减目标值：正值表示高估，负值表示低估；干净目标和观测目标分别分析。','',
        '核验规模：'+json.dumps(report['counts'],ensure_ascii=False),'',
        '## 无噪声验证残差','',
        '| 条件 | 模型 | 节点 | 局部平均残差 | 整网平均残差 | 整网RMSE |',
        '| --- | --- | --- | ---: | ---: | ---: |']
    groups=report['comparison_config_snapshot']['groups']
    for group in groups:
        for mode in ('base','fixed_delta','free_delta'):
            for node in ('T','Q','R'):
                chosen=[r for r in report['residual_summary'] if r['group']==group and r['model']==mode and r['node']==node
                    and r['noise_sigma']==0 and r['split']=='holdout' and r['target']=='clean']
                values={(r['prediction'],r['quantity']):r for r in chosen}
                lines.append(f"| {group} | {mode} | {node} | {values['local','mean_residual']['mean']:.4f} | {values['recursive','mean_residual']['mean']:.4f} | {values['recursive','rmse']['mean']:.4f} |")
    g=report['geometries']; deficient=[r for r in g if r['rank']<r['parameter_count']]
    feasible=[r for r in g if r['null_probe']['feasible']]
    equivalent=[r for r in feasible if r['null_probe']['equivalent_on_sampled_inputs']]
    compensation=[r for r in equivalent if r['null_probe']['kind']=='delta_compensation_direction']
    example=report['illustration']
    lines+=['','## 参数诊断怎样理解','',
        f"{len(g)}次局部拟合中，{len(deficient)}次局部秩不足；预先规定的参数范围内扰动有{len(feasible)}次可行，其中{len(equivalent)}次在当前训练和验证输入上的预测变化均不超过{report['config_snapshot']['prediction_equivalence_tolerance']}。其中{len(compensation)}次属于含δ分量的补偿方向，其余须按不活跃参数或其他零方向解释。",'',
        '不活跃参数表示这些输入下改变该参数暂不影响控制分支；参数补偿则涉及多个参数共同变化。局部秩不足也可能受到边界限制，不能把所有秩不足都叫δ与β补偿，更不能据此断言所有任务下都不可辨识。',
        '零方向扰动按预设步长和参数界限决定，不按验证表现缩小步长；若分支切换使预测变化，照实记录。当前输入上的等价不保证其他输入范围内也等价。',
        '近等起点只按训练RMSE差筛选，示例：'+json.dumps(report['config_snapshot']['illustration'],ensure_ascii=False)+
        f"，局部秩{example['rank']}/{example['parameter_count']}，合格收敛起点{example['near_start_count']}个。参数跨度不是置信区间，验证预测差仅用于事后诊断。",'',
        '## 越界的精确条件','',
        '若加δ前输出为B，则最终输出为B+δ。超过100当且仅当δ>100−B；低于0当且仅当δ<−B。upper_headroom记录100−B，便于逐条复算。正δ可以改善部分残差，同时带来上界风险；不自动裁剪，因为裁剪会改变模型。',
        'outside_predictions.csv保留逐条编号、分支、SoD、CoD、δ及加项前输出。T的局部与递推输入相同，相同预测分别登记，不按独立观测重复计数。','',
        '## 图表阅读','',
        'residual_vs_prediction_sigma0.png：横轴为预测SE，纵轴为对干净目标的残差，颜色表示模型。持续偏离零线提示系统性高估或低估。',
        'residual_vs_prediction_sigma8.png：高噪声时对观测目标的残差，不能直接与无噪声图的残差散布作纯模型误差比较。',
        'residual_distribution_sigma0.png：无噪声验证残差的经验累计分布。横轴为残差，纵轴为不大于该残差的条目比例；不是正态性检验。',
        'near_start_parameter_ranges.png：预先指定例子中，近等训练损失起点的参数最小—最大跨度和选中参数。α、β、δ分开用各自单位展示。',
        'upper_range_condition.png：不额外衰减组自由δ模型T节点的递推验证预测，横轴为加项前上限余量，纵轴为δ；位于δ=余量对角线上方即超过100。右侧放大图保留所有可能越界的位置。',
        '图中汇集10次重复条目作描述，噪声版本及重复内数据并非独立实验。正式稳定性比较仍以08包的重复级配对统计为准。','',*report['limits'],'']
    return '\n'.join(lines)


def plot_diagnostics(report,points):
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib import rc_context
    groups=report['comparison_config_snapshot']['groups']; nodes=['T','Q','R']
    modes=[('base','基础式','#367AA8'),('fixed_delta','固定δ','#32986B'),('free_delta','自由δ','#D78C36')]
    outputs={}
    lookup=defaultdict(list)
    for p in points:
        if p['split']=='holdout' and p['prediction']=='recursive': lookup[p['group'],p['node'],p['noise_sigma'],p['model']].append(p)
    def save(fig,name):
        for ext in ('png','svg'):
            stream=io.BytesIO(); fig.savefig(stream,format=ext,dpi=180); outputs[name+'.'+ext]=stream.getvalue()
    with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':9}):
        for sigma in report['config_snapshot']['plot_noise_std']:
            target='clean' if sigma==0 else 'observed'
            fig=Figure(figsize=(13,7.2),layout='constrained'); FigureCanvasAgg(fig); axes=fig.subplots(len(groups),3,squeeze=False)
            for i,g in enumerate(groups):
                for j,n in enumerate(nodes):
                    ax=axes[i,j]
                    for mode,label,color in modes:
                        chosen=lookup[g,n,sigma,mode]
                        ax.scatter([p['predicted'] for p in chosen],[p['residual_'+target] for p in chosen],
                                   s=8,alpha=.3,color=color,label=label,rasterized=True)
                    ax.axhline(0,color='#555555',linestyle='--',linewidth=1)
                    ax.set(title=g+' / '+n,xlabel='预测SE',ylabel='残差（预测−'+('干净值' if target=='clean' else '观测值')+'）')
                    ax.grid(alpha=.15); ax.legend(fontsize=8)
            fig.suptitle(f'独立验证残差与预测值 σ={sigma}（汇集重复条目，仅作分布描述）')
            save(fig,'residual_vs_prediction_sigma'+str(sigma))
        fig=Figure(figsize=(13,7),layout='constrained'); FigureCanvasAgg(fig); axes=fig.subplots(len(groups),3,squeeze=False)
        for i,g in enumerate(groups):
            for j,n in enumerate(nodes):
                ax=axes[i,j]
                for mode,label,color in modes:
                    values=sorted(p['residual_clean'] for p in lookup[g,n,0,mode])
                    ax.step(values,np.arange(1,len(values)+1)/len(values),where='post',label=label,color=color)
                ax.axvline(0,color='#555555',linestyle='--',linewidth=1)
                ax.set(title=g+' / '+n,xlabel='干净目标残差（SE点）',ylabel='经验累计比例',ylim=(0,1))
                ax.grid(alpha=.15); ax.legend(fontsize=8)
        fig.suptitle('无噪声验证残差分布（经验累计分布，非正态性检验）'); save(fig,'residual_distribution_sigma0')
        ex=report['illustration']; array=np.asarray([r['parameters'] for r in ex['near_starts']])
        fig=Figure(figsize=(11,4.5),layout='constrained'); FigureCanvasAgg(fig); axes=fig.subplots(1,3)
        for ax,prefix in zip(axes,('alpha:','beta:','delta')):
            indexes=[i for i,name in enumerate(ex['parameter_names']) if name.startswith(prefix)]
            for k,index in enumerate(indexes):
                ax.vlines(k,float(array[:,index].min()),float(array[:,index].max()),color='#D78C36',linewidth=5,label='近等损失起点跨度' if k==0 else None)
                ax.scatter(k,ex['selected_parameters'][index],color='#222222',marker='x',s=60,zorder=3,label='选中参数' if k==0 else None)
            ax.set(xticks=range(len(indexes)),xticklabels=[ex['parameter_names'][i] for i in indexes],ylabel='参数值',title=prefix.rstrip(':'))
            if prefix=='alpha:': ax.set_ylim(0,1)
            elif prefix=='beta:': ax.set_ylim(0,100)
            else: ax.set_ylim(*report['comparison_config_snapshot']['free_delta_bounds'][ex['node']])
            ax.grid(alpha=.15); ax.legend(fontsize=8)
        fig.suptitle(f"近等训练损失起点的参数分散：{ex['group']} / {ex['node']} / 重复{ex['repetition']}（跨度非置信区间）")
        save(fig,'near_start_parameter_ranges')
        chosen=[p for p in points if p['model']=='free_delta' and p['group']=='unit_conditions' and p['node']=='T'
                and p['split']=='holdout' and p['prediction']=='recursive']
        fig=Figure(figsize=(11,4.6),layout='constrained'); FigureCanvasAgg(fig); axes=fig.subplots(1,2)
        for ax,limit in zip(axes,(100,15)):
            for outside,label,color in [(False,'范围内','#8295A5'),(True,'超过100','#CB4545')]:
                rows=[p for p in chosen if p['outside']==outside]
                ax.scatter([p['upper_headroom'] for p in rows],[p['delta'] for p in rows],s=12,alpha=.45,color=color,label=label,rasterized=True)
            ax.plot([0,limit],[0,limit],'--',color='#333333',linewidth=1,label='δ=上限余量')
            ax.set(xlim=(0,limit),ylim=(-.5,10.5),xlabel='上限余量 100−加δ前输出',ylabel='δ（SE点）',title='全范围' if limit==100 else '上限附近放大')
            ax.grid(alpha=.15); ax.legend(fontsize=8)
        fig.suptitle(f"自由δ的上界条件：T验证条目{len(chosen)}条，超过100共{sum(p['outside'] for p in chosen)}条（噪声版本配对）")
        save(fig,'upper_range_condition')
    return outputs


def main(argv=None):
    parser=argparse.ArgumentParser(description='冻结δ对照的残差、参数几何及越界诊断；不重新拟合')
    for name in ('generation-report','baseline-fit','comparison-report','summary-report','config','output-dir'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--plot',action='store_true'); args=parser.parse_args(argv)
    try:
        if args.output_dir.exists() and any(args.output_dir.iterdir()): raise ModelError('诊断输出目录须为空')
        def progress(row):
            if row['audited_local_fits']%30==0: print(json.dumps(row,ensure_ascii=False),flush=True)
        report,points=analyze_diagnostics(read_json(args.generation_report),read_json(args.baseline_fit),
            read_json(args.comparison_report),read_json(args.summary_report),read_json(args.config),progress=progress)
        outputs={'diagnostics_report.json':json.dumps(report,ensure_ascii=False,allow_nan=False),
                 '残差参数与越界诊断说明.md':render_summary(report),**csv_outputs(report,points)}
        if args.plot: outputs.update(plot_diagnostics(report,points))
        write_outputs(args.output_dir,outputs,protected=[args.generation_report,args.baseline_fit,args.comparison_report,args.summary_report,args.config])
        print(json.dumps(dict(status=report['status'],counts=report['counts']),ensure_ascii=False),flush=True); return 0
    except (ValueError,OSError,TypeError,KeyError,ImportError) as exc:
        print(json.dumps(dict(status='invalid',error=str(exc)),ensure_ascii=False),flush=True); return 2


if __name__=='__main__': sys.exit(main())
