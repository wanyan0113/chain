"""Stage two, step one: independent 2-to-1 unit generation, without fitting."""
import argparse
from copy import deepcopy
import csv
import io
import json
import math
from pathlib import Path
import sys

from source_data.common import read_json, write_outputs
from .generation import NodeGenerator, validate_config
from .model import ModelError
from .structure import digest


def _closed_form(config,a,b):
    """Two-input direct power/product evaluation, separate from generic log form."""
    x,y=a/100,b/100
    if config['mechanism_type']=='complementary':
        support=1-(1-config['support_coefficients']['A']*x)*(1-config['support_coefficients']['B']*y)
    elif config['mechanism_type']=='weighted':
        support=config['weights']['A']*x+config['weights']['B']*y
    else:
        support=x**config['weights']['A']*y**config['weights']['B']
    f=config['condition_function']; v=f['values']
    return 100*support*(f['capability_base']+f['capability_gain']*v['C'])*(f['task_base']+f['task_gain']*v['M'])*(1-f['environment_penalty']*v['E'])


def run_unit_check(config):
    validate_config('experiment',config)
    if not 0<config['train_count']<config['base_cases']:
        raise ModelError('训练数量须小于基础样本总数并大于0')
    if 0 not in config['noise_std']:
        raise ModelError('单元检查须保留σ=0的干净观测对照')
    for preset in config['presets'].values():
        NodeGenerator(preset)
        if set(preset['parent_nodes'])!={'A','B'}:
            raise ModelError('本单元入口须使用A、B两个输入；整网接口另行连接')
    import numpy as np
    streams=np.random.SeedSequence(config['seed']).spawn(5)
    root_rng=np.random.default_rng(streams[0]); process_rng=np.random.default_rng(streams[2])
    obs_rng=np.random.default_rng(streams[3]); split_rng=np.random.default_rng(streams[4])
    n=config['base_cases']; inputs=[]; categories=[]
    for label,ratio,low_a,low_b in [('normal',.5,False,False),('A_degraded',.2,True,False),
                                  ('B_degraded',.2,False,True),('both_degraded',.1,True,True)]:
        count=round(n*ratio)
        u=np.column_stack([root_rng.uniform(*((.2,.6) if low_a else (.65,.98)),count),
                           root_rng.uniform(*((.2,.6) if low_b else (.65,.98)),count)])
        inputs.extend((100*(.85*u+.15*root_rng.uniform(0,1,(count,1)))).tolist())
        categories.extend([label]*count)
    train_ids=set(split_rng.permutation(n)[:config['train_count']].tolist())
    rows=[]; checks=[]; summaries=[]; worked_examples=[]
    for name,preset in config['presets'].items():
        process_z=process_rng.standard_normal(n)
        observation_z=obs_rng.standard_normal(n)
        for group,values in config['groups'].items():
            node=deepcopy(preset); node['condition_function']['values']=deepcopy(values)
            generator=NodeGenerator(node)
            reverse=deepcopy(node); reverse['parent_nodes']=list(reversed(reverse['parent_nodes']))
            reverse_generator=NodeGenerator(reverse)
            max_formula_error=0.0; max_order_error=0.0; monotone=True
            grid=[0,25,50,75,100]
            for a in grid:
                for b in grid:
                    result=generator.generate(dict(A=a,B=b))
                    max_formula_error=max(max_formula_error,abs(result['receiver_se_clean']-_closed_form(node,a,b)))
                    max_order_error=max(max_order_error,abs(result['receiver_se_clean']-
                        reverse_generator.generate(dict(B=b,A=a))['receiver_se_clean']))
            for fixed in grid:
                for varied in ('A','B'):
                    sequence=[generator.generate({'A':v if varied=='A' else fixed,
                                                   'B':v if varied=='B' else fixed})['receiver_se_clean'] for v in grid]
                    monotone &= all(a<=b+1e-10 for a,b in zip(sequence,sequence[1:]))
            checks.append(dict(preset=name,group=group,formula_grid_points=25,
                max_formula_absolute_error=max_formula_error,max_parent_order_error=max_order_error,
                input_monotone=bool(monotone),passed=max_formula_error<=1e-10 and max_order_error<=1e-10 and bool(monotone)))
            worked_examples.append(dict(preset=name,group=group,**generator.generate(dict(A=80,B=80))))
            local=[]
            for i,(a,b) in enumerate(inputs):
                clean_versions=[]
                for sigma in config['noise_std']:
                    generated=generator.generate(dict(A=a,B=b),
                        process_noise=float(config['process_std']*process_z[i]),
                        observation_noise=float(sigma*observation_z[i]),
                        interaction_gamma=config['interaction_gamma'])
                    clean_versions.append(generated['receiver_se_clean'])
                    row=dict(base_case_id=f'unit:{i}',repetition=1,seed=config['seed'],
                        split='train' if i in train_ids else 'holdout',category=categories[i],
                        preset=name,group=group,noise_sigma=sigma,A=a,B=b,C=values['C'],M=values['M'],E=values['E'],**generated)
                    rows.append(row); local.append(row)
                if len(set(clean_versions))!=1:
                    raise ModelError('观测噪声改变了干净效能，拒绝输出')
            clean_rows=[r for r in local if r['noise_sigma']==0]
            deficits=[max(0,min(r['A'],r['B'])-r['receiver_se_clean']) for r in clean_rows]
            summaries.append(dict(preset=name,group=group,base_cases=n,
                train_base_cases=config['train_count'],holdout_base_cases=n-config['train_count'],
                clean_min=min(r['receiver_se_clean'] for r in clean_rows),
                clean_max=max(r['receiver_se_clean'] for r in clean_rows),
                clean_mean=math.fsum(r['receiver_se_clean'] for r in clean_rows)/n,
                below_min_parent_fraction=sum(d>1e-10 for d in deficits)/n,
                base_fdna_rmse_lower_bound=math.sqrt(math.fsum(d*d for d in deficits)/n),
                observed_boundary_count=sum(r['receiver_se_observed'] in (0,100) for r in local)))
    return dict(format_version='1.0',status='complete' if all(c['passed'] for c in checks) else 'blocked',
        stage='second_stage_step_1_unit_generation',data_kind='independent_synthetic_method_check',
        config_snapshot=deepcopy(config),input_sha256=digest(config),checks=checks,summaries=summaries,
        worked_examples=worked_examples,rows=rows,
        counts=dict(paired_base_inputs=n,condition_groups=len(config['groups']),mechanisms=len(config['presets']),
                    repetitions=1,noise_versions=len(config['noise_std']),observation_rows=len(rows)),
        policies=dict(fdna_forward_used=False,fdna_fitted=False,network_connected=False,
            observation_noise_propagated=False,configuration_source='manual',
            noise_pairing='same_standard_normal_draw_scaled_by_sigma',
            random_streams=['root','reserved_conditions','process','observation','split'],
            input_pairing='same_input_and_split_across_mechanisms_and_conditions'),
        limits=['本轮是三个独立单元的公式和数据流程检查，不是最终综合场景实验',
                '尚未开展FDNA局部拟合、七节点整网预测、10次重复或实际任务验证',
                '理论误差下界只计算当前干净输入与目标，不是已取得的拟合误差'])


def samples_csv(report):
    rows=[]
    for source in report['rows']:
        row={k:v for k,v in source.items() if k!='parent_clean'}
        rows.append(row)
    stream=io.StringIO(newline=''); writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    return '\ufeff'+stream.getvalue()


def render_summary(report):
    config=report['config_snapshot']
    lines=['# 第二阶段第一步 独立生成机制检查','',
        '已实现互补支持、几何组合、加权组合及节点条件函数；不使用FDNA公式生成接受节点目标。',
        '本轮只做单元检查，后续仍以七节点综合场景为主要实验。','',
        '## 本轮规模','',
        f"{report['counts']['paired_base_inputs']}组配对基础输入×{report['counts']['mechanisms']}种机制×{report['counts']['condition_groups']}组条件×{report['counts']['noise_versions']}档观测噪声={report['counts']['observation_rows']}条观测记录。",
        '只运行一次重复；不是同等数量的独立实验。输入和训练划分跨机制、条件、噪声版本配对。',
        '节点条件组：'+json.dumps(config['groups'],ensure_ascii=False),
        '观测噪声σ='+str(config['noise_std'])+'，同一标准正态抽样按σ缩放。',
        f"过程波动标准差={config['process_std']}，交互强度γ={config['interaction_gamma']}。",'',
        '## 公式复算与边界检查','',
        '| 机制 | 条件组 | 最大公式差值 | 上游顺序变化差值 | 输入单调性 |',
        '| --- | --- | ---: | ---: | --- |']
    for c in report['checks']:
        lines.append(f"| {c['preset']} | {c['group']} | {c['max_formula_absolute_error']:.3e} | {c['max_parent_order_error']:.3e} | {c['input_monotone']} |")
    lines+=['','## 两个输入均为80时的示例','',
        '本表关闭过程波动、观测噪声和交互项，仅用于复算各机制及条件函数。','',
        '| 机制 | 条件组 | 支持项 | 条件系数 | 接受节点干净SE |','| --- | --- | ---: | ---: | ---: |']
    for r in report['worked_examples']:
        lines.append(f"| {r['preset']} | {r['group']} | {r['support']:.6f} | {r['condition_factor']:.6f} | {r['receiver_se_clean']:.6f} |")
    lines+=['','## 本轮输出的含义','',
        '输入与权重、支持系数及条件函数系数均为预先声明的模拟假设；不能将权重或支持系数解释为FDNA的α、β。',
        'CSV保存原始输入、条件、训练划分、过程波动、观测噪声、干净值、观测值和截断标记；JSON保存完整配置及检查结果。',
        '独立单元会产生各自的数据；正式综合实验将重新生成联合网络样本，并用其实际上游输入拟合局部FDNA，不能直接把单元拟合参数当作整网参数。','',
        '## 曲线图说明','',
        'mechanism_response.png中，tracking、fusion、execution依次对应互补支持、几何组合和加权组合。横轴为A的SE，B固定为75；纵轴为独立机制生成的干净SE。蓝线unit_conditions不额外衰减，橙线fixed_conditions加入固定节点条件。曲线关闭过程波动、观测噪声及交互项，展示公式响应，不是FDNA拟合结果。','',
        '## 下一步','',
        '连接A、B、C_aux、D_aux、T、Q、R七节点网络，按拓扑顺序传递干净输出；随后开展综合训练样本上的FDNA局部拟合及根节点驱动的整网验证。','',
        '## 重跑命令','', '输出目录须尚未使用。在工程目录执行：','', '```powershell',
        "Set-Location '"+str(Path(__file__).resolve().parents[1])+"'",
        "& '"+sys.executable+"' -B -m fdna.generation_check --config data/fdna/generation_unit_check.json --output-dir generated/stage2-unit-new --plot",'```','']
    return '\n'.join(lines)


def plot_response(report):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    config=report['config_snapshot']; names=list(config['presets'])
    fig=Figure(figsize=(4*len(names),3.8),layout='constrained'); FigureCanvasAgg(fig)
    axes=fig.subplots(1,len(names),squeeze=False)[0]
    for axis,name in zip(axes,names):
        for group,values in config['groups'].items():
            node=deepcopy(config['presets'][name]); node['condition_function']['values']=values
            generator=NodeGenerator(node)
            grid=list(range(101))
            outputs=[generator.generate(dict(A=x,B=75))['receiver_se_clean'] for x in grid]
            axis.plot(grid,outputs,label=group,linewidth=1.7)
        axis.set(title=name,xlabel='Input A SE (B fixed at 75)',ylabel='Generated clean SE',
                 xlim=(0,100),ylim=(0,100))
        axis.grid(alpha=.2); axis.legend(fontsize=8)
    fig.suptitle('Independent generation mechanisms | formula scan, not FDNA fitting')
    stream=io.BytesIO(); fig.savefig(stream,format='png',dpi=250)
    return stream.getvalue()


def main(argv=None):
    parser=argparse.ArgumentParser(description='第二阶段独立生成机制单元检查')
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--plot',action='store_true')
    args=parser.parse_args(argv)
    try:
        report=run_unit_check(read_json(args.config))
        outputs={'generator_report.json':json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',
                 'unit_samples.csv':samples_csv(report),'第二阶段第一步_生成机制检查.md':render_summary(report)}
        if args.plot: outputs['mechanism_response.png']=plot_response(report)
        write_outputs(args.output_dir,outputs,protected=[args.config])
        print(json.dumps(dict(status=report['status'],counts=report['counts']),ensure_ascii=False,indent=2))
        return 0 if report['status']=='complete' else 1
    except (ValueError,OSError,ImportError) as exc:
        print(json.dumps(dict(status='invalid',error=str(exc)),ensure_ascii=False))
        return 2


if __name__=='__main__':
    sys.exit(main())
