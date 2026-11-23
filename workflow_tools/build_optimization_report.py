"""Build presentation-ready figures from frozen optimization artifacts, without fitting."""
import argparse
import csv
import hashlib
import html
import json
from pathlib import Path
import textwrap

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np


NAVY = '#17334D'; TEAL = '#168C87'; BLUE = '#356F9D'; GOLD = '#C48B38'; RED = '#BD584E'
INK = '#26394A'; GRAY = '#6C7C88'; LIGHT = '#EEF3F6'; PAPER = '#FFFFFF'; PURPLE = '#8064A2'


def run(project_root, output_dir):
    root = Path(project_root).resolve(); out = Path(output_dir).resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('输出目录须不存在或为空')
    base = root/'generated/关于优化'; source = root/'generated/关于两篇连接/06_完整场景全流程运行'
    used = {}

    def read(path):
        used[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return json.loads(path.read_text(encoding='utf-8'))

    def verify_manifest(directory, filename):
        manifest = read(directory/filename)
        for name, expected in manifest.items():
            if hashlib.sha256((directory/name).read_bytes()).hexdigest() != expected:
                raise ValueError('源文件校验失败：'+name)
        return len(manifest)

    source_count = verify_manifest(source, '完整包校验.json')
    for directory in ('02_P1诊断集中总览','03_同条件简单预测基线_核验版','04_冻结预测成组稳定性','05_依赖边变更影响预览'):
        verify_manifest(base/directory, 'manifest.json')
    layers = read(source/'分层验收.json'); config = read(source/'00_冻结输入/experiment.json')
    counts = read(base/'02_P1诊断集中总览/整理计数.json')
    card = read(base/'02_P1诊断集中总览/诊断卡片/env_0__base__B1.json')
    baseline = read(base/'03_同条件简单预测基线_核验版/baseline_report.json')
    stability = read(base/'04_冻结预测成组稳定性/stability_report.json')
    mapping = read(base/'02_P1诊断集中总览/映射审计.json')
    delta = read(base/'02_P1诊断集中总览/δ对照.json')
    audit = read(base/'05_依赖边变更影响预览/案例复核.json')
    deletion = read(base/'05_依赖边变更影响预览/案例/remove_T1_flow_0/preview_report.json')
    shortcut = read(base/'05_依赖边变更影响预览/案例/add_T1_shortcut/preview_report.json')
    baseline_check = read(base/'03_同条件简单预测基线_核验版/独立标量复核.json')
    stability_check = read(base/'04_冻结预测成组稳定性/独立标量复核.json')
    metrics = [m for m in baseline['metrics'] if m['evaluation_scope']=='common_records' and m['split']=='holdout']
    groups = sorted({m['fit_group'] for m in metrics}); nodes = sorted({m['node'] for m in metrics})
    keys = [(g,n) for g in groups for n in nodes]
    lookup = {(m['fit_group'],m['node'],m['prediction'],m['method']):m for m in metrics}
    methods = ['fdna:base','fdna:free_delta','ols_intercept','training_mean']
    assert len(keys)==18 and all(m['evaluated']==30 for m in metrics)
    assert len(delta)==18 and len(mapping['nodes'])==9 and sum(e['exported'] for e in mapping['edges'])==6
    assert all(d[k+'_holdout_improvement']>0 for d in delta for k in ('local','recursive'))
    assert deletion['added_roots']==['B1'] and deletion['affected_nodes']==['B1','F1']
    assert shortcut['status']=='semantic_blocked' and source_count==94
    out.mkdir(parents=True); (out/'图表').mkdir(); (out/'图表数据').mkdir()
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,
        'font.size':11,'text.color':INK,'axes.labelcolor':INK,'xtick.color':GRAY,'ytick.color':GRAY,
        'axes.edgecolor':'#D5DFE6','pdf.fonttype':42,'ps.fonttype':42,'svg.fonttype':'path',
        'savefig.facecolor':'white'})
    sections = []; data_exports = {}
    pdf_path = out/'优化成果汇报图文.pdf'

    def page(number, title, subtitle):
        fig = plt.figure(figsize=(16,9),facecolor=PAPER)
        fig.text(.045,.951,'06 场景  /  优化成果与证据',fontsize=10.5,color=TEAL,weight='bold')
        fig.text(.045,.895,title,fontsize=25,color=NAVY,weight='bold')
        fig.text(.045,.851,subtitle,fontsize=11,color=GRAY)
        fig.text(.955,.951,f'{number:02d} / 06',ha='right',fontsize=11,color=GRAY)
        fig.add_artist(plt.Line2D([.045,.955],[.828,.828],transform=fig.transFigure,color='#E0E8ED',lw=1))
        return fig

    def footer(fig, left, right):
        for x, label, text in [(.045,'可以说明',left),(.515,'解释边界',right)]:
            fig.text(x,.133,label,fontsize=11,color=TEAL if x<.5 else GOLD,weight='bold')
            fig.text(x,.105,'\n'.join(textwrap.wrap(text,43)),fontsize=10.3,linespacing=1.6,va='top',color=INK)
        fig.text(.045,.018,'来源：06冻结结果及关于优化02-05包；全部为合成场景。图中数值来自已保存结果，本次未重新拟合。',
                 fontsize=8.3,color=GRAY)

    def save(fig, stem, title, p1, p2, pdf):
        fig.savefig(out/'图表'/(stem+'.png'),dpi=240)
        fig.savefig(out/'图表'/(stem+'.svg'))
        pdf.savefig(fig)
        sections.append(dict(stem=stem,title=title,paragraphs=[p1,p2])); plt.close(fig)

    def box(ax, x, y, w, h, title, body, color=TEAL):
        ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.012,rounding_size=0.018',
            fc=LIGHT,ec='none'))
        ax.text(x+.025,y+h-.055,title,color=color,fontsize=15,weight='bold',va='top')
        ax.text(x+.025,y+h-.125,body,color=INK,fontsize=11,va='top',linespacing=1.8)

    with PdfPages(pdf_path,metadata={'Title':'06场景优化成果与证据','Author':'研究结果汇报','Subject':'冻结合成实验的解释、对照与稳定性'}) as pdf:
        fig=page(1,'数据与依赖关系有可追溯的来路','从已选网到能力状态、产物消费、条件事件与SE；不把设计评分直接当SE')
        ax=fig.add_axes([.045,.22,.91,.57]); ax.set(xlim=(0,1),ylim=(0,1)); ax.axis('off')
        design=layers['chain_network']['actual']
        top=[('知识规则生成链网',f"{design['candidate_chains']}条候选链 · {design['combinations']}种组合\n{design['feasible_networks']}种可行网；同分按语义顺序选取"),
             ('定义能力状态','主体 + 功能 + 任务产物\n9个状态；共享主体关系单独登记'),
             ('登记直接依赖','12条候选关系审查\n6条直接消费边导出；6条未导出')]
        bottom=[('条件合格事件模拟',f"120个基础样本 × 3种环境\n每组条件200次微观重复；固定随机种子"),
                ('形成网络SE观测','360条网络观测，每条含9节点SE\nSE = 合格事件比例 × 100'),
                ('拟合与解释审查','90组训练 / 30组留出\n36次局部拟合；预测与参数诊断分开')]
        bottom.reverse()  # Serpentine flow: top-right dependency audit feeds lower-right event generation.
        xs=[.01,.355,.70]
        for ri,(row,ys) in enumerate([(top,.56),(bottom,.08)]):
            for x,(t,b) in zip(xs,row): box(ax,x,ys,.285,.34,t,b)
            for x in (.305,.65):
                a,b=(x,x+.04) if ri==0 else (x+.04,x)
                ax.annotate('',xy=(b,ys+.17),xytext=(a,ys+.17),arrowprops=dict(arrowstyle='->',color=TEAL,lw=2))
        ax.annotate('',xy=(.84,.43),xytext=(.84,.54),arrowprops=dict(arrowstyle='->',color=TEAL,lw=2))
        ax.text(.5,.48,'上游设计与能力依赖需显式映射；下方为同批网络的事件与估计流程',ha='center',fontsize=10,color=GRAY)
        footer(fig,'可以逐项追溯节点含义、SE定义、边的消费依据、划分和随机种子。','规则与假设已登记，不等于真实依赖已确认；当前SE是合成产物合格比例。')
        save(fig,'01_数据与依赖审计链路','数据与依赖的来路',
            '上游实际产生8条候选链、16种组合和4种可行网。经主体、功能和任务产物的显式映射，建立9个能力状态；12条候选关系中6条具有登记的直接消费依据并被导出。共享主体、通道可用和间接可达关系没有自动转换成依赖边。',
            'SE来自已登记的条件合格事件模拟：120个基础样本对应3种配对环境，共360条网络观测，每条含9个节点SE；每组条件有200次微观重复。按基础样本划分90组训练、30组留出，避免同一基础样本的环境版本跨集合。当前验证了可追溯的合成流程，不代表真实装备效能已校准。',pdf)

        fig=page(2,'预测误差可评估，参数解释仍有明确限制','诊断标记允许重叠；右侧例子说明低误差不能证明每个参数都被识别')
        ax=fig.add_axes([.205,.265,.36,.49])
        labels=['局部秩不足','多初值参数跨度','输入覆盖外推','δ触界（自由δ）','边参数触界']
        flag_keys=['LOCAL_RANK_DEFICIENT','MULTISTART_PARAMETER_SPREAD','PREDICTOR_COVERAGE_EXTRAPOLATION','DELTA_AT_BOUNDARY','EDGE_PARAMETER_AT_BOUNDARY']
        values=[counts['flag_counts'][f] for f in flag_keys]; den=[36,36,36,18,36]
        y=np.arange(5); ax.barh(y,values,color=[BLUE,BLUE,GOLD,RED,GOLD],height=.56)
        for i,(v,d) in enumerate(zip(values,den)): ax.text(v+.6,i,f'{v}/{d}',va='center',fontsize=12,weight='bold',color=INK)
        ax.set(yticks=y,yticklabels=labels,xlim=(0,41),xlabel='出现该标记的局部拟合次数'); ax.invert_yaxis()
        ax.grid(axis='x',alpha=.17); ax.set_axisbelow(True); ax.spines[['top','right','left']].set_visible(False); ax.tick_params(axis='y',length=0)
        attempts=[a for a in card['attempts'] if a['success'] and abs(a['rmse']-min(t['rmse'] for t in card['attempts']))<=1e-6]
        betas=[a['parameters'][1] for a in attempts]
        inset=fig.add_axes([.65,.39,.285,.23]); inset.scatter(betas,np.linspace(-.07,.07,len(betas)),s=80,color=TEAL,zorder=3)
        inset.plot([min(betas),max(betas)],[0,0],color=TEAL,alpha=.4,lw=4)
        inset.set(xlim=(45,90),ylim=(-.5,.5),yticks=[],xlabel='严格近等且成功起点的β值')
        inset.spines[['top','left','right']].set_visible(False); inset.set_title('env_0 / B1 / 基础式',fontsize=14,color=NAVY,pad=18)
        fig.text(.65,.71,f"{len(attempts)}个近等起点 · β约{min(betas):.1f}至{max(betas):.1f}",fontsize=13,weight='bold',color=TEAL)
        fig.text(.65,.29,'训练RMSE约3.75043；局部秩1/2\nβ在这些训练点不活跃；跨度不是置信区间',fontsize=11,linespacing=1.8)
        footer(fig,'36次拟合的误差、触界、秩、分支及多初值差异现在能集中查看。','秩不足不直接等于预测无效；局部满秩也不证明全局唯一。δ触界分母为18次自由δ拟合。')
        data_exports['诊断标记']=[dict(flag=f,label=l,count=v,denominator=d) for f,l,v,d in zip(flag_keys,labels,values,den)]
        data_exports['B1近等参数']=[dict(initial_source=a['initial_source'],rmse=a['rmse'],alpha=a['parameters'][0],beta=a['parameters'][1]) for a in attempts]
        save(fig,'02_诊断标记与参数跨度','参数诊断让解释更有边界',
            '36次局部拟合中，34次局部秩不足，36次存在多初值参数跨度，27次有输入覆盖外推标记；18次自由δ拟合中12次触界。这些标记可以重叠，不能相加作为拟合总数。诊断优化使这些证据集中可见，而不是只展示一组看似确定的参数。',
            f'具体到env_0/B1基础式，{len(attempts)}个严格近等且成功的起点训练误差几乎相同，但β从约{min(betas):.2f}变化到{max(betas):.2f}。该节点局部秩为1/2，β在当前训练分支不活跃。因此低误差不能证明α、β都被唯一识别；该跨度不是参数置信区间。',pdf)

        fig=page(3,'同条件对照：当前06场景未显示FDNA优于OLS','全部中间节点B与末端节点F均列出；每格为共同留出记录上的RMSE，单位为SE点，越低越好')
        cmap=LinearSegmentedColormap.from_list('error',['#F0F6F8','#AACBD7','#4C8CAC','#173F62'])
        vmax=max(m['rmse'] for m in metrics); axes=[]
        for ki,kind in enumerate(('local','recursive')):
            ax=fig.add_axes([.12+ki*.445,.235,.335,.52]); axes.append(ax)
            matrix=np.array([[lookup[g,n,kind,m]['rmse'] for m in methods] for g,n in keys])
            im=ax.imshow(matrix,cmap=cmap,vmin=0,vmax=vmax,aspect='auto')
            for i in range(18):
                for j in range(4): ax.text(j,i,f'{matrix[i,j]:.2f}',ha='center',va='center',fontsize=9.4,color='white' if matrix[i,j]>vmax*.56 else NAVY)
            ax.set_xticks(range(4),['基础式','自由δ','线性回归','训练均值']); ax.xaxis.tick_top(); ax.tick_params(axis='both',length=0,labelsize=10)
            ax.set_yticks(range(18),[g.replace('env_','e=')+' · '+n for g,n in keys]); ax.tick_params(axis='y',labelsize=9)
            for boundary in (5.5,11.5): ax.axhline(boundary,color='white',lw=3)
            ax.set_title('局部预测：使用供给观测' if kind=='local' else '整网递推：仅使用外部根输入',fontsize=13,color=NAVY,pad=27)
            ax.spines[:].set_visible(False)
        color_ax=fig.add_axes([.922,.235,.012,.52]); fig.colorbar(im,cax=color_ax,label='RMSE / SE点')
        footer(fig,'训练均值与线性回归只用训练集估计；四种方法使用相同划分与对应信息。','留出集此前已查看，本次不是新的独立测试；不能推广为所有结构中FDNA都不如OLS。')
        data_exports['四方法共同留出误差']=metrics
        save(fig,'03_四方法留出误差对照','公平基线明确了当前方法定位',
            '图中覆盖3种环境、6个接受节点以及局部和整网递推两种预测层次，每个条件均使用30条共同留出记录。局部比较使用相同供给观测；递推比较只获得相同外部根输入，中间节点由各自模型预测，避免给某种方法额外的目标信息。',
            '对自由δ而言，OLS的局部RMSE在18组中13组更低、4组数值近等、1组更高；递推对应12、4、2组。近等仅指绝对差不超过1e-8，不是显著性或应用容许误差。已有留出集此前已查看，本次不构成新的独立测试。当前06场景不支持FDNA预测优于OLS的主张；但基线没有α/β依赖语义，也不能据此推论所有数据与结构的通用方法排名。',pdf)

        fig=page(4,'自由δ相对基础式的误差改善仍然成立','沿用原FDNA拟合；正值 = 基础式RMSE − 自由δ RMSE；黑色菱形表示该自由δ拟合触界')
        dlookup={(d['fit_group'],d['node']):d for d in delta}
        for ki,kind in enumerate(('local','recursive')):
            ax=fig.add_axes([.12+ki*.445,.235,.355,.52]); vals=[dlookup[k][kind+'_holdout_improvement'] for k in keys]
            y=np.arange(18); ax.barh(y,vals,color=TEAL if ki==0 else BLUE,height=.62,alpha=.85)
            for i,k in enumerate(keys):
                if dlookup[k]['delta_boundary']: ax.scatter(vals[i],i,marker='D',color=INK,s=22,zorder=4)
                ax.text(vals[i]+max(vals)*.018,i,f'{vals[i]:.2f}',va='center',fontsize=9)
            ax.set(yticks=y,yticklabels=[g.replace('env_','e=')+' · '+n for g,n in keys],xlim=(0,max(vals)*1.20),xlabel='留出RMSE下降 / SE点')
            ax.invert_yaxis(); ax.set_title('局部预测改善' if ki==0 else '整网递推改善',fontsize=13,color=NAVY,pad=14)
            ax.tick_params(axis='y',length=0,labelsize=9); ax.grid(axis='x',alpha=.15); ax.set_axisbelow(True); ax.spines[['top','right','left']].set_visible(False)
            for boundary in (5.5,11.5): ax.axhline(boundary,color='#DDE6EB',lw=1)
        footer(fig,'本批18组局部与递推对照均观察到误差下降；这些已有改善没有被本轮优化否定。','12/18次自由δ触界；误差改善不等于δ或α/β识别改善，不能认定δ有唯一物理成因。')
        data_exports['自由δ配对改善']=delta
        save(fig,'04_自由δ改善与触界','保留改善证据，同时保留解释限制',
            '基础式和自由δ使用相同数据与划分。18组配对结果的局部和整网递推留出RMSE均下降，说明已运行的自由δ扩展在本批数据中改善了预测。末端递推的改善还包含上游预测变化向下游传递的作用，不能与单个局部方程的改善混为一谈。',
            '黑色菱形标记12次自由δ触界。原文引入δ用于修正归一化可能带来的误差；本项目中δ是否还吸收遗漏因素或结构失配，仍是诊断假设。误差下降不能证明δ具有唯一物理意义。本轮没有改公式、参数或边界，也没有增加固定δ实验。',pdf)

        fig=page(5,'一次误差差值之外：多数条件仍有样本构成波动','OLS − 自由δ；负值表示OLS误差较低。点为原样本差值，线为2000次成组重采样的中央95%经验范围')
        fig.text(.68,.803,'金色：经验范围含零    绿色：经验范围全负',fontsize=9,color=GRAY)
        slookup={(r['fit_group'],r['node'],r['prediction']):r for r in stability['contrasts'] if r['metric']=='rmse' and r['left']=='ols_intercept'}
        if len(slookup)!=36 or any(sum(r['central_range_contains_zero'] for r in slookup.values() if r['prediction']==kind)!=15 for kind in ('local','recursive')):
            raise ValueError('冻结稳定性结果与本批汇报条件不同，请重新审查标题和说明')
        all_low=min(r['q025'] for r in slookup.values()); all_high=max(r['q975'] for r in slookup.values())
        for ki,kind in enumerate(('local','recursive')):
            ax=fig.add_axes([.12+ki*.445,.235,.355,.52]); ax.axvline(0,color=GRAY,lw=1,ls='--')
            for i,(g,n) in enumerate(keys):
                r=slookup[g,n,kind]; color=GOLD if r['central_range_contains_zero'] else TEAL
                ax.plot([r['q025'],r['q975']],[i,i],color=color,lw=2.8,alpha=.85)
                ax.scatter(r['original'],i,color=color,s=30,zorder=3,edgecolors='white',linewidths=.5)
            ax.set(yticks=range(18),yticklabels=[g.replace('env_','e=')+' · '+n for g,n in keys],
                xlim=(all_low-.15,all_high+.15),xlabel='留出RMSE差 / SE点')
            ax.invert_yaxis(); ax.set_title(('局部' if ki==0 else '整网递推')+'：15/18组经验范围含零',fontsize=13,color=NAVY,pad=14)
            ax.tick_params(axis='y',length=0,labelsize=9); ax.grid(axis='x',alpha=.12); ax.spines[['top','right','left']].set_visible(False)
            for boundary in (5.5,11.5): ax.axhline(boundary,color='#DDE6EB',lw=1)
        footer(fig,'抽样单位是30个留出基础样本；每次抽样同时保留全部环境、节点、方法和两种预测层次。','模型保持冻结。经验范围不是参数置信区间，不作显著性判断，也不覆盖训练重拟合或退化传播不确定性。')
        data_exports['OLS自由δ稳定性']=list(slookup.values())
        save(fig,'05_冻结预测成组稳定性','稳定性分析限制了过强的方法排名',
            '从30个留出基础样本中有放回抽取30个，重复2000次。同一次抽样联合保留全部环境、节点、方法与局部/递推记录，不把配对环境版本或200次微观事件当成独立网络样本。原模型不重新拟合。',
            'OLS−自由δ的18组RMSE差，局部和递推均有15组的中央经验范围包含零。其余3组范围均为负，分别是env_0.3/F1、env_0.6/F1和env_0.6/F2。结果提示“多数点估计较低”不能直接提升为所有条件都稳定获胜。这些范围只描述当前冻结预测的样本构成波动，不是参数置信区间或显著性检验。',pdf)

        fig=page(6,'改边前先看影响，避免无意改变评价任务','10个只读案例：7个待审，3个语义阻断；未应用新拓扑、未重新拟合')
        ax=fig.add_axes([.045,.22,.91,.57]); ax.set(xlim=(0,1),ylim=(0,1)); ax.axis('off')
        ax.text(.02,.94,'已登记正式能力图',fontsize=16,weight='bold',color=NAVY)
        positions={}
        def node(x,y,label,fc=LIGHT,ec=BLUE,w=.075):
            ax.add_patch(FancyBboxPatch((x-w/2,y-.048),w,.096,boxstyle='round,pad=.008,rounding_size=.015',fc=fc,ec=ec,lw=1.3))
            ax.text(x,y,label,ha='center',va='center',fontsize=13,color=NAVY,weight='bold')
        def arrow(a,b,color=BLUE,style='-',rad=0):
            ax.add_patch(FancyArrowPatch(a,b,arrowstyle='-|>',mutation_scale=12,color=color,lw=1.7,linestyle=style,connectionstyle=f'arc3,rad={rad}'))
        for i,task in enumerate(('T1','T2','T3')):
            y=.75-.25*i; ax.text(.02,y,task,color=GRAY,va='center')
            for role,x in zip(('A','B','F'),(.12,.28,.44)):
                name=role+str(i+1); positions[name]=(x,y); node(x,y,name)
            arrow((.165,y),(.235,y)); arrow((.325,y),(.395,y))
        for x,label in zip((.12,.28,.44),('信息产品','指挥产品','效应回执')):
            ax.text(x,.86,label,ha='center',fontsize=10,color=GRAY)
        ax.text(.02,.075,'9个能力状态 · 6条直接产品消费边\n共享主体不自动增加跨任务依赖边',fontsize=10.5,color=GRAY,linespacing=1.6)
        ax.plot([.53,.53],[.05,.95],color='#DDE6EB',lw=1)
        ax.text(.58,.94,'删除 A1→B1 的预览',fontsize=15,weight='bold',color=NAVY)
        for label,x in [('A1',.62),('B1',.77),('F1',.92)]: node(x,.74,label,fc='#FFF2D9' if label=='B1' else LIGHT,ec=GOLD if label=='B1' else BLUE)
        arrow((.665,.74),(.725,.74),GRAY,'--'); ax.text(.695,.74,'×',color=RED,fontsize=24,ha='center',va='center')
        arrow((.815,.74),(.875,.74)); ax.text(.77,.59,'B1成为新增根；B1、F1需重算\nB1仍须保留在原评价目标中',ha='center',fontsize=11,color=INK,linespacing=1.7)
        ax.text(.58,.43,'尝试增加 A1→F1 捷径',fontsize=15,weight='bold',color=NAVY)
        for label,x in [('A1',.62),('B1',.77),('F1',.92)]: node(x,.27,label)
        arrow((.665,.27),(.725,.27)); arrow((.815,.27),(.875,.27))
        arrow((.62,.20),(.92,.20),RED,'--',.12)
        ax.text(.77,.055,'缺少直接消费及通道依据：语义阻断',ha='center',fontsize=10.5,color=RED)
        footer(fig,'预览显示新增根、受影响下游和需重处理的方程，提醒保持原输入信息与评价目标。','结构预览不等于拓扑数值实验；未实际修改正式图，也未重新拟合或确认真实效能依赖。')
        save(fig,'06_依赖边变更影响预览','结构审查提前暴露比较条件的变化',
            '本次预览覆盖6个单边删除、3条无直接产品消费的捷径以及1个全边删除，共10个案例，7个保留待审、3个语义阻断。正式能力图、候选审查状态和拟合参数均没有改变，源包操作前后94个文件校验一致。',
            '删除A1→B1会使B1成为根，B1与F1需要重算。若直接把B1观测真值当作新根输入，并从原接受节点误差表中去掉B1，就改变了信息集和评价任务。预览明确保留这些待处理事项；无消费与直接通道依据的A1→F1捷径也不会因为间接可达而被自动批准。这是结构审查功能，不是替代拓扑数值实验。',pdf)

    for name, rows in data_exports.items():
        fields=list(dict.fromkeys(k for row in rows for k in row))
        with (out/'图表数据'/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields); writer.writeheader()
            writer.writerows({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()} for row in rows)
    intro='本轮未改变FDNA公式与原拟合参数，主要补充解释、对照、稳定性和结构审查证据。已有自由δ相对基础式的预测误差改善仍成立；真实依赖与参数实际物理意义仍需外部数据验证。'
    md=['# 优化成果汇报图文','',intro,'','共6张图，PNG为3840×2160高清图片，SVG可缩放；同目录PDF为6页横向图文报告。每图下方附两段可用于汇报的说明。','']
    parts=[]
    for i,section in enumerate(sections,1):
        image='图表/'+section['stem']+'.png'
        md += [f"## 图{i}　{section['title']}",'',f"![{section['title']}]({image})",'',*sum(([p,''] for p in section['paragraphs']),[])]
        parts.append(f'<section id="figure-{i}"><h2>图{i}　{html.escape(section["title"])}</h2><img src="{image}" alt="{html.escape(section["title"])}">'+''.join('<p>'+html.escape(p)+'</p>' for p in section['paragraphs'])+'</section>')
    concluding='老师追问时，可分别回答数据来源、参数约束和方法比较三个问题：生成规则与计数记录可追溯；拟合完成不等于参数唯一；当前06场景不支持FDNA预测优于简单线性方法的主张。依赖表达和模型内传播的研究价值仍应单独论证。'
    md += ['## 汇报结论','',concluding,'','本轮图表只读取已保存结果，没有重新拟合、生成SE、修改δ边界或应用新拓扑。诊断标记可重叠；经验范围不作参数置信或显著性结论。','']
    (out/'汇报图文说明.md').write_text('\n'.join(md),encoding='utf-8')
    html_text='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>06场景优化成果与证据</title><style>
body{font-family:"Microsoft YaHei",sans-serif;background:#eef3f6;color:#26394a;margin:0;line-height:1.8}main{max-width:1200px;margin:40px auto;padding:0 24px}header,section{background:white;padding:32px;margin:24px 0;border-radius:12px}h1,h2{color:#17334d;line-height:1.4}img{width:100%;height:auto}p{max-width:1050px}a{color:#168c87}small{color:#6c7c88}@media print{body{background:white}main{margin:0;padding:0}section{break-inside:avoid;padding:12px;border-radius:0}header{padding:12px}}
</style><main><header><small>06合成场景 · 已有运行结果</small><h1>优化成果与证据</h1><p>'''+intro+'''</p><p><a href="优化成果汇报图文.pdf">打开6页PDF</a> · PNG / SVG与绘图数据保存在同目录。</p></header>'''+''.join(parts)+'<section><h2>汇报结论</h2><p>'+concluding+'</p></section></main></html>'
    (out/'汇报图文.html').write_text(html_text,encoding='utf-8')
    metadata=dict(source_files=used,source_package_verified_files=source_count,figures=sections,
        baseline_verification=baseline_check,stability_verification=stability_check,topology_preview_verification=audit,
        charts=6,pdf_pages=6,fdna_refitted=False,se_generated=False,topology_applied=False)
    (out/'图表来源与核验.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    inventory={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}
    (out/'manifest.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='generated',figures=6,pdf=str(pdf_path),output=str(out),source_verified_files=source_count),ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='将已有优化结果整理为汇报图表与配套说明，不重新拟合')
    parser.add_argument('--project-root',type=Path,required=True); parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(); run(args.project_root,args.output_dir)
