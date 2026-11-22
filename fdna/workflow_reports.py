"""Build PNG/SVG presentation figures from frozen reports without refitting."""
import argparse
from collections import defaultdict
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch

from .workflow_common import csv_text,dumps
from .network_workflow import analyze_dependencies,read_samples

MODES=('base','fixed_delta','free_delta')
LABELS={'base':'基础式','fixed_delta':'固定δ','free_delta':'自由δ'}
COLORS={'base':'#3679A6','fixed_delta':'#DA963B','free_delta':'#48977A'}


def add_outputs(directory,outputs):
    directory=Path(directory)
    manifest_path=directory/'manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    for name,value in outputs.items():
        data=value if isinstance(value,bytes) else value.encode('utf-8')
        (directory/name).write_bytes(data);manifest[name]=hashlib.sha256(data).hexdigest()
    manifest_path.write_text(dumps(manifest),encoding='utf-8')


def savefig(outputs,name,fig):
    for ext in ('png','svg'):
        stream=io.BytesIO();fig.savefig(stream,format=ext,dpi=180,bbox_inches='tight');outputs[name+'.'+ext]=stream.getvalue()
    plt.close(fig)


def recovery_figures(directory):
    report=json.loads((directory/'report.json').read_text(encoding='utf-8'));jobs=report['jobs'];outputs={};table=[]
    fig,axes=plt.subplots(1,2,figsize=(11,4.6),layout='constrained')
    for ax,parameter,threshold in zip(axes,('alpha','beta'),(report['config_snapshot']['acceptance']['alpha_absolute_error'],report['config_snapshot']['acceptance']['beta_absolute_error'])):
        for k,mode in enumerate(MODES):
            values=[]
            for n in (3,5):
                errors=[p['absolute_error'] for j in jobs if j['case']=='recovery' and j['noise_sigma']==0 and j['mode']==mode and j['input_count']==n for p in j['parameter_comparison'] if p['parameter']==parameter]
                values.append(max(errors))
                table.append(dict(input_count=n,mode=mode,parameter=parameter,max_absolute_error=max(errors),threshold=threshold))
            ax.bar(np.arange(2)+(k-1)*.23,values,width=.23,label=LABELS[mode],color=COLORS[mode])
        ax.axhline(threshold,color='#8D3131',linestyle='--',label='预设恢复阈值')
        ax.set(xticks=[0,1],xticklabels=['3输入','5输入'],ylabel='参数最大绝对误差',title='α恢复' if parameter=='alpha' else 'β恢复')
        ax.set_yscale('symlog',linthresh=1e-8);ax.set_ylim(0,threshold*1.8);ax.grid(axis='y',alpha=.2);ax.legend(fontsize=9,loc='upper right')
    fig.suptitle('无噪声恢复组：全部重复与入边中的最大参数误差')
    savefig(outputs,'图01_多输入参数恢复',fig)
    fig,axes=plt.subplots(2,2,figsize=(11,8),layout='constrained')
    for r,n in enumerate((3,5)):
        for c,case in enumerate(('recovery','diagnostic')):
            ax=axes[r,c];all_metric_values=[]
            for k,mode in enumerate(MODES):
                means=[]
                for sigma in (0,2):
                    values=[m['rmse'] for j in jobs if (j['input_count'],j['case'],j['mode'],j['noise_sigma'])==(n,case,mode,sigma) for m in j['metrics'] if m['split']=='holdout' and m['target']=='clean']
                    means.append(np.mean(values))
                    all_metric_values.extend(values)
                    ax.scatter(np.full(len(values),int(sigma>0))+(k-1)*.035,values,s=22,alpha=.65,color=COLORS[mode])
                ax.plot([0,1],means,marker='o',color=COLORS[mode],label=LABELS[mode])
            ax.set(xticks=[0,1],xticklabels=['σ=0','σ=2'],ylabel='干净留出目标 RMSE（SE点）',title=f'{n}输入 / '+('恢复组' if case=='recovery' else '诊断组'))
            ax.set_yscale('symlog',linthresh=1e-6);ax.set_ylim(0,max(all_metric_values)*2);ax.grid(alpha=.2);ax.legend(fontsize=9)
    fig.suptitle('留出预测：散点为各次重复，折线为均值（非置信区间）')
    savefig(outputs,'图02_多输入留出预测',fig)
    fig,axes=plt.subplots(1,2,figsize=(11,4.5),layout='constrained')
    for ax,n in zip(axes,(3,5)):
        labels=[];truth=[];fitted=[]
        for case in ('recovery','diagnostic'):
            for mode in MODES:
                selected=[j for j in jobs if (j['input_count'],j['case'],j['mode'],j['noise_sigma'])==(n,case,mode,0)]
                labels.append(('恢复' if case=='recovery' else '诊断')+'\n'+LABELS[mode])
                truth.append(np.mean([j['truth_geometry']['rank']/j['truth_geometry']['parameter_count'] for j in selected]))
                fitted.append(np.mean([j['fit']['receivers'][0]['sensitivity']['local_jacobian_rank']/j['truth_geometry']['parameter_count'] for j in selected]))
        x=np.arange(len(labels));ax.bar(x-.18,truth,.36,label='真值处',color='#6F8094');ax.bar(x+.18,fitted,.36,label='拟合处',color='#63AA94')
        ax.set(xticks=x,xticklabels=labels,ylim=(0,1.1),ylabel='局部秩 / 参数个数',title=f'{n}输入 / σ=0');ax.legend();ax.grid(axis='y',alpha=.2)
    fig.suptitle('局部约束诊断：秩比例不代表全局唯一性或置信概率')
    savefig(outputs,'图03_多输入局部约束',fig)
    outputs['图表数据_恢复最大误差.csv']=csv_text(table)
    add_outputs(directory,outputs)


def demo_figures(directory):
    report=json.loads((directory/'workflow_report.json').read_text(encoding='utf-8'))
    rows=json.loads((directory/'输入_SE记录.json').read_text(encoding='utf-8'))
    # New checks replay frozen parameters; this never invokes the optimizer.
    analysis=analyze_dependencies(report,rows,report['config_snapshot']['analysis'])
    outputs={'dependency_analysis.json':dumps(analysis),'节点退化影响.csv':csv_text(analysis['effects']),
        '依赖传播路径.csv':csv_text(analysis['paths']),'退化核验状态.csv':csv_text(analysis['checks'])}
    network=report['network_snapshot'];jobs=report['jobs'];nodes=report['topology']['receivers']
    pos={'S1':(0,0),'D2':(1,0),'D4':(2,0),'S3':(3,0),'S8':(4,0),'S10':(5,0),
         'S5':(1.2,1.45),'S6':(2.2,1.45),'F7':(.9,2.9),'F9':(3.2,2.9),'F11':(5,2.9)}
    kind_colors={'S5':'#86B5D7','S6':'#E5B56D','F7':'#88BDA1','F9':'#E5B56D','F11':'#88BDA1'}
    fig,ax=plt.subplots(figsize=(11,6),layout='constrained')
    for e in network['edges']:
        ax.add_patch(FancyArrowPatch(pos[e['feeder']],pos[e['receiver']],arrowstyle='-|>',mutation_scale=13,linewidth=1.2,color='#6C7785',alpha=.65,shrinkA=23,shrinkB=23,connectionstyle='arc3,rad=0.03'))
    for n,(x,y) in pos.items():
        ax.scatter([x],[y],s=1300,color=kind_colors.get(n,'#D5DDE4'),edgecolor='#4F6070',zorder=3)
        ax.text(x,y,n,ha='center',va='center',fontsize=12,zorder=4)
    ax.set(xlim=(-.6,5.6),ylim=(-.5,3.45));ax.axis('off')
    ax.set_title('11节点18边独立生成网络\n蓝色：互补；橙色：几何；绿色：加权；灰色：外生根节点',fontsize=14)
    savefig(outputs,'图01_11节点独立机制网络',fig)
    fig,axes=plt.subplots(2,2,figsize=(12,8.3),layout='constrained');summary=[]
    for r,sigma in enumerate((0,2)):
        for c,kind in enumerate(('local','recursive')):
            ax=axes[r,c];x=np.arange(len(nodes))
            for k,mode in enumerate(MODES):
                means=[];sds=[]
                for n in nodes:
                    vals=[m['rmse'] for j in jobs if j['model']==mode and j['fit_group'].endswith('sigma'+str(sigma)) for m in j['metrics'] if (m['node'],m['split'],m['prediction'])==(n,'holdout',kind)]
                    means.append(float(np.mean(vals)));sds.append(float(np.std(vals,ddof=1)))
                    summary.append(dict(node=n,noise_sigma=sigma,prediction=kind,model=mode,mean_rmse=means[-1],sample_sd=sds[-1],repetitions=len(vals)))
                ax.bar(x+(k-1)*.24,means,.24,yerr=sds,capsize=2,color=COLORS[mode],label=LABELS[mode])
            ax.set(xticks=x,xticklabels=nodes,ylabel='留出 RMSE（SE点）',title=f'σ={sigma} / '+('局部预测' if kind=='local' else '整网递推'));ax.grid(axis='y',alpha=.2);ax.legend(fontsize=9)
    fig.suptitle('独立机制拟合：相同数据与划分，三次重复均值 ± 样本SD')
    savefig(outputs,'图02_局部与整网预测误差',fig)
    fig,axes=plt.subplots(1,3,figsize=(13,4.4),layout='constrained')
    for ax,node in zip(axes,('F7','F9','F11')):
        all_values=[]
        for mode in MODES:
            points=[p for j in jobs if j['model']==mode and j['fit_group'].endswith('sigma0') for p in j['points'] if p['node']==node and p['split']=='holdout']
            x=[p['target'] for p in points];y=[p['recursive_prediction'] for p in points]
            ax.scatter(x,y,s=12,alpha=.55,label=LABELS[mode],color=COLORS[mode]);all_values+=x+y
        lo=min(0,min(all_values)-2);hi=max(all_values)+2;ax.plot([lo,hi],[lo,hi],'--',color='#777777',linewidth=1)
        ax.set(xlim=(lo,hi),ylim=(lo,hi),title=node,xlabel='干净目标SE（σ=0）',ylabel='整网预测SE（未裁剪）');ax.legend(fontsize=9);ax.grid(alpha=.15)
    fig.suptitle('末端留出预测：保留偏差与所有原始预测值')
    savefig(outputs,'图03_末端预测与目标',fig)
    fig,axes=plt.subplots(1,3,figsize=(12,4.6),layout='constrained')
    for ax,flag,title in zip(axes,('LOCAL_RANK_DEFICIENT','DELTA_AT_BOUNDARY','PREDICTION_OUTSIDE_SE_RANGE'),('局部秩不足','δ触界','出现预测越界')):
        matrix=np.zeros((len(nodes),3))
        for i,n in enumerate(nodes):
            for k,mode in enumerate(MODES):
                local=[l for j in jobs if j['model']==mode for l in j['local_fits'] if l['node']==n]
                matrix[i,k]=sum(flag in l['flags'] for l in local)/len(local)
        ax.imshow(matrix,cmap='YlOrRd',vmin=0,vmax=1,aspect='auto')
        for i in range(len(nodes)):
            for k,mode in enumerate(MODES):
                label='不适用' if flag=='DELTA_AT_BOUNDARY' and mode!='free_delta' else f'{matrix[i,k]:.0%}'
                ax.text(k,i,label,ha='center',va='center',color='white' if matrix[i,k]>.65 else '#222222',fontsize=9)
        ax.set(xticks=range(3),xticklabels=[LABELS[m] for m in MODES],yticks=range(len(nodes)),yticklabels=nodes,title=title)
    fig.suptitle('诊断频率：每格6次局部拟合（3重复 × 2噪声），不是置信概率')
    savefig(outputs,'图04_参数约束与范围诊断',fig)
    root_order=report['topology']['order'];terminals=('F7','F9','F11')
    fig,axes=plt.subplots(1,3,figsize=(12,6),layout='constrained')
    selected=[e for e in analysis['effects'] if e['group'].endswith('sigma0') and e['retained_fraction']==0.]
    maximum=max(abs(e['effect']) for e in selected if e['effect'] is not None)
    for ax,mode in zip(axes,MODES):
        matrix=np.zeros((len(root_order),len(terminals)));exists=np.zeros_like(matrix,dtype=bool)
        for i,n in enumerate(root_order):
            for k,t in enumerate(terminals):
                vals=[-e['effect'] for e in selected if (e['intervened_node'],e['target'],e['model'])==(n,t,mode) and e['effect'] is not None]
                if vals: matrix[i,k]=np.mean(vals);exists[i,k]=True
        ax.imshow(matrix,cmap='Blues',vmin=0,vmax=maximum,aspect='auto')
        for i in range(len(root_order)):
            for k in range(len(terminals)):
                ax.text(k,i,f'{matrix[i,k]:.1f}' if exists[i,k] else '—',ha='center',va='center',color='white' if matrix[i,k]>.65*maximum else '#222222',fontsize=9)
        ax.set(xticks=range(3),xticklabels=terminals,yticks=range(len(root_order)),yticklabels=root_order,title=LABELS[mode],xlabel='下游末端',ylabel='被置零节点')
    fig.suptitle('模型内节点置零后的末端SE减少量\nσ=0，各重复首条留出基准的均值；—表示不作为该节点的下游响应统计')
    savefig(outputs,'图05_节点退化与末端响应',fig)
    outputs['图表数据_预测误差汇总.csv']=csv_text(summary)
    checks=[c for c in analysis['checks'] if c['status']=='checked']
    outputs['补充核验.json']=dumps(dict(intervention_core_crosschecks=sum(c.get('core_max_difference') is not None for c in checks),
        maximum_core_difference=max(c.get('core_max_difference') or 0 for c in checks),non_descendant_invariance=all(c['non_descendant_invariant'] for c in checks),
        wide_long_mapping_equal=read_samples(directory/'输入_SE宽表.csv',network)==read_samples(directory/'输入_SE长表.csv',network,'long'),
        refitted=False))
    add_outputs(directory,outputs)


def main():
    p=argparse.ArgumentParser();p.add_argument('--recovery-dir',type=Path,required=True);p.add_argument('--demo-dir',type=Path,required=True);a=p.parse_args()
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10})
    recovery_figures(a.recovery_dir);demo_figures(a.demo_dir)
    print('Created 8 presentation figures in PNG and SVG; no PDF or refitting.')

if __name__=='__main__':main()
