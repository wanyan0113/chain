"""Reporting for the preregistered abstract record-flow experiment."""
import shutil
from pathlib import Path
import numpy as np


def render_figures(out,doc,report,combos,slots,generation,fit,analysis,common):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    out=Path(out);out.mkdir(parents=True)
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'],'axes.unicode_minus':False})
    def save(fig,name):
        for ext in ('png','svg'):fig.savefig(out/(name+'.'+ext),dpi=170,bbox_inches='tight')
        plt.close(fig)
    tasks=sorted(doc['tasks'],key=lambda t:t['id']);assets=sorted(doc['artifacts'],key=lambda a:a['id'])
    matrix=np.asarray([[t['attributes']['target_type'] in a['attributes']['target_types'] for a in assets] for t in tasks],dtype=int)
    fig,ax=plt.subplots(figsize=(8,3));ax.imshow(matrix,vmin=0,vmax=1,cmap='Blues')
    ax.set_xticks(range(len(assets)),[a['id'] for a in assets]);ax.set_yticks(range(len(tasks)),[t['id']+' / '+t['attributes']['target_type'] for t in tasks])
    for y in range(len(tasks)):
        for x in range(len(assets)):ax.text(x,y,'适配' if matrix[y,x] else '不适配',ha='center',va='center',color='white' if matrix[y,x] else 'black')
    ax.set_title('抽象任务标签与主体适配；不是实际装备性能');save(fig,'01_任务装备适配')
    fig,axes=plt.subplots(1,2,figsize=(12,3.6))
    axes[0].bar([t['task'] for t in report['tasks']],[len(t['chains']) for t in report['tasks']],color='#335c81');axes[0].set(title='实际生成的候选链',ylabel='条数')
    axes[1].bar([c['index'] for c in combos],[1 for c in combos],color=['#34845a' if c['feasible'] else '#bd665b' for c in combos]);axes[1].set(title='全部组合：绿为容量可行，红为排除',xlabel='独立枚举序号',yticks=[])
    fig.tight_layout();save(fig,'02_候选链与全部组合')
    chosen={c['id']:c for t in report['tasks'] for c in t['chains'] if c['id'] in report['network']['chains']}
    ids=sorted({a for c in chosen.values() for a in c['artifacts'].values()})
    groups=[[a['id'] for a in assets if a['id'] in ids and a['role']==r] for r in ('insight','coordination','manifestation')]
    positions={n:(x,(len(nodes)-1)/2-i) for x,nodes in enumerate(groups) for i,n in enumerate(nodes)}
    fig,ax=plt.subplots(figsize=(9,4.5));ax.axis('off');ax.set(xlim=(-.4,2.4),ylim=(-1.6,1.6))
    for n,(x,y) in positions.items():ax.text(x,y,n,ha='center',va='center',fontsize=13,bbox=dict(boxstyle='round,pad=.5',fc='#edf3fa',ec='#335c81'))
    edges={(a,b) for c in chosen.values() for a,b in zip(c['artifacts'].values(),list(c['artifacts'].values())[1:])}
    for a,b in edges:
        x,y=positions[a];xx,yy=positions[b];ax.annotate('',xy=(xx-.1,yy),xytext=(x+.1,y),arrowprops=dict(arrowstyle='->',lw=1.5,color='#335c81'))
    ax.set_title('实际基准装备网：共享主体连接多个任务分支');save(fig,'03_基准装备网')
    fitdir=out.parent/'04_FDNA拟合'
    for old,new in [('01_装备到能力映射','04_任务能力依赖网'),('02_留出预测误差','06_留出局部与递推误差')]:
        for ext in ('png','svg'):shutil.copyfile(fitdir/(old+'.'+ext),out/(new+'.'+ext))
    fig,axes=plt.subplots(1,3,figsize=(12,3.5),sharey=True)
    for ax,k,title in zip(axes,range(3),('信息产品A','指令产品B','回执产品F')):
        for group in sorted({r['fit_group'] for r in generation['rows']}):
            vals=[r['node_values'][s['nodes'][k]] for r in generation['rows'] if r['fit_group']==group for s in slots]
            ax.hist(vals,bins=np.linspace(0,100,16),histtype='step',label=group,linewidth=1.5)
        ax.set(title=title,xlabel='观测SE',ylabel='记录频数');ax.legend(fontsize=8)
    fig.suptitle('合成记录质量分布；汇总任务不表示样本相互独立');fig.tight_layout();save(fig,'05_观测SE与覆盖')
    envs=sorted({j['fit_group'] for j in fit['jobs']});root=slots[0]['nodes'][0];target=slots[0]['nodes'][2]
    fig,axes=plt.subplots(1,len(envs),figsize=(5*len(envs),3.8),squeeze=False,sharey=True)
    for ax,group in zip(axes[0],envs):
        for mode in ('base','free_delta'):
            effects=sorted([e for e in analysis['effects'] if e['group']==group and e['model']==mode and e['intervened_node']==root and e['target']==target],key=lambda e:e['retained_fraction'])
            ax.plot([e['retained_fraction']*100 for e in effects],[e['intervened'] for e in effects],'-o',label=mode)
        ax.set(title=group,xlabel=root+'保留比例（%）',ylabel=target+'预测SE');ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.suptitle('首条留出基准的单产品退化；模型预测不裁剪');fig.tight_layout();save(fig,'07_单能力产品退化')
    group=envs[len(envs)//2];subject=slots[0]['information_subject']
    fig,axes=plt.subplots(1,len(slots),figsize=(5*len(slots),3.8),squeeze=False,sharey=True)
    for ax,slot in zip(axes[0],slots):
        target=slot['nodes'][2];items=[r for r in common if r['fit_group']==group and r['subject']==subject and r['target']==target]
        for mode in ('base','free_delta'):
            own=sorted([r for r in items if r['model']==mode],key=lambda r:r['retained_fraction'])
            ax.plot([r['retained_fraction']*100 for r in own],[r['prediction'] for r in own],'-o',label=mode)
        own=sorted([r for r in items if r['model']=='base'],key=lambda r:r['retained_fraction'])
        ax.plot([r['retained_fraction']*100 for r in own],[r['mechanism_reference'] for r in own],'--',color='black',label='生成机制期望')
        ax.set(title=target,xlabel=subject+'已知合成H保留比例（%）',ylabel='SE');ax.legend(fontsize=8);ax.grid(alpha=.2)
    fig.suptitle('已知合成来源情景：使用期望根值，区别于观测根值退化');fig.tight_layout();save(fig,'08_已知共享来源情景')
    flags=['LOCAL_RANK_DEFICIENT','DELTA_AT_BOUNDARY','EDGE_PARAMETER_AT_BOUNDARY','PREDICTION_OUTSIDE_SE_RANGE','PREDICTOR_COVERAGE_EXTRAPOLATION']
    labels=['局部秩不足','δ触界','边参数触界','预测越界','输入覆盖外推']
    counts=np.asarray([[sum(flag in l['flags'] for l in j['local_fits']) for flag in flags] for j in fit['jobs']])
    fig,ax=plt.subplots(figsize=(9,4.5));ax.imshow(counts,cmap='YlOrRd',vmin=0,vmax=len(slots)*2)
    ax.set_xticks(range(len(labels)),labels,fontsize=9);ax.set_yticks(range(len(fit['jobs'])),[j['fit_group']+'/'+j['model'] for j in fit['jobs']],fontsize=8)
    for y in range(counts.shape[0]):
        for x in range(counts.shape[1]):ax.text(x,y,str(counts[y,x]),ha='center',va='center')
    ax.set_title('诊断：每格为带标记的节点拟合数，不是观测条数');fig.tight_layout();save(fig,'09_参数与范围诊断')


def result_notes(design,counts,flags,common):
    return '\n'.join(['# 本次新场景结果说明','',
        '本包来自新任务、六主体与独立知识条目实际运行，未复用此前配送或旧链网报告。指标仅表示合成信息、指令、回执的规则合格比例。','',
        f"实际生成{design['candidate_chains']}条候选链，枚举{design['combinations']}种组合，{design['feasible_networks']}种容量可行。基准按语义排序首个选择，旧接口的同值指标不是性能评分。",'',
        f"已形成{counts['nodes']}个能力状态、{counts['edges']}条供给边。直接读取{counts['rows']}条观测SE，完成{counts['fitted']}/{counts['local_fits']}次局部拟合，生成{counts['effects']}条标准节点退化响应。",'',
        '每组SE来自200次逐级条件事件；跨环境复用对应均匀随机数，H按实际主体共享。微观事件、均匀数、状态、概率和划分保存在NPZ及审计JSON中。正式拟合不接收这些内部量。','',
        '模型诊断标记计数（节点拟合单元，不是样本条数）：','',
        *['- '+k+'：'+str(v) for k,v in sorted(flags.items())],'',
        f"另有{len(common)}条已知合成H情景记录，使用期望根值作为基准，不与普通观测根值退化混称，也不表示从观测SE识别出了H。",'',
        '分层验收分别保存在分层验收.json。模型适用性只列证据与限制；低误差不证明参数唯一，流程成功不证明真实因果。概率系数不是αβ真值，预测越界不事后裁剪。','',
        '图表见06_汇报图表；每张均有PNG和SVG。原始输入、知识推导、全部组合、映射草稿和登记、事件审计、拟合、共同来源情景分目录保存。',''])
