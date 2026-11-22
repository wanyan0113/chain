"""Reviewed upstream mapping + supplied observed node SE; never generate inputs."""
import argparse
import hashlib
import json
from pathlib import Path
from .bridge_intake import inspect_mapping
from .equipment_bridge import bridge
from .bridge_demo import figures
from .model import ModelError
from .network_workflow import read_samples, validate_samples, fit_network, analyze_dependencies, export_outputs
from .workflow_common import write_bundle, dumps, csv_text


def run(report_path, mapping_path, samples_path, config_path, output_dir, layout='wide'):
    out=Path(output_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):raise ModelError('输出目录须不存在或为空')
    paths={k:Path(v) for k,v in dict(report=report_path,mapping=mapping_path,samples=samples_path,config=config_path).items()}
    raw={k:p.read_bytes() for k,p in paths.items()}
    source=json.loads(raw['report'].decode('utf-8-sig'));mapping=json.loads(raw['mapping'].decode('utf-8-sig'))
    config=json.loads(raw['config'].decode('utf-8-sig'))
    if not isinstance(config,dict):raise ModelError('拟合配置须为JSON对象')
    check=inspect_mapping(source,mapping)
    if check['status']!='ready':raise ModelError('映射不可用于观测SE拟合：'+'；'.join(check['issues']))
    network,side=bridge(source,mapping)
    if config.get('input_role')!='observed':raise ModelError('此入口仅接受input_role=observed，不接收干净供给')
    if config.get('data_kind') not in ('simulated','user_supplied_se'):raise ModelError('须声明simulated或user_supplied_se；声明不等于真实来源已核实')
    if 'analysis' not in config:raise ModelError('须提供依赖分析配置')
    rows=read_samples(paths['samples'],network,layout)
    if paths['samples'].read_bytes()!=raw['samples']:raise ModelError('SE文件在读取过程中发生变化，请固定输入后重试')
    if any('predictor_values' in r for r in rows):raise ModelError('观测SE入口不接受predictor_values或predictor列；请显式移除干净状态')
    validate_samples(network,rows,config)
    rows=[dict(r,node_values={n:r['node_values'][n] for n in sorted(r['node_values'])}) for r in rows]
    report=fit_network(network,rows,config,lambda p:print(dumps(p),flush=True))
    analysis=analyze_dependencies(report,rows,config['analysis'])
    counts=dict(status=report['status'],input_role='observed',data_kind=config['data_kind'],rows=len(rows),
        base_cases=len({r['base_case_id'] for r in rows}),nodes=len(network['nodes']),edges=len(network['edges']),
        missing_se_cells=sum(r['node_values'].get(n) is None for r in rows for n in network['nodes']),
        local_fits=sum(len(j['local_fits']) for j in report['jobs']),
        fitted=sum(l['status']=='fitted' for j in report['jobs'] for l in j['local_fits']),
        flagged_local_fits=sum(bool(l['flags']) for j in report['jobs'] for l in j['local_fits']),
        effects=len(analysis['effects']),generated_samples=False,clean_predictors_used=False,
        input_measurement_error_modeled=False,common_source_reconstructed=False)
    node_table=[dict(node=n,label=a['label'],subject=a['subject'],chain=a['chain_id'],role=a['role'],
        metric=a['metric']['definition'],context=a['context']) for n,a in side['capability_instances'].items()]
    outputs={**export_outputs(report,analysis),'输入_拓扑.json':dumps(network),'输入_SE记录.json':dumps(rows),
        '衔接依据与审查.json':dumps(side),'能力变量对照.csv':csv_text(node_table),'接入校验.json':dumps(check),
        '运行计数.json':dumps(counts),'source_report.json':raw['report'],'mapping.json':raw['mapping'],
        'fit.json':raw['config'],'输入_SE原文件'+paths['samples'].suffix.lower():raw['samples'],
        '输入文件校验.json':dumps({k:hashlib.sha256(v).hexdigest() for k,v in raw.items()}),
        '结果说明.md':'# 观测SE接入\n\n本次直接读取已有节点SE，不调用模拟生成器、不读取干净供给或共同状态真值。data_kind是用户声明，模拟数据不得据此写成真实数据验证。\n\n缺失输入不补零，失败节点、局部排除、参数标记和预测越界分别保留。模型预测不裁剪；供给SE的测量误差未被统计校正，不能据低RMSE断言参数唯一或依赖已被实证验证。\n\n节点退化是拟合模型内的情景推演。没有共同来源观测模型时，不从共享装备身份反推共同状态或自动生成共同来源情景。\n'}
    write_bundle(out,outputs);figures(out,network,side,report,analysis)
    inventory={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}
    (out/'全部产物校验.json').write_text(dumps(inventory),encoding='utf-8')
    return report,analysis,counts


def main(argv=None):
    parser=argparse.ArgumentParser(description='已审查装备映射与外部观测SE的拟合和依赖分析')
    for key in ('report','mapping','samples','config','output-dir'):parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--layout',choices=['wide','long'],default='wide');args=parser.parse_args(argv)
    try:
        _,_,counts=run(args.report,args.mapping,args.samples,args.config,args.output_dir,args.layout)
        print(dumps(counts));return 0 if counts['status']=='complete' else 1
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(dumps(dict(status='invalid',error=str(exc))));return 2


if __name__=='__main__':raise SystemExit(main())
