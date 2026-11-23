"""Replay already-refitted experimental models; never fit or substitute parameters."""
import argparse
from io import BytesIO
import hashlib
import json
from pathlib import Path

import numpy as np

from .model import ModelError
from .propagation_stability import reachable_pairs, FIELDS
from .structure import digest
from .workflow_common import csv_text, dumps, make_model, write_bundle


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def layout(network):
    make_model(network)  # Validate nodes, duplicate edges and DAG before compiling.
    nodes=sorted(network['nodes'])
    parents={n:sorted(e['feeder'] for e in network['edges'] if e['receiver']==n) for n in nodes}
    order=[]
    while len(order)<len(nodes):
        ready=[n for n in nodes if n not in order and all(p in order for p in parents[n])]
        if not ready: raise ModelError('网络存在环')
        order.extend(ready)
    return nodes,parents,order


def predict(network, fits, roots, forced=None):
    """Batch DAG evaluation. Missing values propagate; internal observations are rejected."""
    nodes,parents,order=layout(network)
    if set(roots)!={n for n in nodes if not parents[n]}: raise ModelError('只接受全部根节点观测')
    values={n:np.asarray(v,dtype=float) for n,v in roots.items()}
    shapes={v.shape for v in values.values()}
    if len(shapes)!=1 or len(next(iter(shapes)))!=1: raise ModelError('根观测须为等长一维数组')
    shape=next(iter(shapes))
    if set(fits)!={n for n in nodes if parents[n]}: raise ModelError('接受节点模型不完整')
    if forced is not None:
        node,vector=forced
        vector=np.asarray(vector,dtype=float)
        if node not in nodes or vector.shape!=shape: raise ModelError('干预节点或维度错误')
    for n in order:
        if parents[n]:
            f=fits[n];params=f['parameters']
            if (len(params)!=len(parents[n]) or sorted(p['feeder'] for p in params)!=parents[n]
                    or f['status']!='fitted'): raise ModelError('参数供给关系或拟合状态错误')
            a=np.array([p['alpha'] for p in params]);b=np.array([p['beta'] for p in params]);d=f['delta'] or 0.
            if (not np.isfinite(a).all() or not np.isfinite(b).all() or not np.isfinite(d)
                    or np.any((a<0)|(a>1)) or np.any((b<0)|(b>100))): raise ModelError('参数非有限或越界')
            x=np.array([values[p['feeder']] for p in params])
            values[n]=np.minimum(np.mean(100-a[:,None]*(100-x),axis=0),np.min(x+b[:,None],axis=0))+d
        if forced is not None and forced[0]==n: values[n]=vector.copy()
    return np.stack([values[n] for n in nodes],axis=1)


def replay(network, record, rows, jobs, cases, pairs, fractions, ranges):
    nodes,parents,_=layout(network);index={n:i for i,n in enumerate(nodes)}
    shape=(len(jobs),len(pairs),len(fractions),len(cases))
    arrays={name:np.full(shape,np.nan) for name in ('decrease','changed','forced')}
    arrays.update({name:np.zeros(shape,dtype=bool) for name in ('valid','outside','extrapolated')})
    arrays['baseline']=np.full((len(jobs),len(cases),len(nodes)),np.nan)
    lookup={(r['fit_group'],r['base_case_id']):r for r in rows if r['split']=='holdout'}
    if len(lookup)!=len([r for r in rows if r['split']=='holdout']): raise ModelError('留出面板重复')
    for j,(group,mode) in enumerate(jobs):
        entries=[f for f in record['fits'] if (f['fit_group'],f['model'])==(group,mode)]
        fits={f['node']:f for f in entries}
        if len(entries)!=len(fits): raise ModelError('接受节点拟合重复')
        if set(fits)!={n for n in nodes if parents[n]}: raise ModelError('接受节点拟合缺失')
        if any(f['status']!='fitted' for f in entries): continue
        roots={n:np.array([lookup.get((group,c),{}).get('node_values',{}).get(n,np.nan) for c in cases])
               for n in nodes if not parents[n]}
        baseline=predict(network,fits,roots);arrays['baseline'][j]=baseline
        root_valid=np.all(np.isfinite(np.array(list(roots.values()))),axis=0)
        for k,(start,target) in enumerate(pairs):
            own=baseline[:,index[start]]
            valid=root_valid & np.isfinite(own) & (own>=0) & (own<=100)
            for fi,fraction in enumerate(fractions):
                forced=np.where(valid,own*fraction,np.nan)
                changed=predict(network,fits,roots,(start,forced))
                y=changed[:,index[target]];old=baseline[:,index[target]]
                mask=valid & np.isfinite(y) & np.isfinite(old)
                key=j,k,fi
                arrays['valid'][key]=mask
                arrays['forced'][key]=forced
                arrays['changed'][key]=np.where(mask,y,np.nan)
                arrays['decrease'][key]=np.where(mask,old-y,np.nan)
                arrays['outside'][key]=mask & ((y<0)|(y>100))
                for feeder in parents[target]:
                    low,high=ranges[group,target,feeder];x=changed[:,index[feeder]]
                    arrays['extrapolated'][key]|=mask & ((x<low-1e-8)|(x>high+1e-8))
                if np.any(arrays['decrease'][key][mask]<-1e-8): raise ModelError('违反单调性')
                if fraction==1 and np.any(abs(arrays['decrease'][key][mask])>1e-8): raise ModelError('原值干预不为零')
    return arrays


def training_ranges(network, rows, training_cases):
    _,parents,_=layout(network);groups=sorted({r['fit_group'] for r in rows})
    lookup={(r['fit_group'],r['base_case_id']):r for r in rows if r['split']=='train'}
    result={}
    for group in groups:
        for target,feeders in parents.items():
            for feeder in feeders:
                x=[lookup[group,c]['node_values'][feeder] for c in training_cases]
                result[group,target,feeder]=(min(x),max(x))
    return result


def summarize(arrays,jobs,pairs,fractions,original):
    old={tuple(r[f] for f in FIELDS):r for r in original['summary']};result=[]
    for j,(group,mode) in enumerate(jobs):
        for k,(start,target) in enumerate(pairs):
            for fi,fraction in enumerate(fractions):
                panel=arrays['decrease'][:,j,k,fi];valid=np.isfinite(panel).all(axis=1)
                means=panel[valid].mean(axis=1)
                key=(group,mode,start,target,fraction)
                result.append(dict(zip(FIELDS,key),planned_models=len(panel),complete_models=int(valid.sum()),
                    baseline_records=panel.shape[1],minimum=float(means.min()) if len(means) else None,
                    median=float(np.median(means)) if len(means) else None,maximum=float(means.max()) if len(means) else None,
                    original_mean=old[key]['mean_decrease'],outside_responses=int(arrays['outside'][:,j,k,fi].sum()),
                    target_extrapolated_responses=int(arrays['extrapolated'][:,j,k,fi].sum())))
    return result


def figures(report,focus):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'svg.fonttype':'path'})
    outputs={};labels={'base':'基础式','free_delta':'自由δ'}
    def save(fig,name):
        for ext in ('png','svg'):
            stream=BytesIO();fig.savefig(stream,format=ext,dpi=180,bbox_inches='tight');outputs[name+'.'+ext]=stream.getvalue()
        plt.close(fig)
    rows=[r for r in report['summary'] if r['retained_fraction']==0 and r['target']=='F1']
    fig,ax=plt.subplots(figsize=(12,8),layout='constrained')
    for i,r in enumerate(rows):
        if r['complete_models']:
            ax.plot([r['minimum'],r['maximum']],[i,i],color='#168c87',lw=3)
            ax.scatter(r['median'],i,c='#168c87',s=30,zorder=3)
        ax.scatter(r['original_mean'],i,c='#c56a30',marker='x',s=45,zorder=4)
    ax.plot([],[],color='#168c87',marker='o',label='50轮模型均值：最小—中位—最大')
    ax.scatter([],[],c='#c56a30',marker='x',label='原冻结模型均值')
    ax.set(yticks=range(len(rows)),yticklabels=[r['group']+' / '+labels[r['model']]+' / '+r['intervened_node'] for r in rows],
           xlabel='同一30个基准的F1平均SE减少量 / SE点',title='训练重估后：相对置零传播响应的模型敏感性\n线段不是置信区间；各模型使用自己的干预基准')
    ax.invert_yaxis();ax.grid(axis='x',alpha=.2);ax.legend(loc='best');save(fig,'01_重估模型末端响应范围')
    fig,axes=plt.subplots(1,2,figsize=(12,5),layout='constrained')
    beta=[r['target_beta'] for r in focus];y=[r['mean_decrease'] for r in focus]
    axes[0].scatter(beta,y,c='#168c87',alpha=.7);axes[0].set(xlabel='F1接收B1的β',ylabel='F1平均SE减少量',title='同批模型参数与传播的关联')
    axes[1].scatter([r['recursive_rmse'] for r in focus],y,c='#2c608a',alpha=.7)
    axes[1].set(xlabel='固定留出递推RMSE / SE点',ylabel='F1平均SE减少量',title='普通预测误差与退化响应')
    for ax in axes:ax.grid(alpha=.15)
    fig.suptitle('预先指定示例：env_0 / 自由δ / B1置零 → F1\n其他参数也同时变化；散点不能单独归因于β，不证明真实因果',fontsize=13)
    save(fig,'02_固定示例预测与传播');return outputs


def run(refit_dir,frozen_report,protocol_path,output_dir):
    from workflow_tools.verify_training_refit import verify
    out=Path(output_dir)
    if out.exists() and any(out.iterdir()): raise ModelError('拒绝覆盖非空输出目录')
    directory=Path(refit_dir);protocol=read(protocol_path)
    if digest(protocol)!='0f6614b8249155123e3f993930a5532af3362ad1c06818a76f9e84164d8a39af':
        raise ModelError('与登记方案不一致')
    previous_audit=verify(directory);refit=read(directory/'refit_report.json');source=Path(refit['source']['directory'])
    original=read(frozen_report);network=original['network_snapshot']
    if original['input_sha256']!=refit['source']['input_sha256']: raise ModelError('冻结传播与重估来源不同')
    # Verify the complete frozen output package, including its large response table.
    for name,expected_sha in read(Path(frozen_report).parent/'manifest.json').items():
        if sha(Path(frozen_report).parent/name)!=expected_sha: raise ModelError('冻结传播文件摘要错误: '+name)
    rows=read(source/'04_FDNA拟合/输入_SE记录.json');draws=read(directory/'训练成组抽样索引.json')
    cases=draws['holdout_base_cases'];jobs=sorted({(f['fit_group'],f['model']) for f in read(directory/'控制复跑.json')['record']['fits']})
    pairs=reachable_pairs(network);fractions=protocol['retained_fractions'];nodes=sorted(network['nodes'])
    ranges=training_ranges(network,rows,draws['training_base_cases'])
    control=replay(network,read(directory/'控制复跑.json')['record'],rows,jobs,cases,pairs,fractions,ranges)
    sample_to_case={(r['fit_group'],r['sample_id']):r['base_case_id'] for r in rows if r['split']=='holdout'}
    maximum=0.;keys=set()
    for e in original['analysis']['effects']:
        j=jobs.index((e['group'],e['model']));k=pairs.index((e['intervened_node'],e['target']))
        fi=fractions.index(e['retained_fraction']);b=cases.index(sample_to_case[e['group'],e['baseline_sample']]);key=j,k,fi,b
        if key in keys: raise ModelError('冻结响应重复')
        keys.add(key)
        for a,value in [(control['decrease'][key],-e['effect']),(control['changed'][key],e['intervened']),
                        (control['forced'][key],e['forced_value']),(control['baseline'][j,b,nodes.index(e['target'])],e['baseline'])]:
            error=abs(a-value)
            if not np.isfinite(error) or error>1e-8: raise ModelError('控制传播未复现')
            maximum=max(maximum,float(error))
    if len(keys)!=len(jobs)*len(pairs)*len(fractions)*len(cases): raise ModelError('控制响应不完整')
    runs=[];focus=[];source_hashes={};paths=sorted((directory/'逐次记录').glob('repetition_*.json'))
    if len(paths)!=protocol['source_training_repetitions']: raise ModelError('轮数错误')
    for i,path in enumerate(paths):
        record=read(path);source_hashes[path.name]=sha(path)
        selected=[draws['training_base_cases'][x] for x in draws['indices'][i]]
        current=replay(network,record,rows,jobs,cases,pairs,fractions,training_ranges(network,rows,selected));runs.append(current)
        f=protocol['focus_plot'];j=jobs.index((f['group'],f['model']));k=pairs.index((f['intervened_node'],f['target']));fi=fractions.index(f['retained_fraction'])
        target=next(x for x in record['fits'] if (x['fit_group'],x['model'],x['node'])==(f['group'],f['model'],f['target']))
        metric=next(x for x in record['metrics'] if (x['fit_group'],x['model'],x['node'],x['split'],x['prediction'])==(f['group'],f['model'],f['target'],'holdout','recursive'))
        panel=current['decrease'][j,k,fi]
        focus.append(dict(repetition=i+1,target_beta=next(p['beta'] for p in target['parameters'] if p['feeder']==f['intervened_node']),
                          mean_decrease=float(panel.mean()) if np.isfinite(panel).all() else None,recursive_rmse=metric['rmse']))
    arrays={name:np.stack([r[name] for r in runs]) for name in runs[0]}
    summary=summarize(arrays,jobs,pairs,fractions,original)
    report=dict(stage='refitted_relative_degradation_sensitivity',protocol=protocol,summary=summary,
        axes=dict(jobs=jobs,pairs=pairs,fractions=fractions,base_cases=cases,nodes=nodes),source_refit_directory=str(directory.resolve()),
        source_record_sha256=source_hashes,source_manifest_sha256=sha(directory/'manifest.json'),
        frozen_report_sha256=sha(frozen_report),input_sha256=refit['source']['input_sha256'],
        audit=dict(control_responses=len(keys),control_max_difference=maximum,responses=int(arrays['valid'].size),
            available_responses=int(arrays['valid'].sum()),outside_responses=int(arrays['outside'].sum()),
            target_extrapolated_responses=int(arrays['extrapolated'].sum()),
            complete_condition_models=sum(r['complete_models'] for r in summary),planned_condition_models=len(summary)*len(runs)),
        previous_refit_verification=previous_audit)
    outputs={'propagation_report.json':dumps(report),'固定方案.json':dumps(protocol),
             '传播响应摘要.csv':csv_text(summary),'固定示例.csv':csv_text(focus)}
    stream=BytesIO();np.savez_compressed(stream,**arrays);outputs['逐模型逐基准数组.npz']=stream.getvalue()
    outputs.update(figures(report,focus))
    focus_summary=next(r for r in summary if all(r[k]==protocol['focus_plot'][k] for k in FIELDS))
    outputs['结果说明.md']=f'''# 重估模型的相对退化传播敏感性

对08的50轮训练重估模型，保留同一30个留出根观测和五档保留比例，重算405000条可达下游响应；不重新拟合，不更换拓扑，也不覆盖原模型。先用完整训练控制模型回放07的{len(keys)}条响应，最大数值差为{maximum:.3g}。

![模型响应范围](01_重估模型末端响应范围.png)

每轮先对同一30个基准求平均，再报告50轮均值的最小、中位、最大；橙色叉为原模型均值。不同于07的冻结模型基准构成波动，本图包含训练构成变化后的重新估计。范围不是置信区间，不将50×30个组合视为1500个独立训练实验。

![固定示例](02_固定示例预测与传播.png)

运行前固定示例为env_0/自由δ/B1置零→F1：原模型平均减少量为{focus_summary['original_mean']:.3f}，重估模型为{focus_summary['minimum']:.3f}—{focus_summary['maximum']:.3f}，中位数{focus_summary['median']:.3f} SE点。图中参数、误差与响应的关联不能单独归因于β；其他参数同时变化。训练区间内不活跃的参数，可能在强退化时参与计算，普通预测误差不能代替传播响应稳定性的检查。

本轮{report['audit']['available_responses']}/{report['audit']['responses']}条响应可用，目标输出越界{report['audit']['outside_responses']}条，目标直接供给超当轮重采样训练范围{report['audit']['target_extrapolated_responses']}条；保留原值，不裁剪。后者只描述目标直接供给覆盖，不代表整条路径。各模型干预值为自身基准×比例，绝对值可能不同，因此这里是相对退化情景的模型敏感性。

单个任务任何接受节点拟合失败则整任务不替补；基准缺失保留NaN和valid掩码，只有完整30基准面板才计算模型均值，分母固定50。本轮不能证明参数唯一、物理意义、真实因果或FDNA优于线性基线，也未覆盖其他结构、求解器或真实数据的不确定性。此前已查看的留出集不作为新的独立测试。
'''
    write_bundle(out,outputs);print(dumps(report['audit']));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('refit-dir','frozen-report','protocol','output-dir'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();run(args.refit_dir,args.frozen_report,args.protocol,args.output_dir)
