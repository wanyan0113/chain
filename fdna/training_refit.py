"""Paired training-base-case resampling and actual refits, with fixed original solver settings."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np

from .model import ModelError
from .network_workflow import fit_network,validate_samples
from .structure import digest
from .workflow_common import csv_text,dumps


PROTOCOL=dict(id='flow06_training_cluster_refit_v1',repetitions=50,bootstrap_seed=2026112308,
    resampling_unit='training_base_case_id',pairing='same_draw_across_environments_and_models',replacement=True,
    holdout='unchanged_original_holdout',solver_settings='exact_original_settings_including_starts_seed_and_bounds',
    initialization_from_previous_fit=False,model_selection=False,output_clipping=False,
    summary_statistics=['minimum','median','maximum'],interpretation='finite_training_composition_sensitivity_not_parameter_confidence_interval')


def training_panel(rows):
    splits={};lookup={};groups=sorted({r['fit_group'] for r in rows})
    for row in rows:
        base=row['base_case_id'];split=row['split']
        if split not in ('train','holdout'):raise ModelError('非法划分')
        if base in splits and splits[base]!=split:raise ModelError('基础样本跨训练与留出')
        splits[base]=split;key=row['fit_group'],base
        if key in lookup:raise ModelError('同组基础样本有重复网络观测')
        lookup[key]=row
    train=sorted(b for b,s in splits.items() if s=='train');held=sorted(b for b,s in splits.items() if s=='holdout')
    if len(train)<2 or not held:raise ModelError('需要至少两个训练基础样本和未变的留出样本')
    if set(lookup)!={(g,b) for g in groups for b in train+held}:raise ModelError('训练或留出环境配对面板不完整')
    return train,held,groups,lookup


def sample_training(rows,draw,repetition):
    train,held,groups,lookup=training_panel(rows)
    if len(draw)!=len(train) or any(isinstance(i,bool) or not isinstance(i,(int,np.integer)) or not 0<=i<len(train) for i in draw):
        raise ModelError('训练抽样索引无效')
    result=[]
    for group in groups:
        for position,index in enumerate(draw):
            row=deepcopy(lookup[group,train[index]])
            row['sample_id']=f'refit_{repetition:03d}_draw_{position:03d}_'+row['sample_id']
            result.append(row)
        result.extend(deepcopy(lookup[group,b]) for b in held)
    return result


def compact(result,repetition,protocol_sha256,source_input_sha256):
    fits=[];metrics=[];points=[]
    for job in result['jobs']:
        header=dict(repetition=repetition,fit_group=job['fit_group'],model=job['model'])
        metrics.extend(dict(**header,**m) for m in job['metrics'])
        points.extend(dict(**header,**p) for p in job['points'] if p['split']=='holdout')
        for local in job['local_fits']:
            fit=local.get('fit') or {};row=(fit.get('receivers') or [{}])[0]
            record=dict(**header,node=local['node'],status=local['status'],flags=deepcopy(local['flags']),
                parameters=deepcopy(row.get('parameters',[])),delta=row.get('delta'),delta_bounds=row.get('delta_bounds'),
                sensitivity=deepcopy(row.get('sensitivity',{})),near_best_parameter_spread=deepcopy(row.get('near_best_parameter_spread',{})),
                near_best_start_count=row.get('near_best_start_count'),attempts=deepcopy(row.get('attempts',[])),
                attempt_selection=deepcopy(row.get('attempt_selection')),settings_sha256=digest(fit['settings_snapshot']) if fit else None,
                fit_input_sha256=fit.get('input_sha256'),data_sha256=result['data_sha256'],
                source_input_sha256=source_input_sha256,protocol_sha256=protocol_sha256)
            record['refit_id']='refit_'+digest(record)[:24];fits.append(record)
    return dict(repetition=repetition,input_sha256=result['input_sha256'],data_sha256=result['data_sha256'],
                status=result['status'],fits=fits,metrics=metrics,holdout_points=points)


def compare_control(original,control):
    old={(j['fit_group'],j['model'],l['node']):l for j in original['jobs'] for l in j['local_fits']}
    current={(j['fit_group'],j['model'],l['node']):l for j in control['jobs'] for l in j['local_fits']}
    if set(old)!=set(current):raise ModelError('完整训练控制的拟合任务不一致')
    maximum=0.;parameter_checks=0;prediction_checks=0
    def check(a,b):
        nonlocal maximum
        if a is None or b is None:
            if a is not None or b is not None:raise ModelError('完整训练控制的可用状态不一致')
            return
        if not math.isfinite(a) or not math.isfinite(b):raise ModelError('控制包含非有限值')
        error=abs(a-b);maximum=max(maximum,error)
        if error>1e-8:raise ModelError('完整训练控制未复现原参数或预测，不继续重采样')
    for key in old:
        a,b=old[key],current[key]
        if a['status']!=b['status']:raise ModelError('完整训练控制的拟合状态不一致')
        aa=(a.get('fit') or {}).get('receivers',[{}])[0];bb=(b.get('fit') or {}).get('receivers',[{}])[0]
        left={p['feeder']:p for p in aa.get('parameters',[])};right={p['feeder']:p for p in bb.get('parameters',[])}
        if set(left)!=set(right):raise ModelError('完整训练控制供给映射不一致')
        for feeder in left:
            for name in ('alpha','beta'):check(left[feeder][name],right[feeder][name]);parameter_checks+=1
        check(aa.get('delta'),bb.get('delta'));parameter_checks+=int(aa.get('delta') is not None)
    old_points={(j['fit_group'],j['model'],p['sample_id'],p['node']):p for j in original['jobs'] for p in j['points']}
    new_points={(j['fit_group'],j['model'],p['sample_id'],p['node']):p for j in control['jobs'] for p in j['points']}
    if set(old_points)!=set(new_points):raise ModelError('完整训练控制预测主键不一致')
    for key,a in old_points.items():
        for field in ('local_prediction','recursive_prediction'):
            check(a[field],new_points[key][field]);prediction_checks+=1
    return dict(status='passed',parameter_values_compared=parameter_checks,prediction_values_compared=prediction_checks,max_difference=maximum)


def summarize(fits,metrics,expected_repetitions):
    grouped=defaultdict(list)
    for f in fits:grouped[f['fit_group'],f['model'],f['node']].append(f)
    parameters=[];diagnostics=[]
    for (group,model,node),records in sorted(grouped.items()):
        if len(records)!=expected_repetitions or {r['repetition'] for r in records}!=set(range(1,expected_repetitions+1)):
            raise ModelError('重拟合记录重复或缺少预定重复')
        succeeded=[r for r in records if r['status']=='fitted'];values=defaultdict(list)
        for r in succeeded:
            for p in r['parameters']:
                for kind in ('alpha','beta'):values[kind+':'+p['feeder']].append(p[kind])
            if r['delta'] is not None:values['delta'].append(r['delta'])
        for name,numbers in sorted(values.items()):
            parameters.append(dict(fit_group=group,model=model,node=node,parameter=name,planned=expected_repetitions,
                evaluated=len(numbers),failed=expected_repetitions-len(succeeded),minimum=min(numbers),median=float(np.median(numbers)),maximum=max(numbers)))
        flags=Counter(flag for r in records for flag in r['flags'])
        setting_hashes=sorted({r['settings_sha256'] for r in records if r['settings_sha256'] is not None})
        if len(setting_hashes)>1:raise ModelError('同一条件的求解设置在重采样间变化')
        diagnostics.append(dict(fit_group=group,model=model,node=node,planned=expected_repetitions,fitted=len(succeeded),
            failed=expected_repetitions-len(succeeded),flag_counts=dict(flags),
            settings_sha256=setting_hashes))
    grouped_metrics=defaultdict(list)
    for m in metrics:
        if m['split']=='holdout':grouped_metrics[m['fit_group'],m['model'],m['node'],m['prediction']].append(m)
    prediction=[]
    for key,records in sorted(grouped_metrics.items()):
        if len(records)!=expected_repetitions or {r['repetition'] for r in records}!=set(range(1,expected_repetitions+1)):raise ModelError('留出指标重复或缺失')
        valid=[r for r in records if r['rmse'] is not None and r['evaluated']>0]
        prediction.append(dict(zip(('fit_group','model','node','prediction'),key),planned=expected_repetitions,
            evaluated=len(valid),unavailable=expected_repetitions-len(valid),
            minimum=min((r['rmse'] for r in valid),default=None),median=float(np.median([r['rmse'] for r in valid])) if valid else None,
            maximum=max((r['rmse'] for r in valid),default=None),
            min_evaluated_rows=min((r['evaluated'] for r in valid),default=0),max_evaluated_rows=max((r['evaluated'] for r in valid),default=0),
            outside_predictions=sum(r['outside'] for r in valid)))
    return dict(parameters=parameters,diagnostics=diagnostics,prediction=prediction)


def figures(report,source_fit):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from io import BytesIO
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'svg.fonttype':'path'})
    output={};keys=sorted({(r['fit_group'],r['node']) for r in report['summary']['diagnostics']})
    models=sorted({r['model'] for r in report['summary']['diagnostics']});colors={'base':'#356f9d','free_delta':'#168c87'}
    names={'base':'基础式','free_delta':'自由δ'}
    original={(j['fit_group'],j['model'],l['node']):l['fit']['receivers'][0] for j in source_fit['jobs'] for l in j['local_fits']}
    def save(fig,name):
        for ext in ('png','svg'):
            stream=BytesIO();fig.savefig(stream,format=ext,dpi=200,bbox_inches='tight');output[name+'.'+ext]=stream.getvalue()
        plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(15,8),sharey=True,layout='constrained')
    for ax,kind in zip(axes,('alpha','beta','delta')):
        for i,key in enumerate(keys):
            for mi,model in enumerate(models):
                entry=next((r for r in report['summary']['parameters'] if (r['fit_group'],r['node'],r['model'])==(*key,model)
                            and r['parameter'].split(':')[0]==kind),None)
                if entry is None:continue
                y=i+(mi-.5)*.18;color=colors[model]
                ax.plot([entry['minimum'],entry['maximum']],[y,y],color=color,lw=2);ax.scatter(entry['median'],y,c=color,s=20,zorder=3)
                old=original[key[0],model,key[1]];value=old['delta'] if kind=='delta' else old['parameters'][0][kind]
                ax.scatter(value,y,edgecolors=color,facecolors='white',s=30,zorder=4)
        ax.set(title=kind+'的成功记录范围',yticks=range(len(keys)),yticklabels=[g+' / '+n for g,n in keys],xlabel='参数值')
        ax.grid(axis='x',alpha=.15)
    axes[0].invert_yaxis();fig.suptitle('50次训练成组重拟合：最小值、中位数、最大值\n蓝=基础式，绿=自由δ；实点=中位数，空点=原参数；范围不是置信区间',fontsize=14)
    save(fig,'01_重拟合参数变化范围')
    fig,axes=plt.subplots(1,2,figsize=(14,8),sharex=True,sharey=True,layout='constrained')
    for ax,kind in zip(axes,('local','recursive')):
        for i,key in enumerate(keys):
            for mi,model in enumerate(models):
                r=next(v for v in report['summary']['prediction'] if (v['fit_group'],v['node'],v['model'],v['prediction'])==(*key,model,kind))
                if r['evaluated']:
                    y=i+(mi-.5)*.18;color=colors[model];ax.plot([r['minimum'],r['maximum']],[y,y],color=color,lw=2);ax.scatter(r['median'],y,c=color,s=20)
        ax.set(title='局部留出RMSE' if kind=='local' else '整网递推留出RMSE',yticks=range(len(keys)),yticklabels=[g+' / '+n for g,n in keys],xlabel='RMSE / SE点');ax.grid(axis='x',alpha=.15)
    for model in models:axes[0].plot([],[],color=colors[model],marker='o',label=names[model])
    axes[0].legend(loc='upper right')
    axes[0].invert_yaxis();fig.suptitle('原留出集不变，仅训练构成与重新估计的参数变化\n局部/递推共用横轴；50次有限诊断，范围不作置信、显著性或新独立测试解释',fontsize=14)
    save(fig,'02_重拟合后固定留出预测变化')
    return output


def run(source_dir,protocol_path,output_dir):
    source=Path(source_dir);out=Path(output_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):raise ModelError('输出目录须不存在或为空')
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    protocol=read(Path(protocol_path))
    if protocol!=PROTOCOL:raise ModelError('训练重拟合方案与登记协议不一致')
    from .flow_scenario import verify
    verified=verify(source);original=read(source/'04_FDNA拟合/workflow_report.json');rows=read(source/'04_FDNA拟合/输入_SE记录.json')
    network=original['network_snapshot'];config=original['config_snapshot'];validate_samples(network,rows,config)
    if digest(dict(network=network,rows=rows,config=config))!=original['input_sha256']:raise ModelError('冻结来源摘要不一致')
    train,held,groups,_=training_panel(rows);protocol_sha=digest(protocol)
    generator=np.random.Generator(np.random.PCG64(protocol['bootstrap_seed']))
    draws=generator.integers(0,len(train),size=(protocol['repetitions'],len(train)))
    print(dumps(dict(stage='control_started',refitted_control=True)),flush=True)
    control=fit_network(network,deepcopy(rows),deepcopy(config));control_audit=compare_control(original,control)
    out.mkdir(parents=True);(out/'逐次记录').mkdir()
    def write(name,value):(out/name).write_text(dumps(value),encoding='utf-8')
    write('控制复跑.json',dict(audit=control_audit,record=compact(control,0,protocol_sha,original['input_sha256'])))
    write('训练成组抽样索引.json',dict(training_base_cases=train,holdout_base_cases=held,indices=draws.tolist(),
        seed=protocol['bootstrap_seed'],bit_generator='PCG64'))
    all_fits=[];all_metrics=[];unique_counts=[];started=time.monotonic()
    for index,draw in enumerate(draws,1):
        sampled=sample_training(rows,draw,index);result=fit_network(network,sampled,deepcopy(config))
        record=compact(result,index,protocol_sha,original['input_sha256']);record['distinct_training_base_cases']=len(set(draw.tolist()))
        write(f'逐次记录/repetition_{index:03d}.json',record)
        all_fits.extend(record['fits']);all_metrics.extend(record['metrics']);unique_counts.append(record['distinct_training_base_cases'])
        write('运行进度.json',dict(completed=index,planned=len(draws),complete=index==len(draws)))
        print(dumps(dict(repetition=index,planned=len(draws),local_fits=len(record['fits']),
            failed=sum(f['status']!='fitted' for f in record['fits']),unique_training_cases=unique_counts[-1],seconds=round(time.monotonic()-started,2))),flush=True)
    summary=summarize(all_fits,all_metrics,len(draws));flags=Counter(flag for f in all_fits for flag in f['flags'])
    audit=dict(repetitions=len(draws),local_fits=len(all_fits),failed_local_fits=sum(f['status']!='fitted' for f in all_fits),
        flag_counts=dict(flags),distinct_training_cases_min=min(unique_counts),distinct_training_cases_max=max(unique_counts),
        control=control_audit,source_files_verified=verified)
    report=dict(stage='training_cluster_refit_sensitivity',protocol=deepcopy(protocol),protocol_sha256=protocol_sha,audit=audit,
        summary=summary,source=dict(directory=str(source.resolve()),fit_sha256=hashlib.sha256((source/'04_FDNA拟合/workflow_report.json').read_bytes()).hexdigest(),
            data_sha256=original['data_sha256'],input_sha256=original['input_sha256']),
        training_base_cases=train,holdout_base_cases=held,numpy_version=np.__version__,
        policies=dict(original_fit_overwritten=False,se_generated=False,training_resampled=True,models_refitted=True,
            solver_seed_changed=False,holdout_changed=False,holdout_used_for_optimization=False,parameter_confidence_interval=False,model_selected=False))
    write('refit_report.json',report);write('固定重拟合方案.json',protocol)
    for key,name in [('parameters','参数变化摘要.csv'),('diagnostics','诊断频次.csv'),('prediction','固定留出误差摘要.csv')]:
        (out/name).write_text(csv_text(summary[key]),encoding='utf-8',newline='')
    for name,content in figures(report,original).items():(out/name).write_bytes(content)
    (out/'结果说明.md').write_text(f'''# 训练构成改变后实际重拟合：有限敏感性诊断

保留原30个留出基础样本，对90个训练基础样本成组抽样90次，重复50轮，联合保留全部环境与节点。原求解器、6起点、种子及边界不变，不热启动。原模型和SE来源包不覆盖。

完整训练控制复跑通过，{control_audit['parameter_values_compared']}个参数值、{control_audit['prediction_values_compared']}个预测值最大差{control_audit['max_difference']:.6g}。50轮共{audit['local_fits']}次局部拟合，{audit['failed_local_fits']}次未成功；每轮实际不同训练基础样本数为{min(unique_counts)}至{max(unique_counts)}，不能把重复90条当成90个独立样本。

## 参数变化

![参数范围](01_重拟合参数变化范围.png)

线表示成功拟合的最小到最大，实点为中位数，空点为原参数。范围只描述这50次有限抽样和固定求解设置；波动小不证明参数唯一或真实，尤其要结合局部秩、不活跃参数、触界与多初值跨度。失败分母保留在诊断频次表。

## 留出预测变化

![固定留出误差](02_重拟合后固定留出预测变化.png)

留出记录保持原样，只改变训练构成并重新估计。局部与递推分别报告，不读取中间目标真值做递推。已有留出此前已查看，本步不选择方法，不作为新独立测试或方法普遍优势证据。

本步确实重新估计了实验模型，区别于04和07的参数冻结重采样；但仍不提供参数置信区间，也没有重新估计模型的全传播不确定性或真实因果验证。原正式结果保留。
''',encoding='utf-8')
    verify(source)
    inventory={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}
    write('manifest.json',inventory);print(dumps(audit));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='按训练基础样本成组重采样，保持原求解设置并实际重拟合')
    parser.add_argument('--source-dir',type=Path,required=True);parser.add_argument('--protocol',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();run(args.source_dir,args.protocol,args.output_dir)
