"""Compare seed models under identical absolute clamps, keeping signed changes."""
import argparse
from io import BytesIO
from pathlib import Path

import numpy as np

from .model import ModelError
from .prepared_prediction import PreparedTopology
from .propagation_stability import FIELDS
from .refit_propagation import read,sha,training_ranges
from .structure import digest
from .workflow_common import csv_text,dumps,write_bundle


PROTOCOL=dict(id='flow06_seed_common_absolute_input_v1',reference_seed=20261122,
    seeds=[20261122,2026112401,2026112402,2026112403,2026112404],fractions=[0,.25,.5,.75,1],
    reference='original_seed_predicted_start_baseline_times_fraction_per_group_model_base_case',
    comparison='same_absolute_clamp_across_seeds_within_group_model_base_case',
    signed_decrease='own_target_baseline_minus_common_clamp_target_output',
    fraction_one='not_assumed_identity_for_non_reference_seed',negative_decreases='retain_do_not_clip_or_exclude',
    complete_panel='all_same_30_holdout_base_cases_or_no_mean',additional_fitting=False,model_selection=False,
    confidence_interval=False,tolerance=1e-8,
    focus=dict(group='env_0',model='free_delta',intervened_node='B1',target='F1'),
    focus_basis='inherited_from_09_and_18_before_current_run')


def replay_absolute(network,record,rows,jobs,cases,pairs,fractions,ranges,reference):
    topology=PreparedTopology.from_network(network);nodes=list(topology.nodes);parents=dict(topology.parent_items)
    index={n:i for i,n in enumerate(nodes)}
    reference=np.asarray(reference,dtype=float)
    if reference.shape!=(len(jobs),len(cases),len(nodes)):raise ModelError('共同基准数组维度不符')
    if not fractions or len(set(fractions))!=len(fractions) or any(type(f) not in (int,float) or not np.isfinite(f) or not 0<=f<=1 for f in fractions):raise ModelError('保留比例无效')
    shape=(len(jobs),len(pairs),len(fractions),len(cases))
    arrays={n:np.full(shape,np.nan) for n in ('decrease','changed','forced')}
    arrays.update({n:np.zeros(shape,dtype=bool) for n in ('valid','outside','extrapolated','reference_exceeds_own','negative_decrease')})
    arrays['baseline']=np.full((len(jobs),len(cases),len(nodes)),np.nan)
    selected=[r for r in rows if r['split']=='holdout'];lookup={(r['fit_group'],r['base_case_id']):r for r in selected}
    if len(lookup)!=len(selected):raise ModelError('留出面板重复')
    for j,(group,mode) in enumerate(jobs):
        entries=[f for f in record['fits'] if (f['fit_group'],f['model'])==(group,mode)];fits={f['node']:f for f in entries}
        if len(entries)!=len(fits) or set(fits)!={n for n in nodes if parents[n]}:raise ModelError('接受节点拟合缺失或重复')
        if any(f['status']!='fitted' for f in entries):continue
        roots={n:np.array([lookup.get((group,c),{}).get('node_values',{}).get(n,np.nan) for c in cases]) for n in topology.roots}
        predictor=topology.bind(fits);baseline=predictor.predict(roots);arrays['baseline'][j]=baseline
        roots_valid=np.all(np.isfinite(np.array(list(roots.values()))),axis=0);states={}
        for k,(start,target) in enumerate(pairs):
            own=baseline[:,index[start]];ref=reference[j,:,index[start]]
            valid=roots_valid & np.isfinite(own) & (own>=0) & (own<=100) & np.isfinite(ref) & (ref>=0) & (ref<=100)
            for fi,fraction in enumerate(fractions):
                forced=np.where(valid,ref*fraction,np.nan)
                if (start,fraction) not in states:states[start,fraction]=predictor.predict(roots,(start,forced))
                state=states[start,fraction];old=baseline[:,index[target]];y=state[:,index[target]]
                mask=valid & np.isfinite(old) & np.isfinite(y);key=j,k,fi
                arrays['valid'][key]=mask;arrays['forced'][key]=forced;arrays['changed'][key]=np.where(mask,y,np.nan)
                arrays['decrease'][key]=np.where(mask,old-y,np.nan)
                arrays['outside'][key]=mask & ((y<0)|(y>100))
                arrays['reference_exceeds_own'][key]=mask & (forced>own+1e-8)
                arrays['negative_decrease'][key]=mask & (old-y < -1e-8)
                for feeder in parents[target]:
                    low,high=ranges[group,target,feeder];x=state[:,index[feeder]]
                    arrays['extrapolated'][key]|=mask & ((x<low-1e-8)|(x>high+1e-8))
                if np.any(mask & (forced<=own) & (old-y < -1e-8)):raise ModelError('低于自身基准的强制值违反单调性')
    return arrays


def summarize(fixed,relative,axes):
    rows=[];means=[];maximum=0.
    if fixed['decrease'].shape!=relative['decrease'].shape:raise ModelError('相对/绝对面板维度不同')
    for j,(group,mode) in enumerate(axes['jobs']):
        for k,(start,target) in enumerate(axes['pairs']):
            ni=axes['nodes'].index(target)
            for fi,fraction in enumerate(axes['fractions']):
                condition=dict(zip(FIELDS,(group,mode,start,target,fraction)));values={name:[] for name in ('fixed','relative','reference_contribution','baseline_contrast','output_contrast')}
                for si,seed in enumerate(axes['seeds']):
                    mask=fixed['valid'][si,j,k,fi] & relative['valid'][si,j,k,fi] & fixed['valid'][0,j,k,fi]
                    complete=bool(mask.all())
                    if complete:
                        fd=fixed['decrease'][si,j,k,fi];rd=relative['decrease'][si,j,k,fi]
                        bc=fixed['baseline'][si,j,:,ni]-fixed['baseline'][0,j,:,ni]
                        oc=fixed['changed'][si,j,k,fi]-fixed['changed'][0,j,k,fi]
                        residual=(fd-fixed['decrease'][0,j,k,fi])-(bc-oc)
                        error=float(np.max(np.abs(residual)));maximum=max(maximum,error)
                        if error>1e-8:raise ModelError('固定强制值对照分解未闭合')
                        current=dict(fixed=float(fd.mean()),relative=float(rd.mean()),reference_contribution=float((rd-fd).mean()),baseline_contrast=float(bc.mean()),output_contrast=float(oc.mean()))
                        for name,v in current.items():values[name].append(v)
                    else:current=dict.fromkeys(values)
                    means.append(dict(seed=seed,**condition,complete_baselines=complete,evaluated=int(mask.sum()),**current))
                row=dict(**condition,planned_models=len(axes['seeds']),complete_models=len(values['fixed']),baseline_records=len(axes['base_cases']))
                for name,vs in values.items():
                    row.update({name+'_'+stat:value for stat,value in dict(minimum=min(vs) if vs else None,median=float(np.median(vs)) if vs else None,maximum=max(vs) if vs else None,span=max(vs)-min(vs) if vs else None).items()})
                rows.append(row)
    return rows,means,maximum


def figures(summary,means,protocol):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'svg.fonttype':'path'})
    output={}
    def save(fig,name):
        for ext in ('png','svg'):
            b=BytesIO();fig.savefig(b,format=ext,dpi=170,bbox_inches='tight');output[name+'.'+ext]=b.getvalue()
        plt.close(fig)
    fig,ax=plt.subplots(figsize=(8,6),layout='constrained')
    for root,color,label in [(True,'#3d7599','根节点干预'),(False,'#168c87','内部节点干预')]:
        own=[r for r in summary if r['intervened_node'].startswith('A')==root and r['complete_models']==5]
        ax.scatter([r['relative_span'] for r in own],[r['fixed_span'] for r in own],c=color,label=label,s=26,alpha=.65)
    limit=max(max(r['relative_span'],r['fixed_span']) for r in summary if r['complete_models']) *1.05
    ax.plot([0,limit],[0,limit],ls='--',color='#777',lw=1);ax.set(xlabel='各模型自身比例：平均减少量的5种子跨度 / SE点',ylabel='共同绝对值：平均减少量的5种子跨度 / SE点',title='同一条件下，相对与共同绝对强制值的传播范围\n270个重复使用同一数据的条件；范围不是置信区间')
    difference=max(abs(r['fixed_span']-r['relative_span']) for r in summary if r['complete_models'])
    ax.text(.97,.05,f'两种跨度的最大差：{difference:.6f} SE点',ha='right',transform=ax.transAxes,fontsize=10)
    ax.legend();ax.grid(alpha=.2);save(fig,'01_相对与共同绝对值传播跨度')
    selected=[r for r in means if all(r[k]==v for k,v in protocol['focus'].items())]
    fig,axes=plt.subplots(1,2,figsize=(13,5),layout='constrained');colors=plt.cm.viridis(np.linspace(.1,.85,5))
    for si,seed in enumerate(protocol['seeds']):
        own=[r for r in selected if r['seed']==seed];label='原种子' if si==0 else '新'+str(si)
        axes[0].plot([r['retained_fraction'] for r in own],[r['reference_contribution'] for r in own],marker='o',label=label,color=colors[si])
    axes[0].set(xlabel='保留比例',ylabel='相对减少量 − 固定值减少量 / SE点',title='在当前模型内更换干预值造成的配对差');axes[0].legend()
    focus_max=max(abs(r['reference_contribution']) for r in selected if r['complete_baselines'])
    extent=max(.05,focus_max*1.1);axes[0].set_ylim(-extent,extent)
    axes[0].text(.03,.06,f'本预设示例最大绝对配对差：{focus_max:.3g} SE点\n数值近等阈值：1e-8；不将微小数值差放大解释',transform=axes[0].transAxes,fontsize=9)
    own=[r for r in selected if r['retained_fraction']==1]
    for name,label,style in [('baseline_contrast','目标基准差','o-'),('output_contrast','同强制值目标输出差','s--')]:
        axes[1].plot(range(5),[r[name] for r in own],style,label=label)
    axes[1].plot(range(5),[r['fixed'] for r in own],'x-',label='固定值有符号减少量')
    axes[1].set(xticks=range(5),xticklabels=['原种子','新1','新2','新3','新4'],ylabel='相对原种子的差值 / SE点',title='比例1：固定值未必是新模型自身基准');axes[1].legend()
    for ax in axes:ax.axhline(0,color='#777',lw=.7);ax.grid(alpha=.15)
    fig.suptitle('预先固定示例：env_0 / 自由δ / B1→F1\n固定值减少量差 = 目标基准差 − 同强制值输出差；不隔离单一参数、不证明真实因果',fontsize=12)
    save(fig,'02_固定示例的参考值与模型差异');return output


def run(relative_dir,protocol_path,output_dir):
    directory=Path(relative_dir).resolve();out=Path(output_dir).resolve();protocol=read(protocol_path)
    if protocol!=PROTOCOL:raise ModelError('与预登记绝对强制值方案不一致')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):raise ModelError('拒绝覆盖非空输出')
    if out==directory or out.is_relative_to(directory):raise ModelError('输出不能写入18号来源')
    from workflow_tools.verify_seed_propagation import verify
    upstream=verify(directory);old=read(directory/'seed_propagation_report.json');axes=old['axes']
    if axes['seeds']!=protocol['seeds'] or axes['fractions']!=protocol['fractions']:raise ModelError('来源轴标签不匹配')
    seed_dir=Path(old['source_seed_directory']);seed_report=read(seed_dir/'seed_report.json');source=Path(seed_report['source']['directory'])
    for protected in (seed_dir,source,Path(old['frozen_report_path']).parent):
        if out==protected or out.is_relative_to(protected):raise ModelError('输出不能写入旧来源包')
    with np.load(directory/'逐种子逐基准数组.npz',allow_pickle=False) as a:relative={k:a[k] for k in a.files}
    original=read(source/'04_FDNA拟合/workflow_report.json');network=original['network_snapshot'];rows=read(source/'04_FDNA拟合/输入_SE记录.json')
    train=sorted({r['base_case_id'] for r in rows if r['split']=='train'});ranges=training_ranges(network,rows,train)
    records=[read(seed_dir/f'逐种子记录/seed_{seed}.json') for seed in protocol['seeds']]
    runs=[replay_absolute(network,r,rows,[tuple(j) for j in axes['jobs']],axes['base_cases'],[tuple(p) for p in axes['pairs']],axes['fractions'],ranges,relative['baseline'][0]) for r in records]
    arrays={k:np.stack([r[k] for r in runs]) for k in runs[0]}
    if not np.array_equal(arrays['baseline'],relative['baseline'],equal_nan=True):raise ModelError('普通基准发生变化')
    for key in ('decrease','changed','forced'):
        if not np.allclose(arrays[key][0],relative[key][0],atol=1e-8,rtol=0,equal_nan=True):raise ModelError('原种子对照未复现')
        if not np.allclose(arrays[key][:,:,:,0],relative[key][:,:,:,0],atol=1e-8,rtol=0,equal_nan=True):raise ModelError('零值干预不一致')
    summary,means,maximum=summarize(arrays,relative,axes)
    report=dict(stage='seed_common_absolute_input_comparison',protocol=protocol,protocol_sha256=digest(protocol),axes=axes,
        source_relative_directory=str(directory),source_manifest_sha256=sha(directory/'manifest.json'),source_verification=upstream,
        summary=summary,audit=dict(responses=int(arrays['valid'].size),available=int(arrays['valid'].sum()),outside=int(arrays['outside'].sum()),
            extrapolated=int(arrays['extrapolated'].sum()),reference_exceeds_own=int(arrays['reference_exceeds_own'].sum()),negative_decreases=int(arrays['negative_decrease'].sum()),decomposition_maximum_residual=maximum),
        policies=dict(additional_fitting=False,seed_selection=False,output_clipping=False,confidence_interval=False))
    outputs={'absolute_report.json':dumps(report),'预登记方案.json':dumps(protocol),'相对与绝对传播范围.csv':csv_text(summary),'逐种子配对分解.csv':csv_text(means)}
    b=BytesIO();np.savez_compressed(b,**arrays);outputs['共同绝对值响应数组.npz']=b.getvalue();outputs.update(figures(summary,means,protocol))
    for name,value in read(directory/'manifest.json').items():
        if sha(directory/name)!=value:raise ModelError('运行中18号来源发生变化')
    from .flow_scenario import verify as verify_source
    verify_source(source);write_bundle(out,outputs);print(dumps(report['audit']));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('relative-dir','protocol','output-dir'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();run(args.relative_dir,args.protocol,args.output_dir)
