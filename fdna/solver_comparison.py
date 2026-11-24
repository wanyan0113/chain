"""Registered two-pipeline solver comparison with fixed data and initial vectors."""
import argparse
from collections import Counter
from copy import deepcopy
from io import BytesIO
from pathlib import Path

from .model import ModelError
from .network_workflow import fit_network,validate_samples
from .solver_seed_sensitivity import read,sha
from .structure import digest
from .training_refit import compact,compare_control,training_panel
from .workflow_common import csv_text,dumps,write_bundle

PROTOCOL=dict(id='flow06_fixed_starts_solver_pipeline_comparison_v1',solvers=['L-BFGS-B','least_squares'],
    seed=20261122,starts=6,max_nfev=1500,changed_config_field='solver_only',
    data='unchanged_original_90_training_30_holdout_base_cases',
    initialization='same_neutral_plus_five_seeded_random_scaled_vectors',
    selection='existing_training_loss_only_rule_within_each_solver_no_cross_solver_selection',
    budget_interpretation='same_numeric_limit_not_equal_function_gradient_or_time_budget',
    pipeline_interpretation='existing_analytic_gradient_lbfgsb_vs_finite_difference_least_squares_including_their_existing_tolerances',
    warm_start=False,output_clipping=False,holdout_selection=False,comparison_tolerance=1e-8,
    propagation_recomputed=False,confidence_interval=False)

def solver_config(config,solver):
    if solver not in PROTOCOL['solvers']:raise ModelError('未登记求解器')
    result=deepcopy(config)
    if set(result['models'])!={'base','free_delta'}:raise ModelError('协议要求原两种模型')
    for setting in result['models'].values():
        if any(setting.get(k)!=PROTOCOL[k] for k in ('seed','starts','max_nfev')) or setting.get('solver')!='L-BFGS-B' or 'initialization' in setting:
            raise ModelError('来源不是原固定种子六起点求解配置')
        setting['solver']=solver
    return result

def compare_records(records):
    if len(records)!=2 or [r['solver'] for r in records]!=PROTOCOL['solvers']:raise ModelError('需要两套按协议排序的求解记录')
    fits=[{(f['fit_group'],f['model'],f['node']):f for f in r['fits']} for r in records]
    if set(fits[0])!=set(fits[1]) or any(len(f)!=len(r['fits']) for f,r in zip(fits,records)):raise ModelError('拟合主键缺失或重复')
    parameters=[];diagnostics=[];metrics=[];points=[]
    for key,a in sorted(fits[0].items()):
        b=fits[1][key];header=dict(zip(('fit_group','model','node'),key))
        if sorted(tuple(x['initial']) for x in a['attempts'])!=sorted(tuple(x['initial']) for x in b['attempts']):raise ModelError('两求解器初始向量不同')
        loss=[]
        for f in (a,b):
            chosen=f['attempts'][f['attempt_selection']['index']] if f['attempts'] else None
            loss.append(chosen['rmse'] if chosen else None)
        diagnostics.append(dict(**header,lbfgsb_status=a['status'],least_squares_status=b['status'],
            lbfgsb_training_rmse=loss[0],least_squares_training_rmse=loss[1],
            training_rmse_difference=loss[1]-loss[0] if all(v is not None for v in loss) else None,
            lbfgsb_flags=a['flags'],least_squares_flags=b['flags'],
            lbfgsb_attempt_successes=sum(x['success'] for x in a['attempts']),least_squares_attempt_successes=sum(x['success'] for x in b['attempts'])))
        values=[]
        for f in (a,b):
            v={name+':'+p['feeder']:p[name] for p in f['parameters'] for name in ('alpha','beta')}
            if f['delta'] is not None:v['delta']=f['delta']
            values.append(v)
        for name in sorted(set(values[0])|set(values[1])):
            av=values[0].get(name);bv=values[1].get(name)
            parameters.append(dict(**header,parameter=name,lbfgsb=av,least_squares=bv,difference=bv-av if av is not None and bv is not None else None))
    for kind,fields in [('metrics',('fit_group','model','node','split','prediction')),('holdout_points',('fit_group','model','node','sample_id'))]:
        maps=[{tuple(r[k] for k in fields):r for r in record[kind]} for record in records]
        if set(maps[0])!=set(maps[1]) or any(len(m)!=len(r[kind]) for m,r in zip(maps,records)):raise ModelError('评价记录缺失或重复')
        for key,a in sorted(maps[0].items()):
            b=maps[1][key];header=dict(zip(fields,key))
            if kind=='metrics':
                av=a['rmse'];bv=b['rmse']
                metrics.append(dict(**header,lbfgsb_rmse=av,least_squares_rmse=bv,difference=bv-av if av is not None and bv is not None else None,
                    planned=a['available'],lbfgsb_evaluated=a['evaluated'],least_squares_evaluated=b['evaluated'],lbfgsb_outside=a['outside'],least_squares_outside=b['outside']))
            else:
                if a['target']!=b['target']:raise ModelError('两求解器评价目标不同')
                for prediction in ('local','recursive'):
                    av=a[prediction+'_prediction'];bv=b[prediction+'_prediction']
                    points.append(dict(**header,prediction=prediction,target=a['target'],lbfgsb=av,least_squares=bv,
                        difference=bv-av if av is not None and bv is not None else None))
    return dict(parameters=parameters,diagnostics=diagnostics,metrics=metrics,holdout_points=points)

def figures(summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'svg.fonttype':'path'})
    output={};keys=sorted({(r['fit_group'],r['node']) for r in summary['diagnostics']})
    def save(fig,name):
        for ext in ('png','svg'):
            stream=BytesIO();fig.savefig(stream,format=ext,dpi=160,bbox_inches='tight');output[name+'.'+ext]=stream.getvalue()
        plt.close(fig)
    for split,name,title in [('train','01_固定起点训练损失差','训练误差'),('holdout','02_固定起点留出误差差','留出误差')]:
        kinds=['local','recursive']
        fig,axes=plt.subplots(1,2,figsize=(14,8),sharey=True,layout='constrained',squeeze=False)
        for ax,kind in zip(axes[0],kinds):
            for model,color,offset in [('base','#356f9d',-.12),('free_delta','#168c87',.12)]:
                for i,(group,node) in enumerate(keys):
                    r=next(r for r in summary['metrics'] if (r['fit_group'],r['node'],r['model'],r['split'],r['prediction'])==(group,node,model,split,kind))
                    if r['difference'] is not None:ax.scatter(r['difference'],i+offset,color=color,s=28)
                ax.scatter([],[],c=color,label='基础式' if model=='base' else '自由δ')
            ax.axvline(0,c='#777',lw=1);ax.grid(axis='x',alpha=.2);ax.set(yticks=range(len(keys)),yticklabels=[g+' / '+n for g,n in keys],
                title='局部' if kind=='local' else '整网递推',xlabel='least_squares − L-BFGS-B：RMSE差 / SE点');ax.legend()
            lo,hi=ax.get_xlim();extent=max(abs(lo),abs(hi),.05);ax.set_xlim(-extent,extent)
        axes[0,0].invert_yaxis();fig.suptitle('固定数据、种子与六起点：'+title+'\n负值表示本次least_squares误差更低；两套现有梯度/停止配置不同，不作算法普遍优劣或模型选择',fontsize=12)
        save(fig,name)
    return output

def run(source_dir,protocol_path,output_dir):
    source=Path(source_dir).resolve();out=Path(output_dir).resolve();protocol=read(protocol_path)
    if protocol!=PROTOCOL:raise ModelError('未采用预登记协议')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):raise ModelError('拒绝覆盖非空结果')
    if out==source or out.is_relative_to(source):raise ModelError('不能覆盖正式来源包')
    from .flow_scenario import verify
    verified=verify(source);original=read(source/'04_FDNA拟合/workflow_report.json');rows=read(source/'04_FDNA拟合/输入_SE记录.json')
    network=original['network_snapshot'];config=original['config_snapshot'];validate_samples(network,rows,config)
    train,held,groups,_=training_panel(rows)
    if (len(train),len(held),len(groups))!=(90,30,3) or digest(dict(network=network,rows=rows,config=config))!=original['input_sha256']:raise ModelError('必须使用原06数据和划分')
    records=[];control=None
    for i,solver in enumerate(protocol['solvers']):
        print(dumps(dict(stage='solver_started',solver=solver)),flush=True)
        result=fit_network(network,deepcopy(rows),solver_config(config,solver))
        if i==0:control=compare_control(original,result)
        record=compact(result,i,digest(protocol),original['input_sha256']);record.update(solver=solver,config_snapshot=result['config_snapshot']);records.append(record)
        print(dumps(dict(stage='solver_finished',solver=solver,failed=sum(f['status']!='fitted' for f in record['fits']))),flush=True)
    summary=compare_records(records);report=dict(protocol=protocol,protocol_sha256=digest(protocol),control=control,summary=summary,
        source=dict(directory=str(source),fit_sha256=sha(source/'04_FDNA拟合/workflow_report.json'),data_sha256=original['data_sha256'],input_sha256=original['input_sha256'],verified_files=verified),
        audit={r['solver']:dict(local_fits=len(r['fits']),failed=sum(f['status']!='fitted' for f in r['fits']),flags=dict(Counter(flag for f in r['fits'] for flag in f['flags']))) for r in records},
        policies=dict(original_overwritten=False,holdout_selection=False,training_resampled=False,propagation_recomputed=False,output_clipping=False))
    outputs={'solver_report.json':dumps(report),'预登记方案.json':dumps(protocol)}
    for r,name in zip(records,('lbfgsb_record.json','least_squares_record.json')):outputs[name]=dumps(r)
    for key,name in [('parameters','参数配对差.csv'),('diagnostics','训练损失与诊断.csv'),('metrics','训练与留出指标差.csv'),('holdout_points','逐留出预测差.csv')]:outputs[name]=csv_text(summary[key])
    outputs.update(figures(summary));verify(source);write_bundle(out,outputs);return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source-dir','protocol','output-dir'):parser.add_argument('--'+name,required=True)
    a=parser.parse_args();run(a.source_dir,a.protocol,a.output_dir)
