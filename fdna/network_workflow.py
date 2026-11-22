"""Given DAG + repeated node SE -> local fits, recursive predictions and interventions."""
import argparse
from collections import defaultdict
from copy import deepcopy
import csv
import json
import math
from pathlib import Path

from .engine import evaluate
from .additive import evaluate_additive, ADDITIVE_MODEL
from .fitting import fit_parameters, _validate
from .generation_delta_diagnostics import training_geometry
from .generation_fitting import _stats
from .model import ModelError, topology
from .structure import digest
from .workflow_common import make_model,predict_receiver,parameter_flags,number,text,csv_text,write_bundle,dumps


META=('sample_id','base_case_id','scenario_id','fit_group','split')


def _se(value,label):
    if value in (None,''): return None
    if isinstance(value,str):
        try: value=float(value)
        except ValueError as exc: raise ModelError(label+'不是SE数值') from exc
    return number(value,label,0,100)


def read_samples(path,network,layout='wide'):
    """Read explicit wide/long CSV or canonical JSON; never guess a column mapping."""
    make_model(network)
    path=Path(path); nodes=set(network['nodes'])
    if path.suffix.lower()=='.json':
        value=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,list): raise ModelError('SE JSON须为记录数组')
        return value
    if path.suffix.lower()!='.csv': raise ModelError('当前统一入口支持JSON或CSV')
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        fields=reader.fieldnames or []
        if len(fields)!=len(set(fields)): raise ModelError('CSV列名重复')
        if not set(META)<=set(fields): raise ModelError('CSV缺少样本、场景、分组或划分字段')
        source=list(reader)
        if any(None in r or any(v is None for v in r.values()) for r in source):
            raise ModelError('CSV行的字段数与表头不一致；缺失SE请显式留空，不省略列')
    if layout=='wide':
        if nodes & set(META) or any(n.startswith('predictor:') for n in nodes):
            raise ModelError('节点编号与宽表保留列冲突，请使用长表或JSON记录')
        allowed=set(META)|nodes|{'predictor:'+n for n in nodes}
        if set(fields)-allowed: raise ModelError('宽表包含未知字段或节点')
        return [{**{k:r[k] for k in META},'node_values':{n:_se(r[n],n) for n in nodes if n in r},
                 **({'predictor_values':{n:_se(r['predictor:'+n],n) for n in nodes if 'predictor:'+n in r}}
                    if any(f.startswith('predictor:') for f in fields) else {})} for r in source]
    if layout!='long' or not {'node_id','se'}<=set(fields) or set(fields)-set(META)-{'node_id','se','predictor_se'}:
        raise ModelError('长表须包含node_id、se及可选predictor_se')
    grouped={};seen=set()
    for r in source:
        node=r['node_id']; key=r['fit_group'],r['sample_id']
        if node not in nodes: raise ModelError('长表引用未知节点')
        if (*key,node) in seen: raise ModelError('样本节点主键重复')
        seen.add((*key,node))
        if key not in grouped:
            grouped[key]={**{k:r[k] for k in META},'node_values':{}}
            if 'predictor_se' in fields: grouped[key]['predictor_values']={}
        row=grouped[key]
        if any(row[k]!=r[k] for k in META): raise ModelError('同一样本的元数据冲突')
        row['node_values'][node]=_se(r['se'],node)
        if 'predictor_se' in fields: row['predictor_values'][node]=_se(r['predictor_se'],node)
    return list(grouped.values())


def validate_samples(network,rows,config):
    make_model(network)
    if set(config)-{'input_role','pool_scenarios','data_kind','models','analysis'}:
        raise ModelError('统一拟合配置含未知字段')
    if not isinstance(rows,list) or not rows: raise ModelError('节点SE记录不能为空')
    if config.get('input_role') not in ('observed','clean_predictors_observed_targets'):
        raise ModelError('须声明输入为实测SE或模拟干净供给与观测目标')
    if not isinstance(config.get('pool_scenarios'),bool): raise ModelError('须显式声明是否合并场景拟合')
    if 'analysis' in config: validate_analysis(config['analysis'])
    keys=set(); splits={}; nodes=set(network['nodes']); groups=defaultdict(set)
    for r in rows:
        if not isinstance(r,dict) or set(r)-set(META)-{'node_values','predictor_values'} or not set(META)<=set(r) or not isinstance(r.get('node_values'),dict):
            raise ModelError('SE记录字段不符合统一数据契约')
        for k in META: text(r[k],k)
        key=r['fit_group'],r['sample_id']
        if key in keys: raise ModelError('同组样本id重复')
        keys.add(key)
        if r['split'] not in ('train','holdout'): raise ModelError('split须为train或holdout')
        if r['base_case_id'] in splits and splits[r['base_case_id']]!=r['split']:
            raise ModelError('同一基础实验跨训练与验证，存在数据泄漏')
        splits[r['base_case_id']]=r['split']; groups[r['fit_group']].add(r['scenario_id'])
        if config['input_role']=='clean_predictors_observed_targets' and 'predictor_values' not in r:
            raise ModelError('模拟干净输入模式必须显式提供predictor_values，不假造干净真值')
        for field in ('node_values','predictor_values'):
            if field not in r: continue
            if not isinstance(r[field],dict) or set(r[field])-nodes: raise ModelError('SE引用未知节点')
            for n,value in r[field].items():
                if value is not None: number(value,n,0,100)
    if not config['pool_scenarios'] and any(len(s)>1 for s in groups.values()):
        raise ModelError('未允许同参数机制场景合并，请分别设置fit_group')
    modes=config.get('models')
    if not isinstance(modes,dict) or not modes: raise ModelError('须登记至少一个拟合模型设置')
    receivers={e['receiver'] for e in network['edges']}
    for name,setting in modes.items():
        text(name,'模型版本');_validate('settings',setting)
        if 'initialization' in setting: raise ModelError('应用入口只采用中性与随机起点，不接收预设边参数起点')
        key='delta_bounds' if setting['delta_policy']=='per_receiver_bounded' else 'delta_values'
        if setting['model']==ADDITIVE_MODEL and set(setting[key])!=receivers:
            raise ModelError('δ配置须恰好覆盖全部接受节点')
    return sorted(groups)


def predictors(row,config):
    return row['node_values'] if config['input_role']=='observed' else row['predictor_values']


def recursive(network,local_fits,root_values,forced=None):
    """Unclipped recursive equations; missing fits/roots block descendants explicitly."""
    model=make_model(network);order=topology(model);incoming={n:[] for n in network['nodes']}
    for e in network['edges']: incoming[e['receiver']].append(e['feeder'])
    forced=forced or {};values={};status=[]
    if set(forced)-set(network['nodes']): raise ModelError('干预引用未知节点')
    for n,v in forced.items(): number(v,n+'干预值',0,100)
    for n in order:
        if n in forced:
            values[n]=forced[n];code='intervened'
        elif not incoming[n]:
            values[n]=root_values.get(n);code='root' if values[n] is not None else 'missing_root'
        elif n not in local_fits or local_fits[n].get('status')!='fitted':
            values[n]=None;code='fit_unavailable'
        elif any(values[p] is None for p in incoming[n]):
            values[n]=None;code='upstream_blocked'
        else:
            values[n]=predict_receiver(local_fits[n],values);code='computed'
            if not math.isfinite(values[n]): raise ModelError('整网预测产生非有限数值')
        status.append(dict(node=n,status=code,value=values[n],outside=values[n] is not None and not 0<=values[n]<=100))
    return values,status


def training_ranges(entry):
    if not entry.get('fit') or entry['status']!='fitted': return {}
    row=entry['fit']['receivers'][0];result={}
    for p in row['parameters']:
        key=p['feeder']
        data=[r['node_values'][key] for r in entry['fit']['samples_snapshot']['records']
              if r['split']=='train' and all(r['node_values'].get(n) is not None for n in row['feeders']+[row['receiver']])]
        if data: result[key]=[min(data),max(data)]
    return result


def outside_training(values,ranges):
    return [n for n,(lo,hi) in ranges.items() if values.get(n) is not None and not lo-1e-8<=values[n]<=hi+1e-8]


def fit_network(network,rows,config,progress=None):
    groups=validate_samples(network,rows,config);model=make_model(network)
    receivers=sorted({e['receiver'] for e in network['edges']});roots=sorted(set(network['nodes'])-set(receivers));jobs=[]
    for group in groups:
        selected=sorted((r for r in rows if r['fit_group']==group),key=lambda r:r['sample_id'])
        for mode,setting in config['models'].items():
            local=[]; lookup={}
            for receiver in receivers:
                projected=deepcopy(model)
                projected['edges']=[e for e in model['edges'] if e['receiver']==receiver]
                projected['case_extension']['delta']={receiver:None}
                projected['id']+='_'+receiver
                data=[]
                for r in selected:
                    values=deepcopy(predictors(r,config));values[receiver]=r['node_values'].get(receiver)
                    data.append(dict(id=r['sample_id'],split=r['split'],node_values=values))
                samples=dict(format_version='1.0',id=group+'_'+receiver,data_kind='method_check' if config.get('data_kind')=='simulated' else 'user_supplied_se',
                    basis='统一入口按给定拓扑映射；接收目标和供给输入显式分开；只按训练损失优化。',case_sha256=digest(projected),records=data)
                local_setting=deepcopy(setting)
                for key in ('delta_bounds','delta_values'):
                    if key in local_setting: local_setting[key]={receiver:local_setting[key][receiver]}
                # Invalid data/settings raise before this point; numerical failures remain node records.
                try:
                    fit=fit_parameters(projected,samples,local_setting)
                    row=fit['receivers'][0]
                    geometry=None
                    if fit['status']=='complete' and row['metrics'].get('holdout'):
                        diagnostic_input=deepcopy(fit)
                        needed=row['feeders']+[receiver]
                        original_records=diagnostic_input['samples_snapshot']['records']
                        diagnostic_input['samples_snapshot']['records']=[r for r in original_records if all(r['node_values'].get(n) is not None for n in needed)]
                        geometry=training_geometry(diagnostic_input,dict(near_training_rmse_tolerance=1e-6,prediction_equivalence_tolerance=1e-6,null_step_fraction=.1))
                        geometry['omitted_incomplete_records']=len(original_records)-len(diagnostic_input['samples_snapshot']['records'])
                    flags=parameter_flags(row)
                    if row.get('metrics',{}).get('holdout') is None: flags.append('NO_HOLDOUT_PREDICTION')
                    entry=dict(node=receiver,status=row['status'],flags=flags,fit=fit,diagnostic=geometry)
                    lookup[receiver]=row
                except (FloatingPointError,RuntimeError) as exc:
                    entry=dict(node=receiver,status='numerical_failure',flags=['NUMERICAL_FAILURE'],error=str(exc),fit=None,diagnostic=None)
                local.append(entry)
            ranges={l['node']:training_ranges(l) for l in local}
            for l in local:
                l['training_predictor_ranges']=ranges[l['node']]
                if any(hi-lo<1e-10 for lo,hi in ranges[l['node']].values()): l['flags'].append('CONSTANT_PREDICTOR')
            points=[];metric_rows=[];recursive_checks=0;max_check=0.
            # Reconstitute a core model for independent scalar/core cross-checks.
            core_model=deepcopy(model)
            all_ready=all(n in lookup and lookup[n]['status']=='fitted' for n in receivers)
            if all_ready:
                for e in core_model['edges']:
                    e.update(next({k:p[k] for k in ('alpha','beta')} for p in lookup[e['receiver']]['parameters'] if p['feeder']==e['feeder']))
            for i,r in enumerate(selected):
                pred=predictors(r,config); root={n:pred.get(n) for n in roots}
                values,status=recursive(network,lookup,root)
                if all_ready and all(v is not None for v in root.values()) and i in (0,len(selected)-1):
                    scene=dict(id=r['sample_id'],basis='独立标量整网与核心公式交叉核验',node_values=root)
                    ext=dict(model=ADDITIVE_MODEL,basis='训练所得δ或预设固定δ',delta_values={n:lookup[n].get('delta') or 0. for n in receivers},output_policy='no_clipping')
                    core=evaluate_additive(core_model,scene,ext) if setting['model']==ADDITIVE_MODEL else evaluate(core_model,scene)
                    max_check=max(max_check,max(abs(v['value']-values[v['id']]) for v in core['nodes']))
                    recursive_checks+=1
                for n in receivers:
                    fitrow=lookup.get(n);parents=[e['feeder'] for e in model['edges'] if e['receiver']==n]
                    lp=predict_receiver(fitrow,pred) if fitrow and fitrow['status']=='fitted' and all(pred.get(p) is not None for p in parents) else None
                    target=r['node_values'].get(n)
                    bound=max(0,min(pred[p] for p in parents)-target) if target is not None and all(pred.get(p) is not None for p in parents) and setting['model']!=ADDITIVE_MODEL else None
                    points.append(dict(sample_id=r['sample_id'],base_case_id=r['base_case_id'],scenario_id=r['scenario_id'],split=r['split'],node=n,
                        target=target,local_prediction=lp,recursive_prediction=values[n],recursive_status=next(s['status'] for s in status if s['node']==n),base_local_deficit=bound,
                        local_extrapolated_inputs=outside_training(pred,ranges[n]),recursive_extrapolated_inputs=outside_training(values,ranges[n])))
            for l in local:
                own=[p for p in points if p['node']==l['node']]
                if any(p[k] is not None and not 0<=p[k]<=100 for p in own for k in ('local_prediction','recursive_prediction')):
                    l['flags'].append('PREDICTION_OUTSIDE_SE_RANGE')
                if any(p['local_extrapolated_inputs'] or p['recursive_extrapolated_inputs'] for p in own):
                    l['flags'].append('PREDICTOR_COVERAGE_EXTRAPOLATION')
            for n in receivers:
                for split in ('train','holdout'):
                    candidates=[p for p in points if p['node']==n and p['split']==split]
                    for kind in ('local','recursive'):
                        valid=[p for p in candidates if p['target'] is not None and p[kind+'_prediction'] is not None]
                        stat=_stats([p['target'] for p in valid],[p[kind+'_prediction'] for p in valid]) if valid else None
                        metric_rows.append(dict(node=n,split=split,prediction=kind,available=len(candidates),evaluated=len(valid),omitted=len(candidates)-len(valid),
                            outside=sum(not 0<=p[kind+'_prediction']<=100 for p in valid),
                            rmse_lower_bound=math.sqrt(math.fsum(p['base_local_deficit']**2 for p in valid)/len(valid)) if valid and kind=='local' and all(p['base_local_deficit'] is not None for p in valid) else None,
                            **(stat or dict(count=0,rmse=None,mae=None,max_absolute_error=None,mean_residual=None,r_squared=None))))
            if max_check>1e-8: raise ModelError('独立递推与核心公式不一致')
            job=dict(fit_group=group,model=mode,status='complete' if all_ready else 'incomplete',local_fits=local,points=points,metrics=metric_rows,
                core_crosscheck_count=recursive_checks,core_max_difference=max_check)
            jobs.append(job)
            if progress: progress(dict(group=group,model=mode,status=job['status'],nodes=len(local)))
    return dict(stage='given_network_fitting_workflow',status='complete' if all(j['status']=='complete' for j in jobs) else 'incomplete',
        network_snapshot=deepcopy(network),config_snapshot=deepcopy(config),data_sha256=digest(rows),input_sha256=digest(dict(network=network,rows=rows,config=config)),
        topology=dict(order=topology(model),roots=roots,receivers=receivers),jobs=jobs,
        policies=dict(topology_inferred=False,holdout_used_for_optimization=False,reported_table7_parameters_used=False,prediction_clipping=False,
            input_role=config['input_role'],input_measurement_error_modeled=False,parameter_uniqueness_established=False),
        limits=['诊断标记是当前数据和拟合条件下的证据，不是置信概率。','实测输入的测量误差未建模。','留出预测、参数识别与预测范围分别报告。'])


def _paths(network,start,max_paths):
    adjacency={n:[] for n in network['nodes']}
    for e in network['edges']: adjacency[e['feeder']].append(e['receiver'])
    paths=[];truncated=False;stack=[[start]]
    while stack:
        path=stack.pop()
        if len(path)>1:
            if len(paths)==max_paths: truncated=True;break
            paths.append(path)
        stack.extend(path+[n] for n in sorted(adjacency[path[-1]],reverse=True))
    return paths,truncated


def validate_analysis(config):
    if not isinstance(config,dict) or set(config)-{'retained_fractions','max_paths_per_node','baseline_policy','baseline_count'}:
        raise ModelError('退化分析配置含未知字段')
    fractions=config.get('retained_fractions');limit=config.get('max_paths_per_node')
    if not isinstance(limit,int) or isinstance(limit,bool) or limit<1: raise ModelError('路径上限须为正整数')
    if not isinstance(fractions,list) or not fractions: raise ModelError('保留比例须为非空数组')
    for f in fractions: number(f,'保留比例',0,1)
    if len(set(fractions))!=len(fractions): raise ModelError('保留比例不得重复')
    policy=config.get('baseline_policy','first_holdout')
    if policy not in ('first_holdout','all_holdout','evenly_spaced_holdout'): raise ModelError('未知基准选择规则')
    if policy=='evenly_spaced_holdout':
        count=config.get('baseline_count')
        if not isinstance(count,int) or isinstance(count,bool) or count<2: raise ModelError('均匀基准个数须为至少2的整数')
    elif 'baseline_count' in config: raise ModelError('只有均匀基准规则接收baseline_count')


def select_baselines(rows,config):
    ordered=sorted(rows,key=lambda r:r['sample_id'])
    policy=config.get('baseline_policy','first_holdout')
    if policy=='first_holdout': return ordered[:1]
    if policy=='all_holdout': return ordered
    count=min(config['baseline_count'],len(ordered))
    if count<2: return ordered
    return [ordered[i*(len(ordered)-1)//(count-1)] for i in range(count)]


def analyze_dependencies(report,rows,config):
    validate_analysis(config)
    if digest(rows)!=report['data_sha256']:
        raise ModelError('分析数据与拟合报告不一致；不得将不同来源的样本误标为本报告基准')
    network=report['network_snapshot'];result=[];paths=[];checks=[]
    fractions=config['retained_fractions'];limit=config['max_paths_per_node']
    for j in report['jobs']:
        local={l['node']:l['fit']['receivers'][0] for l in j['local_fits'] if l.get('fit') is not None}
        core_model=make_model(network)
        core_ready=all(n in local and local[n]['status']=='fitted' for n in report['topology']['receivers'])
        if core_ready:
            for edge in core_model['edges']:
                edge.update(next({k:p[k] for k in ('alpha','beta')} for p in local[edge['receiver']]['parameters'] if p['feeder']==edge['feeder']))
        selected=sorted((r for r in rows if r['fit_group']==j['fit_group'] and r['split']=='holdout'),key=lambda r:r['sample_id'])
        if not selected:
            checks.append(dict(group=j['fit_group'],model=j['model'],status='no_holdout_baseline'));continue
        for baseline_index,r in enumerate(select_baselines(selected,config)):
            predictor=predictors(r,report['config_snapshot']);roots={n:predictor.get(n) for n in report['topology']['roots']}
            base,status=recursive(network,local,roots)
            for node in sorted(network['nodes']):
                graph_paths,truncated=_paths(network,node,limit)
                if baseline_index==0:
                    paths.extend(dict(group=j['fit_group'],model=j['model'],intervened_node=node,path=p,truncated=truncated) for p in graph_paths)
                # Recompute reachability separately so truncation never loses affected nodes.
                reachable=set();pending=[node]
                while pending:
                    current=pending.pop()
                    for edge in network['edges']:
                        if edge['feeder']==current and edge['receiver'] not in reachable:
                            reachable.add(edge['receiver']);pending.append(edge['receiver'])
                if base[node] is None or not 0<=base[node]<=100:
                    checks.append(dict(group=j['fit_group'],model=j['model'],baseline_sample=r['sample_id'],node=node,status='baseline_missing_or_outside',baseline=base[node]));continue
                for fraction in fractions:
                    forced=base[node]*fraction;changed,trace=recursive(network,local,roots,{node:forced})
                    core_error=None
                    if core_ready and all(v is not None for v in roots.values()):
                        scene=dict(id=r['sample_id'],basis='模型内退化核验',node_values=roots)
                        clamp=dict(basis='预设保留比例并暂停所干预节点入边',node_values={node:forced})
                        ext=dict(model=ADDITIVE_MODEL,basis='训练所得或预设δ',delta_values={n:local[n].get('delta') or 0. for n in report['topology']['receivers']},output_policy='no_clipping')
                        core=evaluate_additive(core_model,scene,ext,intervention=clamp) if report['config_snapshot']['models'][j['model']]['model']==ADDITIVE_MODEL else evaluate(core_model,scene,intervention=clamp)
                        core_error=max(abs(v['value']-changed[v['id']]) for v in core['nodes'])
                        if core_error>1e-8: raise ModelError('节点退化重算与核心干预公式不一致')
                    non_descendant_invariant=all(changed[n]==base[n] for n in network['nodes'] if n not in reachable and n!=node)
                    if not non_descendant_invariant: raise ModelError('节点干预改变了非后代节点')
                    checks.append(dict(group=j['fit_group'],model=j['model'],baseline_sample=r['sample_id'],node=node,retained_fraction=fraction,status='checked',non_descendant_invariant=True,paths_truncated=truncated,core_max_difference=core_error))
                    for target in sorted(reachable):
                        value=changed[target];baseline=base[target]
                        result.append(dict(group=j['fit_group'],model=j['model'],baseline_sample=r['sample_id'],intervened_node=node,
                            retained_fraction=fraction,forced_value=forced,target=target,baseline=baseline,intervened=value,
                            effect=value-baseline if value is not None and baseline is not None else None,
                            outside=value is not None and not 0<=value<=100,blocked=value is None,paths_truncated=truncated,
                            extrapolated_inputs=outside_training(changed,next(l.get('training_predictor_ranges',{}) for l in j['local_fits'] if l['node']==target))))
    return dict(stage='model_scenario_dependency_analysis',effects=result,paths=paths,checks=checks,
        config_snapshot=deepcopy(config),policies=dict(baseline=config.get('baseline_policy','first_holdout'),baseline_selection_uses_targets=False,refitted=False,intermediate_incoming_suspended=True,
            path_effects_added=False,causal_effect_established=False,parameter_uniqueness_established=False),
        limits=['节点退化结果是拟合模型中的情景推演，不证明真实干预因果。','结构可达路径不保证实际数值变化，多路径影响不能简单相加。','超出训练供给范围的推演只能视为外推。'])


def export_outputs(report,analysis=None):
    parameters=[];status=[];metrics=[];points=[];omissions=[]
    for j in report['jobs']:
        head=dict(fit_group=j['fit_group'],model=j['model'])
        metrics.extend(dict(**head,**m) for m in j['metrics']);points.extend(dict(**head,**p) for p in j['points'])
        for l in j['local_fits']:
            row=l['fit']['receivers'][0] if l['fit'] else {}
            status.append(dict(**head,node=l['node'],status=l['status'],flags=l['flags'],rank=row.get('sensitivity',{}).get('local_jacobian_rank'),parameter_count=row.get('sensitivity',{}).get('parameter_count'),delta=row.get('delta')))
            parameters.extend(dict(**head,receiver=l['node'],delta=row.get('delta'),flags=l['flags'],**p) for p in row.get('parameters',[]))
            omissions.extend(dict(**head,node=l['node'],**p) for p in row.get('omitted',[]))
    outputs={'workflow_report.json':dumps(report),'边参数.csv':csv_text(parameters),'节点状态.csv':csv_text(status),
        '预测指标.csv':csv_text(metrics),'逐条预测.csv':csv_text(points),'局部排除记录.csv':csv_text(omissions,['fit_group','model','node','id','reason'])}
    if analysis is not None:
        outputs.update({'dependency_analysis.json':dumps(analysis),'节点退化影响.csv':csv_text(analysis['effects']),
            '依赖传播路径.csv':csv_text(analysis['paths']),'退化核验状态.csv':csv_text(analysis['checks'])})
    return outputs


def main(argv=None):
    p=argparse.ArgumentParser(description='给定DAG与多次实验SE的拟合及依赖分析')
    p.add_argument('--network',type=Path,required=True);p.add_argument('--samples',type=Path,required=True)
    p.add_argument('--config',type=Path,required=True);p.add_argument('--layout',choices=['wide','long'],default='wide');p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args(argv)
    try:
        if a.output_dir.exists() and any(a.output_dir.iterdir()): raise ModelError('输出目录非空')
        network=json.loads(a.network.read_text(encoding='utf-8'));config=json.loads(a.config.read_text(encoding='utf-8'))
        rows=read_samples(a.samples,network,a.layout);report=fit_network(network,rows,config)
        analysis=analyze_dependencies(report,rows,config['analysis']) if 'analysis' in config else None
        write_bundle(a.output_dir,export_outputs(report,analysis));print(dumps(dict(status=report['status'],jobs=len(report['jobs']))))
        return 0 if report['status']=='complete' else 1
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(dumps(dict(status='invalid',error=str(exc))));return 2

if __name__=='__main__': raise SystemExit(main())
