"""Replay fixed-data seed fits under registered relative degradation scenarios."""
import argparse
from io import BytesIO
from pathlib import Path

import numpy as np

from .model import ModelError
from .propagation_stability import reachable_pairs,FIELDS
from .refit_propagation import replay,training_ranges,summarize,read,sha
from .structure import digest
from .training_refit import training_panel
from .workflow_common import dumps,csv_text,write_bundle


PROTOCOL=dict(id='flow06_solver_seed_relative_propagation_v1',
    seeds=[20261122,2026112401,2026112402,2026112403,2026112404],control_seed=20261122,
    retained_fractions=[0,.25,.5,.75,1],holdout='same_original_30_base_cases',
    intervention_reference='each_seed_models_own_baseline',failed_job='unavailable_without_parameter_substitution',
    incomplete_panel='exclude_mean_but_keep_planned_denominator',summary='minimum_median_maximum_not_confidence_interval',
    additional_fitting=False,output_clipping=False,seed_selection=False,comparison_tolerance=1e-8,
    focus=[dict(group='env_0',model='free_delta',intervened_node='B1',target='F1',retained_fraction=0),
           dict(group='env_0.3',model='free_delta',intervened_node='B3',target='F3',retained_fraction=0)],
    focus_basis='first_inherited_from_09_second_selected_using_17_beta_spread_before_this_propagation_run')


def control_comparison(arrays,original,rows,jobs,cases,pairs,fractions,nodes):
    mapping={(r['fit_group'],r['sample_id']):r['base_case_id'] for r in rows if r['split']=='holdout'}
    seen=set();maximum=0.
    for e in original['analysis']['effects']:
        key=(jobs.index((e['group'],e['model'])),pairs.index((e['intervened_node'],e['target'])),
             fractions.index(e['retained_fraction']),cases.index(mapping[e['group'],e['baseline_sample']]))
        if key in seen:raise ModelError('冻结控制响应主键重复')
        seen.add(key);j,k,fi,b=key
        if not arrays['valid'][key]:raise ModelError('控制响应不可用')
        for a,value in [(arrays['decrease'][key],-e['effect']),(arrays['changed'][key],e['intervened']),
                        (arrays['forced'][key],e['forced_value']),(arrays['baseline'][j,b,nodes.index(e['target'])],e['baseline'])]:
            error=abs(a-value)
            if not np.isfinite(error) or error>1e-8:raise ModelError('原种子未复现冻结传播')
            maximum=max(maximum,float(error))
    if len(seen)!=len(jobs)*len(pairs)*len(fractions)*len(cases):raise ModelError('控制传播响应缺失')
    return dict(responses=len(seen),maximum_absolute_difference=maximum)


def focus_rows(records,arrays,jobs,pairs,fractions,focus):
    result=[]
    for condition in focus:
        j=jobs.index((condition['group'],condition['model']));k=pairs.index((condition['intervened_node'],condition['target']))
        fi=fractions.index(condition['retained_fraction'])
        for si,record in enumerate(records):
            target=next(f for f in record['fits'] if (f['fit_group'],f['model'],f['node'])==(condition['group'],condition['model'],condition['target']))
            parameters=target['parameters']
            if parameters and (len(parameters)!=1 or parameters[0]['feeder']!=condition['intervened_node']):
                raise ModelError('固定示例只支持直接单供给')
            metrics={m['prediction']:m for m in record['metrics'] if (m['fit_group'],m['model'],m['node'],m['split'])==(condition['group'],condition['model'],condition['target'],'holdout')}
            panel=arrays['decrease'][si,j,k,fi];complete=bool(arrays['valid'][si,j,k,fi].all());p=parameters[0] if parameters else None
            forced=arrays['forced'][si,j,k,fi]
            cod_count=int(np.sum(arrays['valid'][si,j,k,fi] & (forced+p['beta']<100-p['alpha']*(100-forced)-1e-8))) if p else 0
            result.append(dict(**condition,seed=record['seed'],target_alpha=p['alpha'] if p else None,target_beta=p['beta'] if p else None,target_delta=target['delta'],
                training_beta_inactive=bool(p and 'beta:'+p['feeder'] in target['sensitivity'].get('inactive_parameters',[])),
                mean_decrease=float(panel.mean()) if complete else None,complete_baselines=bool(complete),
                local_holdout_rmse=metrics['local']['rmse'],recursive_holdout_rmse=metrics['recursive']['rmse'],
                unique_cod_responses=cod_count))
    return result


def figures(report,focus):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'svg.fonttype':'path'})
    output={}
    def save(fig,name):
        for ext in ('png','svg'):
            stream=BytesIO();fig.savefig(stream,format=ext,dpi=170,bbox_inches='tight');output[name+'.'+ext]=stream.getvalue()
        plt.close(fig)
    rows=[r for r in report['summary'] if r['retained_fraction']==0 and r['intervened_node'].startswith('B')]
    fig,ax=plt.subplots(figsize=(12,8),layout='constrained')
    for i,r in enumerate(rows):
        if r['complete_models']:
            ax.plot([r['minimum'],r['maximum']],[i,i],color='#168c87',lw=2);ax.scatter(r['median'],i,c='#168c87',s=22)
        ax.scatter(r['original_mean'],i,marker='x',c='#b26432',s=36,zorder=3)
    ax.plot([],[],color='#168c87',marker='o',label='5种子：最小—中位—最大');ax.scatter([],[],marker='x',c='#b26432',label='原种子')
    ax.set(yticks=range(len(rows)),yticklabels=[r['group']+' / '+('基础式' if r['model']=='base' else '自由δ')+' / '+r['intervened_node']+'→'+r['target'] for r in rows],xlabel='同一30基准的目标平均SE减少量 / SE点',title='固定训练数据，求解起点种子改变后的置零传播范围\n仅展示B→F直接关系；线段不是置信区间，不表示真实因果')
    ax.invert_yaxis();ax.grid(axis='x',alpha=.2);ax.legend(loc='best');save(fig,'01_种子间置零传播范围')
    selected=[r for r in focus if r['group']=='env_0.3' and r['target']=='F3']
    fig,axes=plt.subplots(1,3,figsize=(14,4.8),layout='constrained')
    for ax,field,title in zip(axes,('target_beta','local_holdout_rmse','mean_decrease'),('F3接收B3的β','普通局部留出RMSE / SE点','B3置零→F3平均减少量 / SE点')):
        ax.plot(range(len(selected)),[r[field] for r in selected],marker='o',color='#168c87');ax.set(xticks=range(len(selected)),xticklabels=['原种子','新1','新2','新3','新4'],title=title);ax.grid(alpha=.2)
    axes[1].set_ylim(bottom=0)
    fig.suptitle('运行前固定示例：env_0.3 / 自由δ / B3置零→F3\n依据17号β跨度选定，非独立确认性检验；各面板纵轴不同，不把关联单独归因于β',fontsize=12)
    save(fig,'02_固定示例参数预测与传播');return output


def run(seed_dir,frozen_report,protocol_path,output_dir):
    directory=Path(seed_dir).resolve();out=Path(output_dir).resolve();protocol=read(protocol_path)
    if protocol!=PROTOCOL:raise ModelError('与预登记传播方案不一致')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):raise ModelError('输出目录须不存在或为空')
    if out==directory or out.is_relative_to(directory):raise ModelError('不能覆盖种子来源包')
    frozen_directory=Path(frozen_report).resolve().parent
    if out==frozen_directory or out.is_relative_to(frozen_directory):raise ModelError('不能覆盖冻结传播来源包')
    from workflow_tools.verify_solver_seed_sensitivity import verify
    upstream=verify(directory);seed_report=read(directory/'seed_report.json');source=Path(seed_report['source']['directory']).resolve()
    if out==source or out.is_relative_to(source):raise ModelError('不能覆盖正式06包')
    manifest_sha=sha(directory/'manifest.json')
    frozen=read(frozen_report)
    for name,value in read(Path(frozen_report).parent/'manifest.json').items():
        if sha(Path(frozen_report).parent/name)!=value:raise ModelError('冻结传播来源摘要不符')
    if frozen['input_sha256']!=seed_report['source']['input_sha256']:raise ModelError('传播与种子拟合来源不同')
    original=read(source/'04_FDNA拟合/workflow_report.json');rows=read(source/'04_FDNA拟合/输入_SE记录.json');network=original['network_snapshot']
    train,cases,groups,_=training_panel(rows)
    if (len(train),len(cases),len(groups))!=(90,30,3):raise ModelError('需要原06完整面板')
    records=[read(directory/f'逐种子记录/seed_{seed}.json') for seed in protocol['seeds']]
    if [r['seed'] for r in records]!=protocol['seeds']:raise ModelError('种子记录不匹配')
    jobs=sorted({(f['fit_group'],f['model']) for f in records[0]['fits']});pairs=reachable_pairs(network)
    fractions=protocol['retained_fractions'];nodes=sorted(network['nodes']);ranges=training_ranges(network,rows,train)
    runs=[replay(network,record,rows,jobs,cases,pairs,fractions,ranges) for record in records]
    control=control_comparison(runs[0],frozen,rows,jobs,cases,pairs,fractions,nodes)
    arrays={name:np.stack([r[name] for r in runs]) for name in runs[0]}
    summary=summarize(arrays,jobs,pairs,fractions,frozen);focus=focus_rows(records,arrays,jobs,pairs,fractions,protocol['focus'])
    seed_means=[]
    for si,seed in enumerate(protocol['seeds']):
        for r in summary:
            j=jobs.index((r['group'],r['model']));k=pairs.index((r['intervened_node'],r['target']));fi=fractions.index(r['retained_fraction'])
            valid=arrays['valid'][si,j,k,fi];panel=arrays['decrease'][si,j,k,fi]
            seed_means.append(dict(seed=seed,**{key:r[key] for key in FIELDS},complete_baselines=bool(valid.all()),
                evaluated=int(valid.sum()),mean_decrease=float(panel.mean()) if valid.all() else None))
    report=dict(stage='solver_seed_relative_degradation_sensitivity',protocol=protocol,protocol_sha256=digest(protocol),
        axes=dict(seeds=protocol['seeds'],jobs=jobs,pairs=pairs,fractions=fractions,base_cases=cases,nodes=nodes),
        source_seed_directory=str(directory),source_manifest_sha256=manifest_sha,
        source_record_sha256={f'逐种子记录/seed_{seed}.json':sha(directory/f'逐种子记录/seed_{seed}.json') for seed in protocol['seeds']},
        frozen_report_sha256=sha(frozen_report),frozen_report_path=str(Path(frozen_report).resolve()),
        source_verification=upstream,control=control,summary=summary,
        audit=dict(responses=int(arrays['valid'].size),available=int(arrays['valid'].sum()),outside=int(arrays['outside'].sum()),
            target_extrapolated=int(arrays['extrapolated'].sum()),conditions=len(summary),
            complete_condition_models=sum(r['complete_models'] for r in summary),planned_condition_models=len(summary)*len(records)),
        policies=dict(additional_fitting=False,original_overwritten=False,holdout_changed=False,confidence_interval=False,seed_selection=False))
    outputs={'seed_propagation_report.json':dumps(report),'预登记方案.json':dumps(protocol),'传播范围摘要.csv':csv_text(summary),
        '逐种子平均响应.csv':csv_text(seed_means),'固定示例.csv':csv_text(focus)}
    buffer=BytesIO();np.savez_compressed(buffer,**arrays);outputs['逐种子逐基准数组.npz']=buffer.getvalue();outputs.update(figures(report,focus))
    if sha(directory/'manifest.json')!=manifest_sha:raise ModelError('运行中来源清单发生变化')
    for name,value in read(directory/'manifest.json').items():
        if sha(directory/name)!=value:raise ModelError('运行中种子来源变化')
    for name,value in read(frozen_directory/'manifest.json').items():
        if sha(frozen_directory/name)!=value:raise ModelError('运行中冻结传播来源变化')
    from .flow_scenario import verify as verify_source
    verify_source(source)
    write_bundle(out,outputs);print(dumps(report['audit']));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('seed-dir','frozen-report','protocol','output-dir'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();run(args.seed_dir,args.frozen_report,args.protocol,args.output_dir)
