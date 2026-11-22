"""Inspect SE input readiness and create blank templates without optimizing."""
import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path

from .model import ModelError
from .network_workflow import META,read_samples,validate_samples,predictors
from .workflow_common import make_model,settings,csv_text,write_bundle,dumps


def inspect_data(network,rows,config):
    """Reuse fitting data checks, then report feasibility, not identifiability."""
    groups=validate_samples(network,rows,config)
    receivers=sorted({e['receiver'] for e in network['edges']})
    roots=sorted(set(network['nodes'])-set(receivers));results=[];omitted=[];coverage=[]
    for group in groups:
        selected=[r for r in rows if r['fit_group']==group]
        for mode,setting in config['models'].items():
            for node in receivers:
                parents=sorted(e['feeder'] for e in network['edges'] if e['receiver']==node)
                valid=defaultdict(list)
                for row in selected:
                    values=predictors(row,config)
                    missing=[p for p in parents if values.get(p) is None]
                    if row['node_values'].get(node) is None: missing.append(node)
                    if missing:
                        omitted.append(dict(fit_group=group,model=mode,node=node,sample_id=row['sample_id'],split=row['split'],missing_nodes=missing))
                    else: valid[row['split']].append(row)
                parameter_count=2*len(parents)+int(setting['delta_policy']=='per_receiver_bounded')
                train=valid['train'];holdout=valid['holdout'];warnings=[]
                unique_inputs={tuple(predictors(r,config)[p] for p in parents) for r in train}
                if len(train)<parameter_count: warnings.append('INSUFFICIENT_TRAIN_ROWS')
                if len(unique_inputs)<parameter_count: warnings.append('INSUFFICIENT_DISTINCT_INPUTS')
                if not holdout: warnings.append('NO_COMPLETE_HOLDOUT')
                if any(any(predictors(r,config).get(p) is None for p in roots) for r in selected):
                    warnings.append('MISSING_ROOTS_FOR_RECURSION')
                for parent in parents:
                    values=[predictors(r,config)[parent] for r in train]
                    lo,hi=(min(values),max(values)) if values else (None,None)
                    if values and hi-lo<1e-10: warnings.append('CONSTANT_PREDICTOR:'+parent)
                    coverage.append(dict(fit_group=group,model=mode,node=node,feeder=parent,train_min=lo,train_max=hi,
                        holdout_outside_train=sum(lo is not None and not lo<=predictors(r,config)[parent]<=hi for r in holdout)))
                complete_count=len(train)+len(holdout)
                if complete_count<len(selected): warnings.append('INCOMPLETE_LOCAL_RECORDS')
                fit_ready=len(train)>=parameter_count
                results.append(dict(fit_group=group,model=mode,node=node,feeders=parents,train_count=len(train),holdout_count=len(holdout),
                    independent_train_base_cases=len({r['base_case_id'] for r in train}),distinct_train_inputs=len(unique_inputs),
                    parameter_count=parameter_count,fit_ready=fit_ready,holdout_ready=bool(holdout),warnings=warnings))
    ready=all(r['fit_ready'] for r in results)
    return dict(stage='given_network_input_preflight',status='ready' if ready and not any(r['warnings'] for r in results) else 'ready_with_warnings' if ready else 'needs_data',
        local_readiness=results,omitted_local_records=omitted,predictor_coverage=coverage,network_snapshot=deepcopy(network),config_snapshot=deepcopy(config),
        counts=dict(samples=len(rows),base_cases=len({r['base_case_id'] for r in rows}),fit_groups=len(groups),local_checks=len(results)),
        policies=dict(optimizer_called=False,missing_values_imputed=False,normalization_inferred=False,identifiability_established=False),
        limits=['样本数和输入覆盖是拟合前检查，不证明参数可辨识。','重复记录不等于独立基础实验。','实测SE须由明确指标与归一化产生，本入口不从原始指标猜测SE。'])


def template_outputs(network):
    make_model(network)
    nodes=sorted(network['nodes']);receivers=sorted({e['receiver'] for e in network['edges']})
    rows=[dict(sample_id=f'sample_{i:03d}',base_case_id=f'base_{i:03d}',scenario_id='scenario_1',fit_group='group_1',split=split)
          for i,split in [(1,'train'),(2,'holdout')]]
    long=[dict(**r,node_id=n,se='') for r in rows for n in nodes]
    canonical=[dict(**r,node_values={n:None for n in nodes}) for r in rows]
    config=dict(input_role='observed',pool_scenarios=False,data_kind='user_supplied',models={'base':settings('base',receivers)},
        analysis=dict(retained_fractions=[0.,.5],max_paths_per_node=1000))
    outputs={'网络拓扑.json':dumps(network),'SE填写模板_长表.csv':csv_text(long),'SE填写模板.json':dumps(canonical),'基础式拟合配置.json':dumps(config)}
    wide_safe=not set(nodes)&set(META) and not any(n.startswith('predictor:') for n in nodes)
    if wide_safe: outputs['SE填写模板_宽表.csv']=csv_text([{**r,**{n:'' for n in nodes}} for r in rows])
    optional=deepcopy(config);optional['models']={'free_delta':settings('free_delta',receivers,delta_bounds=(-10,10))}
    outputs['自由δ可选配置.json']=dumps(optional)
    outputs['填写说明.md']='''# 节点SE数据填写说明

模板按给定网络生成，所有SE单元格为空；两行样本只展示训练和留出格式，不是足够拟合的数据，也不是真实数据示例。

1. 每次实验使用唯一sample_id；同一基础实验的衍生记录共用base_case_id，并整体进入train或holdout。
2. 每条记录填写同次实验中各节点SE，范围0到100。缺失值保留空白或null，不补0。长表同一样本的元数据保持一致，node_id不可重复。
3. scenario_id标记场景，fit_group决定哪些记录共用参数。同组跨场景时，只有明确同意共享机制才设置pool_scenarios=true。
4. 默认配置使用实测输入和基础式；自由δ配置单独提供，边界[-10,10]是待明确的模型设置，不能当作真实数据给出的参数范围。
5. 先运行network_preflight检查数据，再运行network_workflow拟合。样本行数超过参数个数不保证可辨识；需要供给输入有变化并在拟合后检查分支和参数约束。
6. 原始指标的含义、单位、效益/成本方向及归一化范围须另行记录。这个模板只接收已经计算的SE，不自动推定指标或归一化。

命令从仓库根目录运行，路径替换为实际文件：

```powershell
python -B -m fdna.network_preflight --network 网络拓扑.json --samples SE填写模板_宽表.csv --config 基础式拟合配置.json --output-dir 输入检查_新运行
python -B -m fdna.network_workflow --network 网络拓扑.json --samples 已填写SE.csv --config 基础式拟合配置.json --output-dir 拟合结果_新运行
```

使用长表时附加--layout long；JSON记录不需要layout选项。输出目录须不存在或为空。模板未填写时预检查应报告needs_data，这不是程序失败。
'''
    return outputs


def preflight_outputs(report):
    lines=['# 网络SE输入预检查','',f"状态：{report['status']}。未调用拟合器。",'',
        '| 分组 | 模型 | 节点 | 有效训练 | 有效留出 | 参数个数 | 输入警告 |',
        '| --- | --- | --- | ---: | ---: | ---: | --- |']
    for r in report['local_readiness']:
        lines.append(f"| {r['fit_group']} | {r['model']} | {r['node']} | {r['train_count']} | {r['holdout_count']} | {r['parameter_count']} | {', '.join(r['warnings']) or '无'} |")
    lines+=['','ready仅表示达到基本样本条件；不证明参数可辨识或预测可靠。ready_with_warnings保留有效样本不足以约束全部方向、缺失、覆盖等提示。needs_data表示至少一个节点达不到当前模型的训练行数要求。']
    return {'输入预检查.json':dumps(report),'节点输入状态.csv':csv_text(report['local_readiness']),
        '缺失记录.csv':csv_text(report['omitted_local_records'],['fit_group','model','node','sample_id','split','missing_nodes']),
        '供给覆盖.csv':csv_text(report['predictor_coverage']),'检查说明.md':'\n'.join(lines)+'\n'}


def main(argv=None):
    p=argparse.ArgumentParser(description='给定网络的空白SE模板与无拟合输入检查')
    p.add_argument('--network',type=Path,required=True);p.add_argument('--samples',type=Path);p.add_argument('--config',type=Path)
    p.add_argument('--layout',choices=['wide','long'],default='wide');p.add_argument('--templates',action='store_true');p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args(argv)
    try:
        if a.output_dir.exists() and any(a.output_dir.iterdir()): raise ModelError('输出目录非空')
        network=json.loads(a.network.read_text(encoding='utf-8'))
        if a.templates:
            if a.samples or a.config: raise ModelError('模板生成不接收已有SE或拟合配置')
            write_bundle(a.output_dir,template_outputs(network));print(dumps(dict(status='templates_created',optimizer_called=False)));return 0
        if not a.samples or not a.config: raise ModelError('预检查须同时提供samples与config')
        config=json.loads(a.config.read_text(encoding='utf-8'));rows=read_samples(a.samples,network,a.layout)
        report=inspect_data(network,rows,config);write_bundle(a.output_dir,preflight_outputs(report))
        print(dumps(dict(status=report['status'],optimizer_called=False)))
        return 1 if report['status']=='needs_data' else 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(dumps(dict(status='invalid',error=str(exc))));return 2

if __name__=='__main__': raise SystemExit(main())
