"""Fit independent joint samples locally, then predict using roots alone."""
import argparse
from copy import deepcopy
import csv
import io
import json
import math
from pathlib import Path
import sys

from jsonschema import Draft202012Validator

from source_data.common import read_json, write_outputs
from .engine import MODEL, evaluate
from .additive import ADDITIVE_MODEL, evaluate_additive
from .fitting import _validate, fit_parameters
from .generation_network_check import run_network_check
from .model import ModelError, _finite, validate_model
from .recovery import recovery_model
from .residuals import diagnose_residuals
from .structure import digest


def audit_generation(report):
    _finite(report)
    if report.get('stage') not in ('second_stage_step_2_joint_generation','stage2_varying_conditions_generation','stage2_process_generation','stage2_interaction_generation') or report.get('status')!='complete':
        raise ModelError('拟合需要已完成的综合网络生成报告')
    if report['stage']=='stage2_interaction_generation':
        from .generation_interaction import run_interaction_network_check
        regenerated=run_interaction_network_check(report['network_snapshot'],report['node_configs_snapshot'],report['interaction_config_snapshot'])
    elif report['stage']=='stage2_process_generation':
        from .generation_process import run_process_network_check
        regenerated=run_process_network_check(report['network_snapshot'],report['node_configs_snapshot'],report['process_config_snapshot'])
    elif report['stage']=='stage2_varying_conditions_generation':
        from .generation_varying import run_varying_network_check
        regenerated=run_varying_network_check(report['network_snapshot'],report['node_configs_snapshot'],report['varying_config_snapshot'])
    else:
        regenerated=run_network_check(report['network_snapshot'],report['node_configs_snapshot'],report['config_snapshot'])
    if digest(regenerated)!=digest(report):
        raise ModelError('生成报告与当前配置重算不一致，请核对来源或版本')


def make_fitting_model(network):
    model=recovery_model('joint_network')
    refs=['equations:eq1_eq2']
    model.update(id='joint_'+network['id'],label='独立综合样本的FDNA拟合',
                 scope='从独立生成网络投影数值计算结构；所有依赖参数待估，不使用生成机制系数作真值。')
    model['subjects']=[dict(id='U_'+n,label='模拟主体'+n,source_refs=refs) for n in sorted(network['nodes'])]
    roles={'T':'insight','Q':'coordination','R':'manifestation'}
    model['nodes']=[dict(id=n,label='模拟能力'+n,subject='U_'+n,role=roles.get(n,'insight'),
        indicator=dict(label='模拟SE',direction='unspecified',normalization=None),source_refs=refs)
        for n in sorted(network['nodes'])]
    model['edges']=[dict(feeder=e['feeder'],receiver=e['receiver'],alpha=None,beta=None,source_refs=refs)
                    for e in network['edges']]
    model['case_extension']['delta']={e['receiver']:None for e in network['edges']}
    validate_model(model)
    return model


def project_local_samples(full_model,observations,receiver):
    model=deepcopy(full_model)
    model['id']+='_'+receiver
    model['scope']='单接受节点的局部回归投影；上游使用生成干净值，目标使用该节点观测值。'
    model['edges']=[e for e in model['edges'] if e['receiver']==receiver]
    model['case_extension']['delta']={receiver:None}
    records=[]
    for row in observations:
        values=row['node_values_clean'].copy()
        # Receiver observation is not reused as a feeder observation in another fit.
        values[receiver]=row['node_values_observed'][receiver]
        records.append(dict(id=row['observation_id'],split=row['split'],node_values=values))
    samples=dict(format_version='1.0',id=model['id']+'_samples',data_kind='method_check',
                 basis='同一次综合网络记录的干净供给输入与接受节点观测目标；完整网络分组划分。',
                 case_sha256=digest(model),records=records)
    validate_model(model)
    return model,samples


def _stats(target,predicted):
    n=len(target)
    residual=[p-y for p,y in zip(predicted,target)]
    sse=math.fsum(e*e for e in residual); mean=math.fsum(target)/n
    sst=math.fsum((y-mean)**2 for y in target)
    return dict(count=n,rmse=math.sqrt(sse/n),mae=math.fsum(abs(e) for e in residual)/n,
                max_absolute_error=max(abs(e) for e in residual),mean_residual=math.fsum(residual)/n,
                r_squared=1-sse/sst if sst>0 else None)


def _validate_config(config,allow_additive=False):
    _finite(config)
    schema=json.loads(Path(__file__).with_name('generation_fitting_schema.json').read_text(encoding='utf-8'))
    errors=list(Draft202012Validator(schema).iter_errors(config))
    if errors: raise ModelError('综合拟合配置不符合契约：'+errors[0].message)
    _validate('settings',config['settings'])
    if config['settings']['model']!=MODEL and not (allow_additive and config['settings']['model']==ADDITIVE_MODEL):
        raise ModelError('本首轮入口只支持不含δ的基础式，不丢弃扩展参数')
    if 'initialization' in config['settings']:
        raise ModelError('本首轮使用中性及随机起点，不能从生成机制参数配置起点')


def fit_joint_network(generation,config,*,progress=None,allow_additive=False):
    _validate_config(config,allow_additive)
    audit_generation(generation)
    available=generation['config_snapshot']
    if (set(config['groups'])-set(available['groups']) or set(config['noise_std'])-set(available['noise_std']) or
            set(config['repetitions'])-set(range(1,available['repetitions']+1))):
        raise ModelError('所选条件、噪声或重复编号不在生成报告中')
    full=make_fitting_model(generation['network_snapshot'])
    receivers=generation['topology']['receivers']; roots=generation['topology']['roots']
    additive=config['settings']['model']==ADDITIVE_MODEL
    if additive:
        delta_key='delta_values' if config['settings']['delta_policy']=='per_receiver_fixed' else 'delta_bounds'
        if set(config['settings'][delta_key])!=set(receivers):
            raise ModelError('整网δ配置须恰好覆盖接受节点')
    observations=generation['observations']; jobs=[]
    for group in config['groups']:
        for sigma in config['noise_std']:
            for rep in config['repetitions']:
                rows=[r for r in observations if r['group']==group and r['noise_sigma']==sigma and r['repetition']==rep]
                if not rows or not {'train','holdout'}<={r['split'] for r in rows}:
                    raise ModelError('每个拟合组都需要完整训练与验证网络记录')
                local=[]; local_predictions={}; parameters=[]
                for receiver in receivers:
                    model,samples=project_local_samples(full,rows,receiver)
                    settings=deepcopy(config['settings'])
                    if additive: settings[delta_key]={receiver:settings[delta_key][receiver]}
                    fit=fit_parameters(model,samples,settings)
                    residual_audit=diagnose_residuals(model,fit)
                    fitted=fit['receivers'][0]
                    local.append(dict(receiver=receiver,model=model,fit=fit,residual_audit=residual_audit))
                    local_predictions[receiver]={p['id']:p['predicted']
                        for split in ('train','holdout') for p in (fitted.get('metrics',{}).get(split) or {}).get('rows',[])}
                    parameters.extend(dict(receiver=receiver,**p) for p in fitted.get('parameters',[]))
                ready=all(r['fit']['status']=='complete' for r in local)
                points=[]; metrics=[]; fitted_model=None
                if ready:
                    fitted_model=deepcopy(full)
                    lookup={(p['feeder'],p['receiver']):p for p in parameters}
                    for edge in fitted_model['edges']:
                        p=lookup[edge['feeder'],edge['receiver']]
                        edge.update(alpha=p['alpha'],beta=p['beta'])
                    extension=(dict(model=ADDITIVE_MODEL,basis='来自已核验的逐接受节点拟合/固定δ，不是原文真值',
                        delta_values={r['receiver']:r['fit']['receivers'][0]['delta'] for r in local},
                        output_policy='no_clipping') if additive else None)
                    for row in rows:
                        root_values={p:row['node_values_clean'][p] for p in roots}
                        scenario=dict(id=row['observation_id']+':recursive',
                            basis='仅提供验证或训练根节点，所有中间节点由拟合FDNA递推',node_values=root_values)
                        recursive=evaluate_additive(fitted_model,scenario,extension) if additive else evaluate(fitted_model,scenario)
                        calculated={n['id']:n['value'] for n in recursive['nodes']}
                        for receiver in receivers:
                            parent_values=[row['node_values_clean'][e['feeder']] for e in full['edges'] if e['receiver']==receiver]
                            points.append(dict(observation_id=row['observation_id'],base_case_id=row['base_case_id'],
                                node=receiver,split=row['split'],actual_clean=row['node_values_clean'][receiver],
                                actual_observed=row['node_values_observed'][receiver],
                                local_prediction=local_predictions[receiver][row['observation_id']],
                                recursive_prediction=calculated[receiver],
                                actual_min_parent=min(parent_values)))
                    for receiver in receivers:
                        for split in ('train','holdout'):
                            selected=[p for p in points if p['node']==receiver and p['split']==split]
                            for target_kind in ('clean','observed'):
                                target=[p['actual_'+target_kind] for p in selected]
                                for mode in ('local','recursive'):
                                    stat=_stats(target,[p[mode+'_prediction'] for p in selected])
                                    bound=(math.sqrt(math.fsum(max(0,p['actual_min_parent']-p['actual_'+target_kind])**2
                                        for p in selected)/len(selected)) if mode=='local' and not additive else None)
                                    if bound is not None and stat['rmse']+1e-8<bound:
                                        raise ModelError('局部拟合误差低于同输入同目标的结构下界，请核对计算')
                                    metrics.append(dict(node=receiver,split=split,target=target_kind,mode=mode,
                                                        rmse_lower_bound=bound,**stat))
                jobs.append(dict(group=group,noise_sigma=sigma,repetition=rep,
                    status='complete' if ready else 'blocked',local_fits=local,parameters=parameters,
                    fitted_model=fitted_model,extension=extension if ready else None,points=points,metrics=metrics,
                    counts=dict(train=sum(r['split']=='train' for r in rows),holdout=sum(r['split']=='holdout' for r in rows))))
                if progress is not None:
                    progress(dict(group=group,noise_sigma=sigma,repetition=rep,status=jobs[-1]['status']))
    return dict(format_version='1.0',status='complete' if all(j['status']=='complete' for j in jobs) else 'blocked',
        stage='second_stage_step_3_joint_fit',config_snapshot=deepcopy(config),
        generation_report_sha256=digest(generation),generation_input_sha256=generation['input_sha256'],
        topology=deepcopy(generation['topology']),
        input_sha256=digest(dict(generation=generation,config=config)),jobs=jobs,
        policies=dict(generator_parameters_used_as_truth=False,generator_parameters_used_as_initialization=False,
            parameter_truth_available=False,holdout_used_for_optimization=False,local_input='clean_parents_observed_receiver',
            recursive_input='clean_roots_only',actual_intermediate_nodes_used_for_recursion=False,
            reported_pseudo_r_squared_used=False,delta=config['settings']['delta_policy'],prediction_clipping='none'),
        limits=['结果是独立合成机制下的模型近似实验，不是参数恢复或原文表7复现',
                '多起点有界优化不保证全局最优；局部敏感度秩不证明全局唯一',
                '噪声根节点作为观测保留，当前递推基线仍输入干净根节点，实际测量误差输入另行研究',
                '整网误差可能放大或衰减；实际上游输入计算的结构下界仅适用于局部指标'])


def fitting_csvs(report):
    result={}
    tables={
        'fit_metrics.csv':[dict(group=j['group'],noise_sigma=j['noise_sigma'],repetition=j['repetition'],**m)
                           for j in report['jobs'] for m in j['metrics']],
        'fit_parameters.csv':[dict(group=j['group'],noise_sigma=j['noise_sigma'],repetition=j['repetition'],**p)
                              for j in report['jobs'] for p in j['parameters']],
        'fit_predictions.csv':[dict(group=j['group'],noise_sigma=j['noise_sigma'],repetition=j['repetition'],**p)
                               for j in report['jobs'] for p in j['points']]}
    for name,rows in tables.items():
        stream=io.StringIO(newline='')
        if rows:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        result[name]='\ufeff'+stream.getvalue()
    return result


def render_fit_summary(report):
    lines=['# 第二阶段第三步 综合网络FDNA拟合与预测','',
        '独立机制生成联合数据，基础FDNA逐接受节点联合拟合入边参数。局部预测读取真实干净上游值；整网预测只输入根节点，中间节点完全由FDNA递推。',
        '本轮只运行配置选定的条件组、噪声和重复；无α、β真值，不报告参数恢复误差。','',
        '| 条件组 | σ | 节点 | 验证局部RMSE | 验证整网RMSE | 局部理论下界 | 局部R² | 整网R² |',
        '| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |']
    for j in report['jobs']:
        for n in report['topology']['order']:
            chosen={m['mode']:m for m in j['metrics'] if m['node']==n and m['split']=='holdout' and m['target']=='clean'}
            if not chosen: continue
            a,b=chosen['local'],chosen['recursive']
            def show(x): return '未定义' if x is None else f'{x:.4f}'
            lines.append(f"| {j['group']} | {j['noise_sigma']} | {n} | {show(a['rmse'])} | {show(b['rmse'])} | {show(a['rmse_lower_bound'])} | {show(a['r_squared'])} | {show(b['r_squared'])} |")
    lines+=['','## 结果解释','',
        '本表使用干净目标和同一验证集。局部下界也在相同验证输入与目标上计算，不能拿来约束整网误差。完整CSV另存训练/验证以及干净/观测目标的指标。',
        '不额外衰减组用于观察组合机制近似；固定条件组加入自身与环境条件乘数，可能产生基础FDNA无法表示的低输出。拟合误差需结合结构下界解释，不因误差较大就修改生成数据。',
        '两类误差不能预设随层数单调增加，可能因分支、饱和及偏差抵消而放大或衰减。R²采用普通决定系数，允许为负，不称为原文伪R²。',
        '', '## 参数与计算边界','',
        '参数表中的α、β是FDNA对这批独立生成数据的估计，没有预设真值；生成器的权重和支持系数不是FDNA参数。每个局部拟合均保留起点、收敛状态、残差审查和敏感度诊断。',
        '本轮的根节点输入使用干净值，观测噪声未当作实际状态向下游生成端传播。含δ拟合、噪声输入与多重复实验仍作为后续独立配置。','',
        '## 局部参数敏感度','', '| 条件组 | 节点 | 局部秩 | 不活跃参数 |', '| --- | --- | --- | --- |']
    for j in report['jobs']:
        if j['status']!='complete':
            lines+=['',j['group']+'存在未完成拟合，未用于整网预测。']
        for local in j['local_fits']:
            row=local['fit']['receivers'][0]
            if 'sensitivity' not in row: continue
            s=row['sensitivity']
            lines.append(f"| {j['group']} | {local['receiver']} | {s['local_jacobian_rank']}/{s['parameter_count']} | {', '.join(s['inactive_parameters']) or '无'} |")
    lines+=['','局部秩不足或存在不活跃参数时，不能将单点参数当作唯一关系强度；本轮优先解释预测表现，不据此给出关键节点排序。','',
        '## 重跑命令','', '```powershell',"Set-Location '"+str(Path(__file__).resolve().parents[1])+"'",
        "& '"+sys.executable+"' -B -m fdna.generation_fitting --generation-report generated/stage2-step2-joint-final/network_report.json --config data/fdna/generation_network_fit.json --output-dir generated/stage2-fit-new --plot",'```','']
    return '\n'.join(lines)


def plot_fit_results(report):
    """Compare the same held-out clean targets without hiding poor fits."""
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib import rc_context
    jobs=[j for j in report['jobs'] if j['status']=='complete']
    if not jobs: return {}
    nodes=[n for n in report['topology']['order'] if n in report['topology']['receivers']]
    outputs={}
    with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10}):
        fig=Figure(figsize=(6*len(jobs),4.8),layout='constrained'); FigureCanvasAgg(fig)
        axes=fig.subplots(1,len(jobs),squeeze=False,sharey=True)[0]
        for ax,job in zip(axes,jobs):
            selected={(m['node'],m['mode']):m for m in job['metrics'] if m['split']=='holdout' and m['target']=='clean'}
            x=list(range(len(nodes)))
            ax.bar([i-.18 for i in x],[selected[n,'local']['rmse'] for n in nodes],width=.36,label='局部预测',color='#367AA8')
            ax.bar([i+.18 for i in x],[selected[n,'recursive']['rmse'] for n in nodes],width=.36,label='整网递推',color='#D78C36')
            ax.scatter(x,[selected[n,'local']['rmse_lower_bound'] for n in nodes],marker='D',color='#333333',label='局部结构下界',zorder=4)
            ax.set(xticks=x,xticklabels=nodes,xlabel='接受节点',ylabel='验证集 RMSE（SE点）',
                   title=job['group']+' | σ='+str(job['noise_sigma']))
            ax.grid(axis='y',alpha=.2); ax.set_axisbelow(True); ax.legend()
        fig.suptitle('综合网络：局部近似与整网递推误差')
        for ext in ('png','svg'):
            stream=io.BytesIO(); fig.savefig(stream,format=ext,dpi=200); outputs['holdout_rmse.'+ext]=stream.getvalue()
        fig=Figure(figsize=(4*len(nodes),3.8*len(jobs)),layout='constrained'); FigureCanvasAgg(fig)
        axes=fig.subplots(len(jobs),len(nodes),squeeze=False)
        for row,job in enumerate(jobs):
            for col,node in enumerate(nodes):
                ax=axes[row,col]; points=[p for p in job['points'] if p['node']==node and p['split']=='holdout']
                actual=[p['actual_clean'] for p in points]
                ax.scatter(actual,[p['local_prediction'] for p in points],s=22,alpha=.65,label='局部预测',color='#367AA8')
                ax.scatter(actual,[p['recursive_prediction'] for p in points],s=25,alpha=.65,marker='x',label='整网递推',color='#D78C36')
                ax.plot([0,100],[0,100],color='#777777',linestyle='--',linewidth=1,label='预测=实际')
                ax.set(xlim=(0,100),ylim=(0,100),xlabel='独立生成的干净SE',ylabel='FDNA预测SE',title=job['group']+' / '+node)
                ax.set_aspect('equal'); ax.grid(alpha=.15); ax.legend(fontsize=8)
        fig.suptitle('独立验证集：预测值与生成值对照')
        for ext in ('png','svg'):
            stream=io.BytesIO(); fig.savefig(stream,format=ext,dpi=200); outputs['holdout_predictions.'+ext]=stream.getvalue()
    return outputs


def main(argv=None):
    parser=argparse.ArgumentParser(description='从独立整网样本开展局部与递推FDNA检验')
    parser.add_argument('--generation-report',type=Path,required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--plot',action='store_true')
    args=parser.parse_args(argv)
    try:
        result=fit_joint_network(read_json(args.generation_report),read_json(args.config))
        outputs={'fit_report.json':json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',
                 '第二阶段第三步_综合拟合结果.md':render_fit_summary(result),**fitting_csvs(result)}
        if args.plot: outputs.update(plot_fit_results(result))
        write_outputs(args.output_dir,outputs,protected=[args.generation_report,args.config])
        print(json.dumps(dict(status=result['status'],jobs=len(result['jobs'])),ensure_ascii=False))
        return 0 if result['status']=='complete' else 1
    except (ValueError,OSError,KeyError,TypeError,ImportError) as exc:
        print(json.dumps(dict(status='invalid',error=str(exc)),ensure_ascii=False))
        return 2


if __name__=='__main__':
    sys.exit(main())
