"""Figures and cautious interpretation for post-baseline frozen diagnostics."""
from collections import Counter,defaultdict
import io
import math
import statistics

from .generation_delta_repeats import distribution
from .generation_varying_experiment import csv_rows,dumps
from .model import ModelError
from .structure import digest


def summarize_geometries(reports,tolerance):
    grouped=defaultdict(list);seen=set()
    for report in reports:
        for row in report['geometries']:
            key=tuple(row[k] for k in ('model','group','noise_sigma','repetition','node'))
            if key in seen:raise ModelError('不同来源的参数诊断重复计数')
            seen.add(key);grouped[row['group'],row['model']].append(row)
    result=[]
    for (group,mode),rows in sorted(grouped.items()):
        probes=[r['null_probe'] for r in rows]
        feasible=[p for p in probes if p['feasible']]
        result.append(dict(group=group,model=mode,local_fits=len(rows),rank_deficient=sum(r['rank']<r['parameter_count'] for r in rows),
            feasible_probes=len(feasible),equivalent_train_and_holdout=sum(p['equivalent_on_sampled_inputs'] for p in feasible),
            train_equivalent_holdout_changed=sum(p['max_train_prediction_change']<=tolerance and p['max_holdout_prediction_change']>tolerance for p in feasible),
            feasible_delta_directions=sum(p['kind']=='delta_compensation_direction' for p in feasible),
            confirmed_delta_equivalence=sum(p['kind']=='delta_compensation_direction' and p['equivalent_on_sampled_inputs'] for p in feasible),
            confirmed_inactive_equivalence=sum(p['kind']=='inactive_parameter_direction' and p['equivalent_on_sampled_inputs'] for p in feasible)))
    return result


def confirmed_delta_paths(reports,tolerance):
    rows=[]
    for report in reports:
        for g in report['geometries']:
            probe=g['null_probe']
            if probe['kind']!='delta_compensation_direction' or not probe['feasible'] or not probe['equivalent_on_sampled_inputs']:continue
            if 'delta' not in g['parameter_names']:raise ModelError('δ方向缺少δ参数')
            index=g['parameter_names'].index('delta')
            changes=[b-a for a,b in zip(probe['parameters_before'],probe['parameters_after'])]
            if abs(changes[index])<=tolerance:raise ModelError('δ方向标签不能替代超过数值容差的实际δ变化')
            rows.append(dict(group=g['group'],model=g['model'],noise_sigma=g['noise_sigma'],repetition=g['repetition'],node=g['node'],
                delta_before=probe['parameters_before'][index],delta_after=probe['parameters_after'][index],delta_change=changes[index],
                parameter_changes=dumps(dict(zip(g['parameter_names'],changes))),
                max_train_prediction_change=probe['max_train_prediction_change'],max_holdout_prediction_change=probe['max_holdout_prediction_change']))
    return rows


def additional_findings(reports,tolerance):
    rows=confirmed_delta_paths(reports,tolerance)
    geometries=[g for report in reports for g in report['geometries']]
    feasible=[g['null_probe'] for g in geometries if g['null_probe']['feasible']]
    train_only=sum(p['max_train_prediction_change']<=tolerance and p['max_holdout_prediction_change']>tolerance for p in feasible)
    text=['','补充核验：涉及δ的近似等价路径均检查了参数前后值，要求δ实际变化超过1e−6，而不只依据方向标签。',
        f"本轮{sum(g['rank']<g['parameter_count'] for g in geometries)}/{len(geometries)}次局部秩不足；{len(feasible)}次预设扰动在界内可行，其中{sum(p['equivalent_on_sampled_inputs'] for p in feasible)}次在当前训练与验证输入上近似等价。实际确认{len(rows)}条δ变化路径，详细δ和其他参数变化见confirmed_delta_paths.csv。",'']
    if train_only==0:text+=['本轮未出现“训练预测近似不变、验证预测明显改变”的预设扰动。此前旧固定条件/不额外衰减基线中出现过此现象，属于不同数据集，不与本轮混计。','']
    text+=['这些路径只是在当前样本与既定步长下的证据，不能外推所有输入或所有节点。参数识别问题在此得到更具体的解释，尚未被解决。','']
    return rows,'\n'.join(text)


def build_bundle_report(reports,points,quality,config):
    if len(reports)!=len(config['sources']) or any(r['status']!='complete' for r in reports):raise ModelError('扩展诊断来源未全部完成')
    groups=[g for s in config['sources'] for g in s['expected_groups']]
    labels={'varying_conditions':'变化条件','varying_conditions_process_2':'过程σ=2',
        'interaction_005':'交互γ=0.05','interaction_015':'交互γ=0.15','interaction_030':'交互γ=0.30'}
    nodes=sorted({p['node'] for p in points},key=lambda n:({'T':0,'Q':1,'R':2}.get(n,3),n))
    geometry=summarize_geometries(reports,config['policy']['prediction_equivalence_tolerance'])
    counts={k:sum(r['counts'][k] for r in reports) for k in reports[0]['counts']}
    residuals=[r for report in reports for r in report['residual_summary']]
    metrics=[r for report in reports for r in report['residual_metrics']]
    outside=[r for report in reports for r in report['outside_predictions']]
    conditions=[]
    for row in outside:
        expected=row['base_before_delta']+row['delta']
        if not math.isclose(expected,row['predicted'],abs_tol=1e-8):raise ModelError('越界条目不满足加性δ恒等式')
        if row['predicted']<0 and not row['delta']< -row['base_before_delta']:raise ModelError('负值越界条件不成立')
        if row['predicted']>100 and not row['delta']>row['upper_headroom']:raise ModelError('上界越界条件不成立')
        conditions.append(dict(group=row['group'],model=row['model'],noise_sigma=row['noise_sigma'],repetition=row['repetition'],
            node=row['node'],split=row['split'],prediction=row['prediction'],observation_id=row['observation_id'],
            before_delta=row['base_before_delta'],delta=row['delta'],predicted=row['predicted'],
            side='below_zero' if row['predicted']<0 else 'above_100',branch=row['branch']))
    quality_aggregates=[]
    for group in groups:
        for node in nodes:
            for split in ('train','holdout'):
                rows=[r for r in quality['node_quality'] if (r['group'],r['node'],r['split'])==(group,node,split)]
                for name in ('target_below_min_fraction','base_local_rmse_lower_bound')+tuple('min_input_below_'+str(t)+'_fraction' for t in config['low_input_thresholds']):
                    quality_aggregates.append(dict(group=group,node=node,split=split,quantity=name,**distribution([r[name] for r in rows])))
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import rc_context
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    import numpy as np
    outputs={};modes=[('base','基础式','#367AA8'),('fixed_delta','固定δ','#32986B'),('free_delta','自由δ','#D78C36')]
    def aggregate(group,mode,node,sigma,quantity):
        return next(r for r in residuals if (r['group'],r['model'],r['node'],r['noise_sigma'],r['quantity'],r['split'],r['target'],r['prediction'])
            ==(group,mode,node,sigma,quantity,'holdout','clean','recursive'))
    def save(fig,name):
        for ext in ('png','svg'):
            stream=io.BytesIO();fig.savefig(stream,format=ext,dpi=180);outputs[name+'.'+ext]=stream.getvalue()
    with rc_context({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':9}):
        fig=Figure(figsize=(13,5.5),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,2)
        for ax,sigma in zip(axes,config['policy']['plot_noise_std']):
            for i,(mode,label,color) in enumerate(modes):
                rows=[aggregate(g,mode,'R',sigma,'mean_residual') for g in groups]
                ax.bar(np.arange(len(groups))+(i-1)*.24,[r['mean'] for r in rows],width=.24,yerr=[r['sd'] for r in rows],capsize=3,color=color,label=label)
            ax.set(xticks=range(len(groups)),xticklabels=[labels.get(g,g) for g in groups],ylabel='R整网平均残差（预测−干净目标）',title=f'观测σ={sigma}')
            ax.tick_params(axis='x',labelrotation=20);ax.axhline(0,color='#777777',linestyle='--');ax.grid(axis='y',alpha=.2);ax.legend(fontsize=8)
        fig.suptitle('新实验组的系统偏差方向（十次重复均值±样本标准差）');save(fig,'new_groups_residual_bias')
        fig=Figure(figsize=(13,3*len(groups)),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(len(groups),len(nodes),squeeze=False)
        for i,g in enumerate(groups):
            for j,n in enumerate(nodes):
                ax=axes[i,j]
                for mode,label,color in modes:
                    rows=[p for p in points if p['group']==g and p['node']==n and p['model']==mode and p['noise_sigma']==0]
                    ax.scatter([p['predicted'] for p in rows],[p['residual_clean'] for p in rows],s=5,alpha=.20,color=color,label=label,rasterized=True)
                ax.axhline(0,color='#777777',linestyle='--');ax.set(title=labels.get(g,g)+' / '+n,xlabel='整网预测SE',ylabel='干净目标残差');ax.grid(alpha=.15)
                if i==0 and j==0:ax.legend(fontsize=7)
        fig.suptitle('观测σ=0验证残差与预测值（配对条目仅作分布描述）');save(fig,'new_groups_residual_vs_prediction')
        fig=Figure(figsize=(13,8),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(2,3)
        for ax,g in zip(axes.flat,groups):
            for mode,label,color in modes:
                v=sorted(p['residual_clean'] for p in points if p['group']==g and p['node']=='R' and p['model']==mode and p['noise_sigma']==0)
                ax.step(v,np.arange(1,len(v)+1)/len(v),where='post',label=label,color=color)
            ax.axvline(0,color='#777777',linestyle='--');ax.set(title=labels.get(g,g),xlabel='R整网干净目标残差',ylabel='经验累计比例',ylim=(0,1));ax.grid(alpha=.15);ax.legend(fontsize=7)
        for ax in list(axes.flat)[len(groups):]:ax.axis('off')
        fig.suptitle('末端验证残差经验累计分布，观测σ=0（配对条目仅作描述）');save(fig,'new_groups_residual_cdf')
        fig=Figure(figsize=(12,3*len(reports)),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(len(reports),3,squeeze=False)
        for i,report in enumerate(reports):
            ex=report['illustration'];array=np.asarray([a['parameters'] for a in ex['near_starts']])
            for ax,prefix in zip(axes[i],('alpha:','beta:','delta')):
                indices=[j for j,k in enumerate(ex['parameter_names']) if k.startswith(prefix)]
                for k,j in enumerate(indices):
                    ax.vlines(k,float(array[:,j].min()),float(array[:,j].max()),linewidth=5,color='#D78C36')
                    ax.scatter(k,ex['selected_parameters'][j],marker='x',color='#222222',zorder=3)
                ax.set(xticks=range(len(indices)),xticklabels=[ex['parameter_names'][j] for j in indices],ylabel='参数值',
                    title=labels.get(ex['group'],ex['group'])+' / '+ex['node']+' / '+prefix.rstrip(':'))
                if prefix=='alpha:':ax.set_ylim(0,1)
                elif prefix=='beta:':ax.set_ylim(0,100)
                else:ax.set_ylim(*report['comparison_config_snapshot']['free_delta_bounds'][ex['node']])
                ax.grid(alpha=.15)
        fig.suptitle('预先指定示例：近等训练损失起点跨度（黑叉为选中值，非置信区间）');save(fig,'new_groups_near_start_spread')
        fig=Figure(figsize=(12,5),layout='constrained');FigureCanvasAgg(fig);ax=fig.subplots()
        for i,(key,label,color) in enumerate([('rank_deficient','局部秩不足','#367AA8'),('equivalent_train_and_holdout','扰动后训练与验证近似等价','#32986B'),
                ('train_equivalent_holdout_changed','训练近似不变但验证改变','#D78C36')]):
            values=[sum(r[key] for r in geometry if r['group']==g) for g in groups]
            ax.bar(np.arange(len(groups))+(i-1)*.24,values,width=.24,label=label,color=color)
        ax.set(xticks=range(len(groups)),xticklabels=[labels.get(g,g) for g in groups],ylabel='局部拟合/预设扰动条目数');ax.legend(fontsize=8);ax.grid(axis='y',alpha=.2)
        fig.suptitle('局部秩与有限扰动诊断（三模型、四档配对噪声，非独立样本计数）');save(fig,'new_groups_null_probes')
        chosen=[p for p in points if p['model']=='free_delta' and p['group']=='interaction_030' and p['node']=='R']
        fig=Figure(figsize=(11,4.7),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,2)
        for ax,window in zip(axes,('all','near')):
            selected=chosen if window=='all' else [p for p in chosen if p['base_before_delta']<20]
            for is_negative,label,color in [(False,'未低于0','#8295A5'),(True,'低于0','#CB4545')]:
                rows=[p for p in selected if (p['predicted']<0)==is_negative]
                ax.scatter([p['base_before_delta'] for p in rows],[p['delta'] for p in rows],s=12,alpha=.35,label=label,color=color,rasterized=True)
            limit=100 if window=='all' else 20
            ax.plot([0,limit],[0,-limit],'--',color='#333333',label='δ=−加项前输出')
            ax.set(xlim=(-5,limit),ylim=(-12,11),xlabel='加δ前的整网计算值',ylabel='δ',title='全范围' if window=='all' else '下界附近');ax.grid(alpha=.15);ax.legend(fontsize=8)
        fig.suptitle('强交互末端R验证负值条件（观测σ=0/8配对，阈值线以下产生负值）');save(fig,'new_groups_lower_range_condition')
        fig=Figure(figsize=(12,4.8),layout='constrained');FigureCanvasAgg(fig);axes=fig.subplots(1,len(nodes))
        for ax,n in zip(axes,nodes):
            for key,label,color in [('min_input_below_'+str(t)+'_fraction','最低供给<'+str(t),'#367AA8' if i==0 else '#D78C36') for i,t in enumerate(config['low_input_thresholds'])]+[
                    ('target_below_min_fraction','目标<最低供给','#32986B')]:
                rows=[next(r for r in quality_aggregates if (r['group'],r['node'],r['split'],r['quantity'])==(g,n,'holdout',key)) for g in groups]
                ax.errorbar(range(len(groups)),[r['mean'] for r in rows],yerr=[r['sd'] for r in rows],marker='o',capsize=3,color=color,label=label)
            ax.set(title=n,xticks=range(len(groups)),xticklabels=[labels.get(g,g) for g in groups],ylabel='验证样本比例');ax.tick_params(axis='x',labelrotation=25);ax.grid(alpha=.15);ax.legend(fontsize=7)
        fig.suptitle('实际干净供给覆盖与基础式下界违反比例（重复均值±SD，阈值仅作描述）');save(fig,'new_groups_input_coverage')
    lines=['# 新实验组深入诊断与供给数据质量检查','',
        '本轮复核变化C/M/E、过程σ=2、交互γ=0.05/0.15/0.30五个已冻结实验组，不重新拟合，不生成新样本，不改变机制、δ边界或输出不裁剪策略。20和40两个低输入阈值仅用于描述覆盖，不进入生成、优化或筛选。',
        '', '复核规模：'+dumps(counts)+'。预测条目包含模型、节点、噪声版本与局部/整网，不能当作独立实验次数。',
        '', '| 条件 | 模型 | 局部拟合数 | 秩不足 | 可行扰动 | 训练及验证近似等价 | 训练不变但验证改变 | 确认δ补偿方向 |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for row in geometry:
        lines.append(f"| {labels.get(row['group'],row['group'])} | {row['model']} | {row['local_fits']} | {row['rank_deficient']} | {row['feasible_probes']} | {row['equivalent_train_and_holdout']} | {row['train_equivalent_holdout_changed']} | {row['confirmed_delta_equivalence']} |")
    lines+=['','近等起点只按训练RMSE相差≤1e−6且收敛筛选；有限扰动幅度预先设定为归一化参数范围的0.1以内，并受实际参数边界约束。训练和验证预测变化均≤1e−6才称本次样本上近似等价，不构成全局等价或唯一性证明。训练近似不变但验证改变，说明不敏感性可能依赖输入覆盖；不利用验证变化选择或优化扰动方向。',
        '', '未找到某类补偿方向不能证明没有补偿；当前只检查预先定义的有限方向和步长。局部秩不足也不能一律解释成δ补偿，应区分不活跃参数和涉及δ的方向。',
        '', '| 条件 | 基础式R平均残差 | 固定δ R平均残差 | 自由δ R平均残差 | 自由δ R RMSE |',
        '| --- | ---: | ---: | ---: | ---: |']
    for g in groups:
        values=[aggregate(g,m,'R',0,'mean_residual')['mean'] for m,_,_ in modes]
        lines.append('| '+labels.get(g,g)+' | '+' | '.join(f'{v:.3f}' for v in values)+f" | {aggregate(g,'free_delta','R',0,'rmse')['mean']:.3f} |")
    lines+=['', '上表为观测σ=0、干净验证目标的整网结果，平均残差=预测−目标；正值表示高估。各组存在的残差结构不因RMSE改善而消失。局部与整网MSE保留交叉项，不将两种RMSE相减当作传播误差。',
        '', f'供给质量表共{len(quality["node_quality"])}条重复/划分/节点统计，按干净网络计数、不按观测噪声复制。记录实际供给SE范围、相关系数、各供给成为唯一/并列最低输入的频率、低输入覆盖、目标低于最低供给的比例、结构下界及生成截断；观测边界另存。常数输入相关系数保留空值并标注原因，不补0。',
        '', 'Pearson相关不证明因果；本来带共享因素的根节点也不应强制相关为0。阈值20/40为本轮明确的描述阈值，不代表原文给出的退化标准。几何、加权及交互传播会改变实际供给分布，不能假定中间供给和根节点服从同一分布。',
        '', f'全部{len(outside)}条越界记录按δ加项前输出独立核对，满足精确条件：预测<0当且仅当δ<−加项前输出；预测>100当且仅当δ>100−加项前输出。保留原值，不裁剪；局部/整网、训练/验证和噪声版本不能重复解释为独立观测。',
        '', '7张图分别展示残差方向、残差-预测关系、末端残差经验分布、预设示例近等起点跨度、秩及扰动、强交互负值条件、实际供给覆盖。完整逐样本残差、参数扰动与供给质量CSV按来源子目录保存。',
        '', '本轮补齐新组深入诊断与供给质量统计，没有解决参数不可辨识、模型表达限制或输出越界。下一步才开展其他生成系数系统敏感性，再进入正式多输入和11节点18边独立生成拟合。当前结论限定在已设合成机制及输入覆盖，不证明真实任务有效。','']
    meta=dict(status='complete',stage='post_baseline_extended_diagnostics',counts=counts,
        quality_bound_checks=sum(r['quality_bound_checks'] for r in reports),sources={s['id']:r['source_hashes'] for s,r in zip(config['sources'],reports)},
        geometry_summary=geometry,low_input_thresholds=config['low_input_thresholds'],new_fits=0,new_samples=0,
        report_hashes=[digest(r) for r in reports])
    delta_paths,supplement=additional_findings(reports,config['policy']['prediction_equivalence_tolerance'])
    outputs.update({'结果解释.md':'\n'.join(lines)+supplement,'extended_diagnostics_summary.json':dumps(meta),
        'confirmed_delta_paths.csv':csv_rows(delta_paths),
        'geometry_summary.csv':csv_rows(geometry),'residual_summary.csv':csv_rows(residuals),'residual_repeat_metrics.csv':csv_rows(metrics),
        'quality_repeat_summary.csv':csv_rows(quality_aggregates),'outside_condition_checks.csv':csv_rows(conditions)})
    outputs.update({k+'.csv':csv_rows(v) for k,v in quality.items()})
    return outputs
