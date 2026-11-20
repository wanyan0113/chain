"""Report figures from an audited saved recovery run; never refit or alter data."""
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import sys

from source_data.common import read_json, write_outputs
from .model import ModelError, _finite
from .recovery import independent_forward, validate_recovery
from .residuals import diagnose_residuals
from .structure import digest


def prepare_report(report):
    _finite(report)
    config=report['config_snapshot']
    validate_recovery(config)
    if report['input_sha256']!=digest(config):
        raise ModelError('实验报告与配置指纹不一致')
    if [c['id'] for c in report['cases']]!=[c['id'] for c in config['cases']]:
        raise ModelError('参数恢复案例与配置不一致')
    for case,truth in zip(report['cases'],config['cases']):
        if case['known_parameters']!=truth:
            raise ModelError('预设真值与配置不一致')
        fit=case['fit_report']
        audit=diagnose_residuals(case['fitting_case'],fit)
        if fit['settings_snapshot']['model']!='fdna_base_eq1_eq2':
            raise ModelError('本图表入口只接受第一阶段基础式恢复报告')
        parameters={p['feeder']:p for p in fit['receivers'][0]['parameters']}
        for p in case['parameter_comparison']:
            i=('A','B').index(p['feeder'])
            known=truth[p['parameter']][i]
            fitted=parameters[p['feeder']][p['parameter']]
            if any(abs(a-b)>1e-15 for a,b in [
                (p['known'],known),(p['fitted'],fitted),(p['error'],fitted-known),
                (p['absolute_error'],abs(fitted-known))]):
                raise ModelError('参数对照表与已核验拟合结果不一致')
        for split in ('train','holdout'):
            metric=fit['receivers'][0]['metrics'][split]
            records=[r for r in fit['samples_snapshot']['records'] if r['split']==split]
            expected=config['random_train'] if split=='train' else config['random_holdout']
            if len(records)!=expected:
                raise ModelError('样本数量与配置不一致')
            for row in records:
                values=row['node_values']
                target=independent_forward(values['A'],values['B'],truth['alpha'],truth['beta'])[0]
                if abs(values['R']-target)>config['acceptance']['forward_absolute_error']:
                    raise ModelError('保存样本不符合预设真值的无噪声生成式')
            audited=next(s['metrics'] for s in audit['series'] if s['split']==split)
            if any(not math.isclose(case['metrics'][split][k],audited[k],rel_tol=1e-12,abs_tol=1e-15)
                   for k in audited):
                raise ModelError('展示指标与独立残差审查不一致')
    return config


def build_figures(report):
    config=prepare_report(report)
    import numpy as np
    import matplotlib as mpl
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib import font_manager

    available={f.name for f in font_manager.fontManager.ttflist}
    chinese=next((n for n in ['Microsoft YaHei','SimHei','Noto Sans CJK SC','Arial Unicode MS'] if n in available),None)
    if chinese is None:
        raise ModelError('绘制中文报告图需要微软雅黑、黑体或Noto CJK字体')
    styles={'font.family':'sans-serif','font.sans-serif':[chinese,'DejaVu Sans'],
            'font.size':10,'axes.titlesize':11,'axes.labelsize':10,'axes.unicode_minus':False,
            'svg.fonttype':'path','figure.facecolor':'white','axes.spines.top':False,
            'axes.spines.right':False,'savefig.facecolor':'white'}
    outputs={}
    cases=report['cases']
    names=[f'参数组{i+1}' for i in range(len(cases))]
    colors=['#2563A6','#D77B25','#318368']

    def figure(size):
        fig=Figure(figsize=size,layout='constrained')
        FigureCanvasAgg(fig)
        return fig

    def save(fig,name):
        for kind in ('png','svg'):
            stream=io.BytesIO()
            fig.savefig(stream,format=kind,dpi=300)
            outputs[name+'.'+kind]=stream.getvalue()

    with mpl.rc_context(styles):
        # Paired bars show parameter scale; exact discrepancies belong in the error table.
        fig=figure((10.6,4.5)); axes=fig.subplots(1,2)
        for axis,param,symbol in zip(axes,['alpha','beta'],['α','β']):
            points=[p for c in cases for p in c['parameter_comparison'] if p['parameter']==param]
            x=np.arange(len(points)); w=.36
            axis.bar(x-w/2,[p['known'] for p in points],w,label='预设值',color='#2563A6')
            axis.bar(x+w/2,[p['fitted'] for p in points],w,label='拟合值',color='#EDAC65')
            axis.set_xticks(x,[f'{names[i//2]}\n{p["feeder"]}→R' for i,p in enumerate(points)])
            axis.set(ylabel=symbol+'参数值',title=('（a）依赖强度 α' if param=='alpha' else '（b）依赖关键度 β'),
                     ylim=(0,1 if param=='alpha' else 100))
            axis.grid(axis='y',alpha=.18); axis.set_axisbelow(True); axis.legend(frameon=False)
        fig.suptitle('预设参数与拟合参数对照',fontsize=13)
        save(fig,'figure_01_parameter_recovery')

        fig=figure((4*len(cases),4.2)); axes=fig.subplots(1,len(cases),squeeze=False)[0]
        for i,(case,axis) in enumerate(zip(cases,axes)):
            metric=case['fit_report']['receivers'][0]['metrics']['holdout']
            axis.plot([0,100],[0,100],color='#52606D',ls='--',lw=1,label='理想预测 y=x')
            axis.scatter([p['observed'] for p in metric['rows']],
                         [p['predicted'] for p in metric['rows']],s=15,alpha=.6,color=colors[i%3],label='独立验证样本')
            axis.set(title=names[i],xlabel='生成的接受节点SE',ylabel='FDNA预测SE',
                     xlim=(0,100),ylim=(0,100),aspect='equal')
            axis.text(.04,.95,f'n={metric["count"]}\nRMSE={metric["rmse"]:.3e}',
                      transform=axis.transAxes,va='top',fontsize=9,
                      bbox=dict(facecolor='white',edgecolor='none',alpha=.85))
            axis.grid(alpha=.15)
            if i==0: axis.legend(loc='lower right',frameon=False,fontsize=8)
        fig.suptitle('独立验证集预测结果',fontsize=13)
        save(fig,'figure_02_holdout_prediction')

        fig=figure((4*len(cases),4.1)); axes=fig.subplots(1,len(cases),squeeze=False)[0]
        maximum=max(abs(p['residual']) for c in cases
                    for p in c['fit_report']['receivers'][0]['metrics']['holdout']['rows'])
        for i,(case,axis) in enumerate(zip(cases,axes)):
            metric=case['fit_report']['receivers'][0]['metrics']['holdout']
            axis.scatter([p['predicted'] for p in metric['rows']],
                         [p['residual'] for p in metric['rows']],s=13,alpha=.6,color=colors[i%3])
            axis.axhline(0,color='#52606D',ls='--',lw=1)
            limit=maximum*1.15 if maximum else 1e-15
            axis.set(title=names[i],xlabel='FDNA预测SE',ylabel='残差 预测−生成（SE分值）',
                     xlim=(0,100),ylim=(-limit,limit))
            axis.ticklabel_format(axis='y',style='sci',scilimits=(0,0),useMathText=True)
            axis.grid(alpha=.15)
        fig.suptitle('独立验证集残差  三组使用相同纵轴范围',fontsize=13)
        save(fig,'figure_03_holdout_residuals')

        fig=figure((4*len(cases),4.2)); axes=fig.subplots(1,len(cases),squeeze=False)[0]
        labels=['SoD控制','CoD A→R控制','CoD B→R控制','分支相等']
        branch_colors=['#2563A6','#D77B25','#8059A4','#687887']
        coverage_rows=[]; legend_items={}
        for i,(case,axis) in enumerate(zip(cases,axes)):
            rows=[r for r in case['fit_report']['samples_snapshot']['records'] if r['split']=='train']
            groups=[[],[],[],[]]
            truth=case['known_parameters']
            for row in rows:
                v=row['node_values']; _,sod,ca,cb=independent_forward(v['A'],v['B'],truth['alpha'],truth['beta'])
                branch=0 if sod<min(ca,cb)-1e-7 else 1 if ca<min(sod,cb)-1e-7 else 2 if cb<min(sod,ca)-1e-7 else 3
                groups[branch].append((v['A'],v['B']))
            for label,color,points in zip(labels,branch_colors,groups):
                coverage_rows.append(dict(case=case['id'],branch=label,count=len(points),fraction=len(points)/len(rows)))
                if points:
                    handle=axis.scatter([v[0] for v in points],[v[1] for v in points],s=13,alpha=.65,
                                        color=color,label=label)
                    legend_items[label]=handle
            counts='SoD: '+str(len(groups[0]))+'   CoD A: '+str(len(groups[1]))+'   CoD B: '+str(len(groups[2]))
            axis.set(title=names[i]+'\n'+counts,xlabel='供给节点A的SE',ylabel='供给节点B的SE',
                     xlim=(0,100),ylim=(0,100),aspect='equal')
            axis.grid(alpha=.15)
        fig.legend(list(legend_items.values()),list(legend_items),loc='outside lower center',ncol=4,frameon=False,fontsize=9)
        fig.suptitle('训练输入与实际控制输出的分支  按预设参数计算',fontsize=13)
        save(fig,'figure_04_input_branch_coverage')

    summary_rows=[]
    parameter_rows=[]
    for i,c in enumerate(cases):
        fit=c['fit_report']['receivers'][0]
        summary_rows.append(dict(case=c['id'],label=names[i],train_n=fit['metrics']['train']['count'],
            holdout_n=fit['metrics']['holdout']['count'],train_rmse=c['metrics']['train']['rmse'],
            holdout_rmse=c['metrics']['holdout']['rmse'],holdout_mae=c['metrics']['holdout']['mae'],
            holdout_max_absolute_error=c['metrics']['holdout']['max_absolute_error'],
            max_alpha_absolute_error=max(p['absolute_error'] for p in c['parameter_comparison'] if p['parameter']=='alpha'),
            max_beta_absolute_error=max(p['absolute_error'] for p in c['parameter_comparison'] if p['parameter']=='beta'),
            outcome=c['outcome']))
        parameter_rows.extend(dict(case=c['id'],label=names[i],**p) for p in c['parameter_comparison'])
    for name,rows in [('table_01_parameters.csv',parameter_rows),('table_02_metrics.csv',summary_rows),
                      ('table_03_branch_coverage.csv',coverage_rows)]:
        stream=io.StringIO(newline=''); writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
        outputs[name]='\ufeff'+stream.getvalue()
    outputs['报告图表说明.md']=render_notes(report,summary_rows)
    return outputs


def render_notes(report,summary_rows):
    c=report['config_snapshot']
    passed=sum(v['outcome']=='passed' for v in report['cases'])
    conclusion=(f'本次{len(report["cases"])}组实验均在预设容差内恢复参数，独立验证集预测误差接近零；结合有效分支覆盖及局部敏感度检查，支持拟合程序在本次输入覆盖和数值设置下的正确性。'
                if passed==len(report['cases']) else
                f'本次{len(report["cases"])}组实验中有{passed}组通过自检；未通过案例需结合收敛状态、分支活跃及可辨识性诊断判断，不能统一声明参数恢复成功。')
    lines=['# 第一阶段参数恢复实验图表与报告文字','',
           '## 实验设置','',
           f'采用两个供给节点A、B和一个接受节点R。A、B独立从U(0,100)抽样，每组使用{c["random_train"]}条训练记录和{c["random_holdout"]}条独立验证记录。',
           f'实验总种子为{c["seed"]}，采用L-BFGS-B及{c["starts"]}个中性或固定种子随机起点；训练、验证和优化使用独立随机流。',
           '预设α、β用于FDNA正向生成无噪声目标，拟合时边参数设置为未知；不加入δ、不使用表7参数。三组参数共用输入作配对检查，不是三倍独立基础条件。','',
           '## 表1 预设与拟合参数','',
           '| 参数组 | 依赖边 | 参数 | 预设值 | 拟合值 | 绝对误差 |',
           '| --- | --- | --- | ---: | ---: | ---: |']
    for i,case in enumerate(report['cases']):
        for p in case['parameter_comparison']:
            symbol='α' if p['parameter']=='alpha' else 'β'
            lines.append(f'| {i+1} | {p["feeder"]}→R | {symbol} | {p["known"]:.8g} | {p["fitted"]:.12g} | {p["absolute_error"]:.3e} |')
    lines+=['','## 表2 预测误差汇总','',
            '| 参数组 | 训练RMSE | 验证RMSE | 验证MAE | 最大α误差 | 最大β误差 |',
            '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in summary_rows:
        lines.append(f'| {r["label"]} | {r["train_rmse"]:.3e} | {r["holdout_rmse"]:.3e} | {r["holdout_mae"]:.3e} | {r["max_alpha_absolute_error"]:.3e} | {r["max_beta_absolute_error"]:.3e} |')
    lines+=['','## 图注与讲解','',
      '**图1 预设参数与拟合参数对照。** 左图为α，右图为β；横轴区分参数组及依赖边，纵轴为相应参数值。蓝色为预设值，浅橙色为拟合值。两者在图中接近一致，微小差异须结合表1绝对误差判断。',
      '', '![参数对照](figure_01_parameter_recovery.png)','',
      f'**图2 独立验证集预测结果。** 各子图对应不同参数组。横轴为预设FDNA生成的接受节点SE，纵轴为反求参数后预测的SE；虚线表示理想预测y=x。每个子图包含{c["random_holdout"]}条未参与拟合的记录，点接近虚线说明对同分布新输入的预测与生成结果一致。图中目标由人工公式生成，不是真实观测。',
      '', '![验证预测](figure_02_holdout_prediction.png)','',
      '**图3 独立验证集残差。** 横轴为预测SE，纵轴为预测值减生成值，虚线为零残差。三组使用相同纵轴范围，并保留科学计数法刻度。误差接近数值计算精度，不能将放大后的形态解释为现实噪声、显著偏差或异方差证据。',
      '', '![验证残差](figure_03_holdout_residuals.png)','',
      f'**图4 训练输入与有效分支覆盖。** 横纵轴分别为供给节点A、B的SE；颜色区分SoD或两条CoD关系中实际决定最终输出的分支，按预设真值计算。每幅图使用同样{c["random_train"]}对输入，但因参数不同，分支分布不同。该图用于检查本次随机数据是否激活不同分支；分支覆盖本身不保证参数全局唯一。',
      '', '![分支覆盖](figure_04_input_branch_coverage.png)','',
      '## 可直接放入报告的结果描述','',
      f'为初步验证FDNA参数拟合程序，构建双供给节点、单接受节点的无噪声参数恢复实验。分别设定{len(report["cases"])}组α、β，通过基础FDNA公式生成接受节点效能，再以未知参数进行有界拟合。'+conclusion,
      '', '本实验属于同模型参数恢复检查，尚不能证明FDNA适用于真实任务或独立生成机制，也不表示独立求回原论文表7。图中微小误差不等于实际应用中的预测精度。',
      '', '## 使用建议','',
      '正文优先使用图1、图2和表2；图4用于解释输入覆盖与参数恢复条件，图3可作为补充。PNG为300dpi，SVG为矢量图；CSV可在表格软件中打开。表格保留科学计数法，避免把微小非零误差显示成精确的0。',
      '', '## 数据来源','', '全部图表读取同一份已保存实验报告，重新核对配置指纹、参数及逐行残差后绘制，没有重新拟合、调整数据或添加噪声。源报告的SHA256见manifest.json。','']
    return '\n'.join(lines)


def main(argv=None):
    parser=argparse.ArgumentParser(description='从已保存的第一阶段结果生成报告图表')
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        report=read_json(args.report)
        outputs=build_figures(report)
        outputs['manifest.json']=json.dumps(dict(
            source_report=str(args.report.resolve()),
            source_report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(),
            config_sha256=report['input_sha256'],
            source_recovery_status=report['status'],
            refitted=False,data_modified=False,figure_formats=['300dpi PNG','SVG'],
            files=sorted(outputs)),ensure_ascii=False,indent=2)+'\n'
        write_outputs(args.output_dir,outputs,protected=[args.report])
        print(json.dumps(dict(status='complete',files=len(outputs),output_dir=str(args.output_dir.resolve())),ensure_ascii=False))
        return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(json.dumps(dict(status='invalid',error=str(exc)),ensure_ascii=False))
        return 2


if __name__=='__main__':
    sys.exit(main())
