"""Single-input edge ablations with explicit training-only policies for new roots."""
import argparse
from copy import deepcopy
from io import BytesIO
from pathlib import Path

import numpy as np

from .model import ModelError
from .network_workflow import validate_samples
from .propagation_stability import reachable_pairs
from .refit_propagation import read, sha
from .structure import digest
from .topology_preview import canonical_network, graph_info, descendants, candidate_registry, preview
from .workflow_common import csv_text, dumps, write_bundle


PROTOCOL=dict(id='flow06_registered_edge_ablation_training_mean_v1',
    individual_cuts=[['A1','B1'],['A2','B2'],['A3','B3'],['B1','F1'],['B2','F2'],['B3','F3']],
    include_all_edges_cut=True,new_root_policy='target_mean_from_original_training_only_never_observed_holdout',
    remaining_equations='frozen_original_local_observed_input_fits',evaluation_targets='all_original_receivers_even_if_new_roots',
    external_observed_inputs='original_roots_only',retained_fractions=[0,.25,.5,.75,1],
    propagation_pairs='all_original_reachable_pairs_keep_removed_paths_as_zero_checks',
    intervention_reference='each_scenarios_own_predicted_baseline',output_clipping=False,fdna_refitting=False,
    holdout_role='previously_examined_descriptive_only_no_topology_selection',mapping_approval=False,
    interpretation='training_mean_null_hypothesis_ablation_not_true_dependency_identification_or_full_T_Refit')


def receiver_value(fit,values):
    if fit['status']!='fitted':raise ModelError('不能用原参数替补未成功拟合')
    params=fit['parameters']
    if not params:raise ModelError('接受节点缺少供给参数')
    a=np.array([p['alpha'] for p in params]);b=np.array([p['beta'] for p in params]);d=fit['delta'] or 0.
    if not np.isfinite([*a,*b,d]).all() or np.any((a<0)|(a>1)) or np.any((b<0)|(b>100)):
        raise ModelError('参数非有限或越界')
    x=np.array([values[p['feeder']] for p in params],dtype=float)
    return np.minimum(np.mean(100-a[:,None]*(100-x),axis=0),np.min(x+b[:,None],axis=0))+d


def forward(network,original_roots,fits,constants,root_values,forced=None):
    info=graph_info(canonical_network(network));roots=set(info['roots']);original_roots=set(original_roots)
    if set(root_values)!=original_roots or set(constants)!=roots-original_roots or not original_roots<=roots:
        raise ModelError('新增根须且仅由训练常数提供，外部观测输入须与原根完全一致')
    inputs={n:np.asarray(v,dtype=float) for n,v in root_values.items()};shapes={v.shape for v in inputs.values()}
    if len(shapes)!=1 or len(next(iter(shapes)))!=1:raise ModelError('根输入须为等长一维数组')
    shape=next(iter(shapes));values={}
    if any(not np.isfinite(v) for v in constants.values()):raise ModelError('训练常数非有限')
    if forced is not None:
        node,vector=forced;vector=np.asarray(vector,dtype=float)
        if node not in info['order'] or vector.shape!=shape or np.any(np.isfinite(vector)&((vector<0)|(vector>100))):
            raise ModelError('干预节点、维度或范围错误')
    for n in info['order']:
        if forced is not None and n==forced[0]:values[n]=vector.copy()
        elif n in original_roots:values[n]=inputs[n]
        elif n in constants:values[n]=np.full(shape,constants[n],dtype=float)
        else:
            if n not in fits or sorted(p['feeder'] for p in fits[n]['parameters'])!=sorted(info['incoming'][n]):
                raise ModelError('剩余方程参数与子图供给不一致')
            values[n]=receiver_value(fits[n],values)
    return values


def fit_means(rows,targets):
    means=[]
    for group in sorted({r['fit_group'] for r in rows}):
        for node in sorted(targets):
            selected=sorted((r for r in rows if r['fit_group']==group and r['split']=='train' and r['node_values'].get(node) is not None),key=lambda r:r['sample_id'])
            if not selected:raise ModelError('无依赖预测缺少训练目标，不允许用留出目标补齐')
            means.append(dict(group=group,node=node,value=float(np.mean([r['node_values'][node] for r in selected])),
                parameter_count=1,training_sample_ids=[r['sample_id'] for r in selected],
                training_base_cases=sorted({r['base_case_id'] for r in selected}),training_rows=len(selected)))
    return means


def scenarios(network,sidecar):
    info=graph_info(network)
    if any(len(v)>1 for v in info['incoming'].values()):raise ModelError('当前训练均值删边策略只适用于单供给结构')
    pairs=sorted((e['feeder'],e['receiver']) for e in network['edges'])
    if pairs!=[tuple(p) for p in PROTOCOL['individual_cuts']]:raise ModelError('源边不符合预先登记的六边面板')
    registry=candidate_registry(network,sidecar)
    result=[dict(id='original',removed=[],network=deepcopy(network),new_constant_roots=[],affected_targets=[],preview=None)]
    ids={(r['candidate']['feeder'],r['candidate']['receiver']):cid for cid,r in registry.items() if r['candidate']['exported']}
    for cuts in [[pair] for pair in pairs]+[pairs]:
        name='cut_all' if len(cuts)>1 else 'cut_'+cuts[0][0]+'_'+cuts[0][1]
        proposal=dict(id=name,source_network_sha256=digest(canonical_network(network)),source_sidecar_sha256=digest(sidecar),add=[],
            remove=[dict(candidate_id=ids[pair],rationale='预先登记的训练均值无依赖对照；仅作实验消融，不撤销原产品消费登记。') for pair in cuts])
        audit=preview(network,sidecar,proposal)
        if audit['semantic_blockers']:raise ModelError('拒绝存在语义阻断的实验提案')
        result.append(dict(id=name,removed=[list(p) for p in cuts],network=audit['proposed_topology_preview'],
            new_constant_roots=audit['added_roots'],affected_targets=audit['affected_nodes'],preview=audit))
    return result


def rmse(actual,predicted):
    mask=np.isfinite(actual)&np.isfinite(predicted)
    return float(np.sqrt(np.mean((actual[mask]-predicted[mask])**2))) if mask.any() else None


def compare(original,rows,sidecar):
    network=original['network_snapshot'];validate_samples(network,rows,original['config_snapshot'])
    if original['config_snapshot']['input_role']!='observed':raise ModelError('本方案只接受已登记的观测供给角色')
    if digest(dict(network=network,rows=rows,config=original['config_snapshot']))!=original['input_sha256']:raise ModelError('源输入摘要不一致')
    info=graph_info(network);targets=sorted(n for n in network['nodes'] if info['incoming'][n]);roots=info['roots'];nodes=sorted(network['nodes'])
    cases=sorted({r['base_case_id'] for r in rows});splits={r['base_case_id']:r['split'] for r in rows}
    held=[c for c in cases if splits[c]=='holdout'];held_indices=[cases.index(c) for c in held]
    groups=sorted({r['fit_group'] for r in rows});lookup={(r['fit_group'],r['base_case_id']):r for r in rows}
    if set(lookup)!={(g,c) for g in groups for c in cases}:raise ModelError('环境与基础样本面板不完整')
    jobs=sorted((j['fit_group'],j['model']) for j in original['jobs']);job_lookup={(j['fit_group'],j['model']):j for j in original['jobs']}
    if len(jobs)!=len(set(jobs)):raise ModelError('模型任务重复')
    means=fit_means(rows,targets);mean_lookup={(r['group'],r['node']):r['value'] for r in means}
    variants=scenarios(network,sidecar);pairs=reachable_pairs(network);fractions=PROTOCOL['retained_fractions']
    predictions=np.full((len(variants),len(jobs),len(cases),len(targets),2),np.nan)
    shape=(len(variants),len(jobs),len(pairs),len(fractions),len(held))
    effects=np.full(shape,np.nan);changed=np.full(shape,np.nan);forced_values=np.full(shape,np.nan)
    extrapolated=np.zeros(shape,dtype=bool);baseline=np.full((len(variants),len(jobs),len(held),len(nodes)),np.nan)
    structural=np.zeros((len(variants),len(pairs)),dtype=bool);complexity=[];control_error=0.;control_count=0
    target_values=np.array([[[lookup[g,c]['node_values'].get(n,np.nan) for n in targets] for c in cases] for g in groups],dtype=float)
    for si,variant in enumerate(variants):
        cut=set(variant['new_constant_roots']);topo=graph_info(variant['network'])
        for k,(start,target) in enumerate(pairs):structural[si,k]=target in descendants(topo,start)
        for ji,(group,mode) in enumerate(jobs):
            job=job_lookup[group,mode];fits={f['node']:f['fit']['receivers'][0] for f in job['local_fits']}
            constants={n:mean_lookup[group,n] for n in cut};records=[lookup[group,c] for c in cases]
            inputs={n:np.array([r['node_values'].get(n,np.nan) for r in records]) for n in nodes}
            values=forward(variant['network'],roots,fits,constants,{n:inputs[n] for n in roots})
            for ni,n in enumerate(targets):
                predictions[si,ji,:,ni,0]=np.full(len(cases),constants[n]) if n in cut else receiver_value(fits[n],inputs)
                predictions[si,ji,:,ni,1]=values[n]
            if si==0:
                old={(p['base_case_id'],p['node']):p for p in job['points']}
                if len(old)!=len(cases)*len(targets):raise ModelError('原预测面板不完整或重复')
                for bi,c in enumerate(cases):
                    for ni,n in enumerate(targets):
                        for pi,key in enumerate(('local_prediction','recursive_prediction')):
                            error=abs(predictions[si,ji,bi,ni,pi]-old[c,n][key]);control_error=max(control_error,float(error));control_count+=1
                            if not np.isfinite(error) or error>1e-8:raise ModelError('原模型预测未复现')
            else:
                for ni,n in enumerate(targets):
                    if n not in variant['affected_targets'] and not np.allclose(predictions[si,ji,:,ni],predictions[0,ji,:,ni],rtol=0,atol=1e-8,equal_nan=True):
                        raise ModelError('未受影响分量的预测改变')
            complexity.append(dict(scenario=variant['id'],group=group,model=mode,constant_parameters=len(cut),
                frozen_fdna_nominal_parameters=sum(2*len(f['parameters'])+int(f['delta'] is not None) for n,f in fits.items() if n not in cut),
                evaluation_targets=len(targets),observed_external_roots=roots,new_roots_observed=False))
            hb={n:values[n][held_indices] for n in nodes};baseline[si,ji]=np.stack([hb[n] for n in nodes],axis=1)
            for k,(start,target) in enumerate(pairs):
                if not np.isfinite(hb[start]).all() or np.any((hb[start]<0)|(hb[start]>100)):raise ModelError('当前源面板不支持完整合法比例干预')
                for fi,fraction in enumerate(fractions):
                    forced=hb[start]*fraction;post=forward(variant['network'],roots,fits,constants,{n:hb[n] for n in roots},(start,forced))
                    key=si,ji,k,fi;changed[key]=post[target];forced_values[key]=forced;effects[key]=hb[target]-post[target]
                    if fraction==1 and np.max(abs(effects[key]))>1e-8:raise ModelError('保持基准不应改变输出')
                    if not structural[si,k] and np.max(abs(effects[key]))>1e-8:raise ModelError('已断开路径出现非零响应')
                    if np.any(effects[key]<-1e-8):raise ModelError('违反非负FDNA的单调性')
                    if target not in cut:
                        ranges=next(f['training_predictor_ranges'] for f in job['local_fits'] if f['node']==target)
                        for feeder,(low,high) in ranges.items():extrapolated[key]|=(post[feeder]<low-1e-8)|(post[feeder]>high+1e-8)
    metrics=[];responses=[]
    for si,variant in enumerate(variants):
        for ji,(group,mode) in enumerate(jobs):
            truth=target_values[groups.index(group)]
            for ni,n in enumerate(targets):
                for split in ('train','holdout'):
                    indices=[i for i,c in enumerate(cases) if splits[c]==split]
                    for pi,kind in enumerate(('local','recursive')):
                        actual=truth[indices,ni];pred=predictions[si,ji,indices,ni,pi];old=predictions[0,ji,indices,ni,pi]
                        valid=np.isfinite(actual)&np.isfinite(pred)&np.isfinite(old)
                        value=rmse(actual[valid],pred[valid]);reference=rmse(actual[valid],old[valid])
                        metrics.append(dict(scenario=variant['id'],group=group,model=mode,node=n,split=split,prediction=kind,
                            planned=len(indices),evaluated=int(valid.sum()),rmse=value,original_rmse=reference,
                            difference=value-reference if value is not None else None,outside=int(np.sum(valid&((pred<0)|(pred>100)))),
                            mean_policy=n in variant['new_constant_roots'],affected=n in variant['affected_targets']))
            for k,(start,target) in enumerate(pairs):
                for fi,fraction in enumerate(fractions):
                    key=si,ji,k,fi;panel=effects[key]
                    responses.append(dict(scenario=variant['id'],group=group,model=mode,intervened_node=start,target=target,retained_fraction=fraction,
                        baseline_records=len(held),evaluated=int(np.isfinite(panel).sum()),structurally_reachable=bool(structural[si,k]),
                        mean_decrease=float(panel.mean()),original_mean_decrease=float(effects[0,ji,k,fi].mean()),
                        target_has_predictors=target not in variant['new_constant_roots'],extrapolated_records=int(extrapolated[key].sum()),
                        outside_records=int(np.sum((changed[key]<0)|(changed[key]>100)))))
    arrays=dict(predictions=predictions,target_values=target_values,decrease=effects,changed=changed,forced=forced_values,
        baseline=baseline,structurally_reachable=structural,extrapolated=extrapolated)
    audit=dict(original_predictions_compared=control_count,original_prediction_max_difference=control_error,training_constants=len(means),
        scenarios=len(variants),prediction_values=int(predictions.size),propagation_responses=int(effects.size),
        removed_path_responses=int(sum(not structural[s,k] for s in range(len(variants)) for k in range(len(pairs)))*len(jobs)*len(fractions)*len(held)),
        formal_mapping_changed=False,fdna_solver_called=False,additional_observed_roots=False)
    report=dict(stage='training_mean_edge_ablation_frozen_remaining_equations',protocol=deepcopy(PROTOCOL),audit=audit,
        axes=dict(scenarios=[v['id'] for v in variants],jobs=jobs,cases=cases,holdout_cases=held,nodes=nodes,targets=targets,
                  groups=groups,pairs=pairs,fractions=fractions,prediction=['local','recursive']),
        scenarios=variants,training_constants=means,metrics=metrics,responses=responses,complexity=complexity,
        source_input_sha256=original['input_sha256'],data_sha256=original['data_sha256'])
    return report,arrays


def figures(report):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'svg.fonttype':'path'})
    outputs={};labels={'base':'基础式','free_delta':'自由δ'};variants=report['axes']['scenarios'][1:]
    label=lambda s: {'original':'原模型','cut_all':'全边消融'}.get(s,s.replace('cut_','').replace('_','→'))
    keys=[(g,n) for g in report['axes']['groups'] for n in report['axes']['targets']]
    selected=[r for r in report['metrics'] if r['split']=='holdout' and r['prediction']=='recursive' and r['scenario']!='original']
    limit=max(abs(r['difference']) for r in selected)
    fig,axes=plt.subplots(1,2,figsize=(15,9),sharey=True,layout='constrained')
    for ax,mode in zip(axes,('base','free_delta')):
        matrix=np.array([[next(r['difference'] for r in selected if (r['scenario'],r['model'],r['group'],r['node'])==(s,mode,g,n)) for s in variants] for g,n in keys])
        im=ax.imshow(matrix,cmap='RdBu_r',vmin=-limit,vmax=limit,aspect='auto')
        for i in range(len(keys)):
            for j in range(len(variants)):ax.text(j,i,f'{matrix[i,j]:.1f}',ha='center',va='center',fontsize=8,color='white' if abs(matrix[i,j])>limit*.6 else '#153347')
        ax.set(xticks=range(len(variants)),xticklabels=[label(s) for s in variants],yticks=range(len(keys)),
            yticklabels=[g+' / '+n for g,n in keys],title=labels[mode],xlabel='训练均值替代的删边情景');ax.tick_params(axis='x',rotation=35)
    fig.colorbar(im,ax=axes,label='递推留出RMSE变化：正值更差，负值更低 / SE点')
    fig.suptitle('同外部信息、同全部评价目标：删边并用训练均值替代\n原模型为参照；不按结果选择拓扑，不视为真实依赖识别',fontsize=14)
    def save(fig,name):
        for ext in ('png','svg'):
            buffer=BytesIO();fig.savefig(buffer,format=ext,dpi=180,bbox_inches='tight');outputs[name+'.'+ext]=buffer.getvalue()
        plt.close(fig)
    save(fig,'01_删边后同目标递推误差变化')
    focus=[r for r in report['responses'] if (r['group'],r['model'],r['target'],r['retained_fraction'])==('env_0','free_delta','F1',0)]
    fig,ax=plt.subplots(figsize=(12,5),layout='constrained');x=np.arange(len(report['axes']['scenarios']))
    for offset,start,color in [(-.18,'A1','#24788b'),(.18,'B1','#c88a39')]:
        values=[next(r['mean_decrease'] for r in focus if (r['scenario'],r['intervened_node'])==(s,start)) for s in report['axes']['scenarios']]
        ax.bar(x+offset,values,width=.36,label=start+'置零 → F1',color=color)
    ax.set(xticks=x,xticklabels=[label(s) for s in report['axes']['scenarios']],ylabel='30个基准的F1平均SE减少量',
           title='固定示例 env_0 / 自由δ：依赖消融对置零传播的影响\n路径移除后零响应是模型定义的结果，不证明真实系统没有影响')
    ax.legend(loc='upper left',bbox_to_anchor=(1.01,1.));ax.grid(axis='y',alpha=.15);save(fig,'02_删边后固定示例置零传播')
    return outputs


def run(source_dir,protocol_path,output_dir):
    from .flow_scenario import verify
    source=Path(source_dir);out=Path(output_dir)
    if out.exists() and any(out.iterdir()):raise ModelError('拒绝覆盖非空输出目录')
    if read(protocol_path)!=PROTOCOL:raise ModelError('方案与预先登记协议不一致')
    verified=verify(source);folder=source/'04_FDNA拟合'
    original=read(folder/'workflow_report.json');rows=read(folder/'输入_SE记录.json');sidecar=read(folder/'衔接依据与审查.json')
    report,arrays=compare(original,rows,sidecar)
    report['source']=dict(directory=str(source.resolve()),manifest_sha256=sha(source/'完整包校验.json'),
                          fit_sha256=sha(folder/'workflow_report.json'),files_verified=verified)
    outputs={'ablation_report.json':dumps(report),'固定方案.json':dumps(PROTOCOL),'同目标误差对照.csv':csv_text(report['metrics']),
        '同关系传播对照.csv':csv_text(report['responses']),'训练常数与参数维度.json':dumps(dict(constants=report['training_constants'],complexity=report['complexity']))}
    buffer=BytesIO();np.savez_compressed(buffer,**arrays);outputs['逐样本预测与传播.npz']=buffer.getvalue();outputs.update(figures(report))
    outputs['结果说明.md']=f'''# 同信息、同目标的训练均值依赖消融

对原六条边逐一消融，并增加全边消融，共7个对照与1个原模型。失去唯一入边的节点显式使用原90个训练基础样本目标均值；新根不读取观测真值，全部六个原接受节点继续评价，只有原A1/A2/A3作为外部观测输入。原30个留出基础样本、模型设置、SE与正式映射不变。这是无依赖的统计零假设对照，不是撤销产品消费事实。

![同目标误差](01_删边后同目标递推误差变化.png)

正值表示递推留出RMSE更高，负值表示更低。局部和递推、训练和留出分别在完整CSV中报告，保持同目标同记录面板。未受影响任务分量必须完全一致；原模型8640个预测重算最大差{report['audit']['original_prediction_max_difference']:.3g}。训练均值是已估计的1个常数参数，不能称零参数；名义参数维度不等于可辨识自由度。

六个单边消融的受影响递推留出比较中，每种模型有27组：自由δ模型27组误差更高；基础式24组更高、3组更低（沿用1e-8近等口径）。这里统计的是重复使用同一留出集的条件比较，不是27个独立实验，不作显著性或真实依赖识别结论；也不改变此前与OLS对照的结论。

只给原根观测的限制专指整网递推。局部评价仍按原方案提供该目标的直接供给观测，非切断节点用原局部方程，切断节点仍只用训练常数；不能把局部误差解释为仅外部根输入下的整网误差。

![置零传播](02_删边后固定示例置零传播.png)

各情景先用原根输入计算自身基准，再按同五档保留比例重算，保留原9组可达关系用于对照。子图已断开的关系明确标记不可达并核对零响应，不从表中删除。比例0的绝对强制值都是0，非零比例下可能不同，不作固定绝对干预比较。新常数根仍可被显式干预，干预时暂停常数方程；被断开上游的置零不能再传到它。

本步属于剩余方程冻结、训练均值替代的受限数值拓扑消融。直接改边节点的原α/β/δ方程被整体移除，以训练均值替代；未变方程只在输入供给关系、观测训练数据和设置均未变时保留原参数，并非把原接受方程强套到零输入。新增18个训练均值，各有90条训练记录，未调用FDNA求解器。不是完整T-Refit，也不把两种实验混报。

实际保存{report['audit']['prediction_values']}个局部/递推预测值与{report['audit']['propagation_responses']}条下游响应，保留原所有评价目标、不可达标记、目标预测越界和目标直接供给外推提示。常数目标没有供给时，覆盖标记为不适用，不能当成已验证输入覆盖。

现有留出此前已查看，本轮不选择最佳拓扑，不据误差变化宣布真实依赖成立或不成立。05的待审结构提案仍保持待审，只在隔离实验报告中进行数值计算，不批准正式映射。真实数据、不同结构及完整重估拓扑比较仍待后续验证。
'''
    verify(source);write_bundle(out,outputs);print(dumps(report['audit']));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source-dir','protocol','output-dir'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();run(args.source_dir,args.protocol,args.output_dir)
