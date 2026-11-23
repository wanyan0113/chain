"""Extend frozen branch explanations to the 50 refitted experimental models."""
import argparse
from io import BytesIO
from pathlib import Path

import numpy as np

from .model import ModelError
from .refit_propagation import read, sha, layout
from .workflow_common import csv_text, dumps, write_bundle


PROTOCOL=dict(id='flow06_refitted_training_to_propagation_branch_audit_v1',tie_tolerance=1e-8,
    scope='validated_single_feeder_receivers_only',
    training_inputs='observed_feeders_in_each_original_resampled_training_draw',
    holdout_inputs='each_models_recursive_feeders_not_internal_observations',
    response_panel='all_saved_09_responses_including_retained_one',
    strict_training_beta_inactive='all_training_rows_unique_SOD_no_COD_no_near_tie',
    activation='unique_COD_in_saved_intervention_response',missing='code_zero_unavailable_not_SOD',
    fit_or_change_parameters=False,
    interpretation='local_piecewise_formula_explanation_not_parameter_identification_or_causality')


def branch(x,alpha,beta,delta=0.,tolerance=1e-8):
    """0 unavailable, 1 unique SOD, 2 unique COD, 3 near tie. Delta is after min."""
    if not np.isfinite([alpha,beta,delta,tolerance]).all() or not 0<=alpha<=1 or not 0<=beta<=100 or tolerance<0:
        raise ModelError('分支参数或容差无效')
    x=np.asarray(x,dtype=float);sod=100-alpha*(100-x);cod=x+beta
    valid=np.isfinite(x);codes=np.zeros(x.shape,dtype=np.uint8);gap=sod-cod
    codes[valid & (gap < -tolerance)]=1
    codes[valid & (gap > tolerance)]=2
    codes[valid & (np.abs(gap)<=tolerance)]=3
    return codes,np.minimum(sod,cod)+delta,sod,cod


def feeder_values(arrays,ri,j,k,fi,feeder,axes):
    """Read recursive feeder from the saved panel; never use internal target observations."""
    start=axes['pairs'][k][0]
    if feeder==start:return arrays['forced'][ri,j,k,fi]
    relation=[start,feeder]
    if relation in axes['pairs']:
        return arrays['changed'][ri,j,axes['pairs'].index(relation),fi]
    return arrays['baseline'][ri,j,:,axes['nodes'].index(feeder)]


def audit_model(network,record,rows,selected_cases,arrays,ri,axes,tolerance):
    nodes,parents,_=layout(network)
    if any(len(p)>1 for p in parents.values()):raise ModelError('本诊断只支持已经验证的单供给节点；多输入须逐COD候选另行定义')
    lookup={(r['fit_group'],r['base_case_id']):r for r in rows if r['split']=='train'}
    panel=np.zeros(arrays['valid'][ri].shape,dtype=np.uint8)
    before=np.zeros((len(axes['jobs']),len(axes['pairs']),len(axes['base_cases'])),dtype=np.uint8)
    training=[];conditions=[];maximum=0.
    for j,(group,mode) in enumerate(axes['jobs']):
        entries=[f for f in record['fits'] if (f['fit_group'],f['model'])==(group,mode)]
        fits={f['node']:f for f in entries}
        if len(fits)!=len(entries) or set(fits)!={n for n in nodes if parents[n]}:raise ModelError('拟合接受节点重复或缺失')
        available=all(f['status']=='fitted' for f in entries)
        inactive={}
        for target in sorted(fits):
            f=fits[target];feeder=parents[target][0]
            counts=[0,0,0,0]
            if available:
                if len(f['parameters'])!=1 or f['parameters'][0]['feeder']!=feeder:raise ModelError('单供给参数关系不一致')
                p=f['parameters'][0];x=[lookup[group,c]['node_values'][feeder] for c in selected_cases]
                codes,*_=branch(x,p['alpha'],p['beta'],f['delta'] or 0.,tolerance)
                counts=np.bincount(codes,minlength=4).tolist()
            inactive[target]=available and counts[1]==len(selected_cases)
            training.append(dict(repetition=ri+1,group=group,model=mode,node=target,feeder=feeder,
                planned_training_rows=len(selected_cases),distinct_training_cases=len(set(selected_cases)),
                available_training_rows=sum(counts[1:]),training_sod=counts[1],training_cod=counts[2],training_near_tie=counts[3],
                strict_training_beta_inactive=inactive[target],job_available=available))
        for k,(start,target) in enumerate(axes['pairs']):
            feeder=parents[target][0];f=fits[target]
            if available:
                p=f['parameters'][0];d=f['delta'] or 0.
                x=arrays['baseline'][ri,j,:,axes['nodes'].index(feeder)]
                base,*_=branch(x,p['alpha'],p['beta'],d,tolerance);before[j,k]=base
            for fi,fraction in enumerate(axes['fractions']):
                valid=arrays['valid'][ri,j,k,fi]
                if not available and valid.any():raise ModelError('不可用模型却保存了有效响应')
                if available:
                    x=feeder_values(arrays,ri,j,k,fi,feeder,axes)
                    codes,y,*_=branch(x,p['alpha'],p['beta'],d,tolerance)
                    if np.any(valid & (codes==0)):raise ModelError('有效响应的供给非有限')
                    codes=np.where(valid,codes,0).astype(np.uint8);panel[j,k,fi]=codes
                    difference=np.abs(y[valid]-arrays['changed'][ri,j,k,fi][valid])
                    if len(difference):maximum=max(maximum,float(difference.max()))
                    if maximum>1e-8:raise ModelError('分支公式与09响应不一致')
                codes=panel[j,k,fi];base=before[j,k];activated=(codes==2)&inactive[target]
                conditions.append(dict(repetition=ri+1,group=group,model=mode,intervened_node=start,target=target,retained_fraction=fraction,
                    planned_records=len(axes['base_cases']),available_records=int(valid.sum()),
                    sod=int((codes==1).sum()),cod=int((codes==2).sum()),near_tie=int((codes==3).sum()),
                    strict_training_beta_inactive=inactive[target],activated_beta_records=int(activated.sum()),
                    activation_with_extrapolation=int((activated&arrays['extrapolated'][ri,j,k,fi]).sum()),
                    activated_from_recursive_sod=int((activated&(base==1)).sum()),
                    activated_already_recursive_cod=int((activated&(base==2)).sum()),
                    activated_from_recursive_near_tie=int((activated&(base==3)).sum()),
                    baseline_sod_to_cod=int(((base==1)&(codes==2)).sum())))
    return training,conditions,panel,before,maximum


def aggregate(conditions):
    keys=('group','model','intervened_node','target','retained_fraction');grouped={}
    for row in conditions:grouped.setdefault(tuple(row[k] for k in keys),[]).append(row)
    fields=('planned_records','available_records','sod','cod','near_tie','activated_beta_records','activation_with_extrapolation',
            'activated_from_recursive_sod','activated_already_recursive_cod','activated_from_recursive_near_tie','baseline_sod_to_cod')
    return [dict(zip(keys,key),models=len(rows),strict_training_beta_inactive_models=sum(r['strict_training_beta_inactive'] for r in rows),
                 **{field:sum(r[field] for r in rows) for field in fields}) for key,rows in sorted(grouped.items())]


def figures(summary,training):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'svg.fonttype':'path'})
    output={};labels={'base':'基础式','free_delta':'自由δ'}
    def save(fig,name):
        for ext in ('png','svg'):
            stream=BytesIO();fig.savefig(stream,format=ext,dpi=180,bbox_inches='tight');output[name+'.'+ext]=stream.getvalue()
        plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(13,5),sharey=True,layout='constrained')
    groups=sorted({r['group'] for r in summary});fractions=sorted({r['retained_fraction'] for r in summary})
    for ax,mode in zip(axes,('base','free_delta')):
        for group in groups:
            rates=[]
            for fraction in fractions:
                rows=[r for r in summary if (r['group'],r['model'],r['retained_fraction'])==(group,mode,fraction)]
                denominator=sum(r['available_records'] for r in rows)
                rates.append(100*sum(r['activated_from_recursive_sod'] for r in rows)/denominator if denominator else np.nan)
            ax.plot(fractions,rates,marker='o',label=group)
        ax.set(title=labels[mode],xlabel='节点SE保留比例',ylabel='占全部有效可达响应的比例 / %',ylim=(0,105));ax.grid(alpha=.2);ax.legend()
    fig.suptitle('训练时β严格不活跃：递推基准SOD → 干预后COD\n排除基准已是COD或近并列；分母为全部有效可达响应，不作置信解释',fontsize=13)
    save(fig,'01_训练不活跃参数的退化启用')
    rows=[r for r in summary if (r['group'],r['model'],r['intervened_node'],r['target'])==('env_0','free_delta','B1','F1')]
    fig,ax=plt.subplots(figsize=(10,5),layout='constrained');bottom=np.zeros(len(rows));x=np.arange(len(rows))
    for name,label,color in [('sod','唯一SOD','#2b718e'),('cod','唯一COD','#d19442'),('near_tie','近并列','#987db5')]:
        values=np.array([100*r[name]/r['available_records'] if r['available_records'] else 0 for r in rows]);ax.bar(x,values,bottom=bottom,label=label,color=color);bottom+=values
    ax.set(xticks=x,xticklabels=[str(r['retained_fraction']) for r in rows],xlabel='B1保留比例',ylabel='50轮×30基准的响应构成 / %',ylim=(0,105),
           title='固定示例 env_0 / 自由δ / B1→F1：限制分支如何变化\n同一模型同一比例的多基准记录，不是独立训练实验')
    ax.legend(loc='upper left',bbox_to_anchor=(1.01,1.0));save(fig,'02_固定示例分支构成');return output


def run(propagation_dir,protocol_path,output_dir):
    from workflow_tools.verify_refit_propagation import verify
    source=Path(propagation_dir);out=Path(output_dir)
    if out.exists() and any(out.iterdir()):raise ModelError('输出目录须不存在或为空')
    protocol=read(protocol_path)
    if protocol!=PROTOCOL:raise ModelError('与登记分支诊断协议不一致')
    previous=verify(source);saved=read(source/'propagation_report.json');refits=Path(saved['source_refit_directory'])
    refit=read(refits/'refit_report.json');original=Path(refit['source']['directory'])
    network=read(original/'04_FDNA拟合/workflow_report.json')['network_snapshot']
    rows=read(original/'04_FDNA拟合/输入_SE记录.json');draws=read(refits/'训练成组抽样索引.json');axes=saved['axes']
    with np.load(source/'逐模型逐基准数组.npz') as archive:arrays={k:archive[k] for k in archive.files}
    training=[];conditions=[];panels=[];baselines=[];maximum=0.
    for ri,indices in enumerate(draws['indices']):
        record=read(refits/f'逐次记录/repetition_{ri+1:03d}.json')
        selected=[draws['training_base_cases'][i] for i in indices]
        t,c,p,b,error=audit_model(network,record,rows,selected,arrays,ri,axes,protocol['tie_tolerance'])
        training.extend(t);conditions.extend(c);panels.append(p);baselines.append(b);maximum=max(maximum,error)
    summary=aggregate(conditions)
    activated=sum(r['activated_beta_records'] for r in conditions);extra=sum(r['activation_with_extrapolation'] for r in conditions)
    inactive=sum(r['strict_training_beta_inactive'] for r in training)
    activated_fits={(r['repetition'],r['group'],r['model'],r['target']) for r in conditions if r['activated_beta_records']}
    audit=dict(training_fits=len(training),strict_training_beta_inactive_fits=inactive,activated_in_propagation_fits=len(activated_fits),
        response_records=sum(r['planned_records'] for r in conditions),available_responses=sum(r['available_records'] for r in conditions),
        activated_beta_responses=activated,activation_with_extrapolation=extra,activation_without_extrapolation=activated-extra,
        activated_from_recursive_sod=sum(r['activated_from_recursive_sod'] for r in conditions),
        activated_already_recursive_cod=sum(r['activated_already_recursive_cod'] for r in conditions),
        activated_from_recursive_near_tie=sum(r['activated_from_recursive_near_tie'] for r in conditions),
        baseline_sod_to_cod=sum(r['baseline_sod_to_cod'] for r in conditions),near_tie_responses=sum(r['near_tie'] for r in conditions),
        max_response_difference=maximum)
    report=dict(stage='refitted_training_to_degradation_branch_audit',protocol=protocol,audit=audit,summary=summary,axes=axes,
        source_propagation_directory=str(source.resolve()),source_manifest_sha256=sha(source/'manifest.json'),
        source_report_sha256=sha(source/'propagation_report.json'),source_arrays_sha256=sha(source/'逐模型逐基准数组.npz'),
        previous_verification=previous,branch_codes={'0':'unavailable','1':'unique_SOD','2':'unique_COD','3':'near_tie'})
    stream=BytesIO();np.savez_compressed(stream,response_branch=np.stack(panels),baseline_branch=np.stack(baselines))
    outputs={'branch_report.json':dumps(report),'固定方案.json':dumps(protocol),'训练分支逐拟合.csv':csv_text(training),
        '传播分支逐模型条件.csv':csv_text(conditions),'传播分支条件摘要.csv':csv_text(summary),'逐响应分支.npz':stream.getvalue()}
    outputs.update(figures(summary,training))
    outputs['结果说明.md']=f'''# 为什么普通预测不能代替退化传播诊断

本步扩展已有冻结模型分支解释，检查08的50轮重估实验模型在09全部405000条响应中的目标限制分支。没有新增拟合、改变参数、改边、重生成SE或覆盖历史输出。以1e-8为近并列容差，在加δ之前比较SOD与COD；近并列保留双候选，不选唯一导数。仅适用于当前已验证的单供给结构，多输入明确拒绝。

当前单供给公式为 `SE = min(100 − α(100 − x), x + β) + δ`。前一候选称SOD，后一候选称COD；两者取较小值。训练时x为登记的观测供给，传播时x为根观测或上游递推输出，内部节点不读取留出真值。

![退化启用](01_训练不活跃参数的退化启用.png)

“训练时β严格不活跃”指该轮90条成组抽样训练记录中，目标方程全部由唯一SOD限制，没有COD或近并列。这里是局部公式上的零敏感度证据，不证明全局唯一、β值正确或没有其他识别途径。重复训练记录仍非新的独立基础样本。

1800个局部拟合中，{inactive}个符合上述严格定义，其中{len(activated_fits)}个在本批递推/干预情景的至少一条记录上由唯一COD限制。全部响应中{activated}条符合“训练β严格不活跃、该响应唯一COD”；其中{extra}条目标直接供给超当轮训练范围，{activated-extra}条未超该范围。它们不全由退化新触发：{audit['activated_from_recursive_sod']}条从同模型递推基准的唯一SOD转为唯一COD，{audit['activated_already_recursive_cod']}条基准已是COD，{audit['activated_from_recursive_near_tie']}条基准为近并列。第一张图只统计前一类，保留比例1时应为0。分母和训练不活跃模型数逐条件保存在CSV中，不把这些记录当作独立训练实验或显著性检验。

![固定示例](02_固定示例分支构成.png)

继续使用09预先指定的env_0/自由δ/B1→F1示例，不按结果挑例。第二图包含全部50轮模型，不限于“训练β严格不活跃”的子集，不能把第二图全部COD比例当成第一图的启用率。第一图基础式的零值也不能推出基础式更可靠或更准确，只说明本批模型未出现所定义的切换。

唯一SOD下，固定输入及其他参数时，局部输出对β的导数为0；唯一COD下为1；近并列不指定唯一导数。δ在min之后相加，固定输入时不改变SOD/COD选择，但上游δ仍可改变递推供给。因此图中分支变化不能单独归因于某个参数，更不能把局部斜率乘以大退化量当作全程响应。

保留比例0时，各模型绝对强制值均为0；非零比例下，各模型自己的基准可能不同。训练分支使用观测供给，留出基准与干预分支使用递推供给，不能把两者差异只归因于退化。另行保存“同模型递推基准SOD→干预COD”计数，避免混淆。

本步具体说明了原先“训练时不活跃参数可能在强退化时启用”的机制与本批证据；没有提高FDNA预测精度，也没有给出置信区间、真实因果或更通用拓扑验证。原09输出逐条保持一致，公式重算最大差{maximum:.3g}。
'''
    write_bundle(out,outputs);print(dumps(audit));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('propagation-dir','protocol','output-dir'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();run(args.propagation_dir,args.protocol,args.output_dir)
