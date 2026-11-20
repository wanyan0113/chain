"""python -m fdna inspect CASE; python -m fdna evaluate CASE --scenario SCENARIO ..."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from source_data.common import read_json, write_outputs
from .engine import MODEL, evaluate
from .model import inspect_model, load_model, verify_sources
from .sweep import csv_report, plot_sweep, render_sweep_summary, sweep
from .figure_compare import compare_readings, plot_comparison, render_comparison
from .self_effectiveness import normalize_measurements, render_se_summary, se_csv
from .structure import inspect_structure, render_structure, structure_csv
from .identification import identify_dependencies
from .experiments import inspect_experiments
from .extraction import extract_measurements
from .fitting import fit_check, fit_parameters
from .additive import ADDITIVE_MODEL, evaluate_additive
from .robustness import robustness_check
from .application import analyze_application, application_csv
from .preparation import prepare_fit_samples
from .residuals import diagnose_residuals, residual_csv, plot_residuals
from .precision import check_precision
from .protocol_audit import audit_protocols
from .pca import analyze_pca
from .fitted_application import analyze_fitted_application, response_difference_csv
from .recovery import run_recovery, recovery_csvs, render_recovery


def render_summary(report):
    lines = ['# 奥术网能力依赖核验', '', report['scope'], '',
             f"求法：{report['model']}；状态：{report['status']}。", '',
             f"取值依据：{report['scenario']['basis']}", '',
             '每条供给关系先求完整SoD项，再取均值；CoD取最小值。α不另行归一化。',
             '未添加δ或效能裁剪；表中数值是基础式的重算结果。', '',
             '| 能力 | 所属主体 | 效能 | SoD均值 | CoD最小值 | 状态 |',
             '| --- | --- | ---: | ---: | ---: | --- |']
    def display(value):
        return '未知' if value is None else f'{value:.10g}'
    for node in report['nodes']:
        status = {'computed': '已求得', 'supplied': '已给定', 'unknown': '未知'}[node['status']]
        lines.append(f"| {node['id']} | {node['subject']} | {display(node['value'])} | "
                     f"{display(node['sod_mean'])} | {display(node['cod_min'])} | {status} |")
    lines += ['', '## 仍待核实', '']
    lines.extend(f"- {gap['label']}" for gap in report['pending'])
    lines += ['', '表7的伪R²为原文报告值，本次没有另行拟合或重新求取。', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='奥术网能力依赖核验')
    commands = parser.add_subparsers(dest='command', required=True)
    recovery = commands.add_parser('recovery', help='双供给节点的已知参数恢复与独立验证')
    recovery.add_argument('--config', type=Path, required=True)
    recovery.add_argument('--output-dir', type=Path, required=True)
    check = commands.add_parser('inspect', help='核对案例结构与来源')
    check.add_argument('case', type=Path)
    run = commands.add_parser('evaluate', help='依基础式求取效能')
    run.add_argument('case', type=Path)
    run.add_argument('--scenario', type=Path, required=True)
    run.add_argument('--model', choices=[MODEL, ADDITIVE_MODEL], required=True)
    run.add_argument('--extension', type=Path, help='δ扩展必须显式提供逐接受节点δ与输出约定')
    run.add_argument('--output-dir', type=Path, required=True)
    scan = commands.add_parser('sweep', help='按明确约定逐项改变节点效能；中间节点须声明干预策略')
    scan.add_argument('case', type=Path)
    scan.add_argument('--protocol', type=Path, required=True)
    scan.add_argument('--output-dir', type=Path, required=True)
    scan.add_argument('--plot', action='store_true', help='另存基础式曲线图，需要matplotlib')
    compare = commands.add_parser('compare', help='带误差读图值与基础式作条件对照')
    compare.add_argument('case', type=Path)
    compare.add_argument('--protocol', type=Path, required=True)
    compare.add_argument('--readings', type=Path, required=True)
    compare.add_argument('--output-dir', type=Path, required=True)
    compare.add_argument('--plot', action='store_true')
    normalizer = commands.add_parser('normalize', help='按明确约定将原始效能指标转为SE')
    normalizer.add_argument('case', type=Path)
    normalizer.add_argument('--calibration', type=Path, required=True)
    normalizer.add_argument('--measurements', type=Path, required=True)
    normalizer.add_argument('--output-dir', type=Path, required=True)
    structural = commands.add_parser('structure', help='核对第三节六层实体与规则映射')
    structural.add_argument('case', type=Path)
    structural.add_argument('--structure', type=Path, required=True)
    structural.add_argument('--output-dir', type=Path, required=True)
    identify = commands.add_parser('identify', help='依据活动交互与效能证据审查候选依赖')
    identify.add_argument('case', type=Path)
    identify.add_argument('--structure', type=Path, required=True)
    identify.add_argument('--records', type=Path, required=True)
    identify.add_argument('--output-dir', type=Path, required=True)
    experiments = commands.add_parser('experiments', help='核查实验变量与法术链闭合记录')
    experiments.add_argument('case', type=Path)
    experiments.add_argument('--structure', type=Path, required=True)
    experiments.add_argument('--plan', type=Path, required=True)
    experiments.add_argument('--batch', type=Path, required=True)
    experiments.add_argument('--output-dir', type=Path, required=True)
    extract = commands.add_parser('extract', help='从闭合记录提取指标，可按显式配置转换SE')
    extract.add_argument('case', type=Path)
    extract.add_argument('--structure', type=Path, required=True)
    extract.add_argument('--plan', type=Path, required=True)
    extract.add_argument('--batch', type=Path, required=True)
    extract.add_argument('--extraction', type=Path, required=True)
    extract.add_argument('--calibration', type=Path)
    extract.add_argument('--output-dir', type=Path, required=True)
    fit = commands.add_parser('fit', help='用明确分组的SE样本拟合基础式α、β')
    fit.add_argument('case', type=Path)
    fit.add_argument('--samples', type=Path, required=True)
    fit.add_argument('--settings', type=Path, required=True)
    fit.add_argument('--output-dir', type=Path, required=True)
    artificial = commands.add_parser('fit-check', help='生成明确标记的人工SE样本并验证有界拟合')
    artificial.add_argument('case', type=Path)
    artificial.add_argument('--generation', type=Path, required=True)
    artificial.add_argument('--settings', type=Path, required=True)
    artificial.add_argument('--output-dir', type=Path, required=True)
    stability = commands.add_parser('fit-stability', help='补充人工扰动与多种子的稳定性诊断')
    stability.add_argument('case', type=Path)
    stability.add_argument('--generation', type=Path, required=True)
    stability.add_argument('--settings', type=Path, required=True)
    stability.add_argument('--protocol', type=Path, required=True)
    stability.add_argument('--output-dir', type=Path, required=True)
    application = commands.add_parser('application', help='比较指定扫描条件下的节点效能下降、约束及依赖路径')
    application.add_argument('case', type=Path)
    application.add_argument('--protocol', type=Path, required=True)
    application.add_argument('--output-dir', type=Path, required=True)
    prepare = commands.add_parser('prepare-fit', help='从闭合事实提取实测SE，按整次推演划分并导出拟合样本')
    prepare.add_argument('case', type=Path)
    for flag in ('structure', 'plan', 'batch', 'extraction', 'calibration', 'partition', 'output-dir'):
        prepare.add_argument('--'+flag, type=Path, required=True)
    residuals = commands.add_parser('residuals', help='核对已有拟合报告，分别导出训练与留出残差诊断')
    residuals.add_argument('case', type=Path)
    residuals.add_argument('--fit-report', type=Path, required=True)
    residuals.add_argument('--output-dir', type=Path, required=True)
    residuals.add_argument('--plot', action='store_true')
    precision = commands.add_parser('precision', help='检查表7舍入精度下的输出范围与条件排序')
    precision.add_argument('case', type=Path)
    precision.add_argument('--protocol', type=Path, required=True)
    precision.add_argument('--output-dir', type=Path, required=True)
    protocol_audit = commands.add_parser('protocol-audit', help='对照四种显式扫描假设及报告精度，保留读图分离点')
    protocol_audit.add_argument('case', type=Path)
    protocol_audit.add_argument('--protocol', type=Path, required=True)
    protocol_audit.add_argument('--readings', type=Path, required=True)
    protocol_audit.add_argument('--output-dir', type=Path, required=True)
    pca = commands.add_parser('pca', help='仅用训练样本分析供给变量的主成分，不自动换算参数起点')
    pca.add_argument('case', type=Path)
    pca.add_argument('--samples', type=Path, required=True)
    pca.add_argument('--config', type=Path, required=True)
    pca.add_argument('--output-dir', type=Path, required=True)
    fitted_application = commands.add_parser('fitted-application', help='核验拟合报告并以拟合参数重算传播，与来源参数同条件对照')
    fitted_application.add_argument('case', type=Path)
    fitted_application.add_argument('--fit-report', type=Path, required=True)
    fitted_application.add_argument('--protocol', type=Path, required=True)
    fitted_application.add_argument('--reference-extension', type=Path,
                                    help='可选来源δ配置；未提供时不假设来源δ为0')
    fitted_application.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'recovery':
            result = run_recovery(read_json(args.config))
            write_outputs(args.output_dir, {
                'fdna_report.json': json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',
                'recovery_summary.md': render_recovery(result),
                **recovery_csvs(result),
            }, protected=[args.config])
            print(json.dumps(dict(status=result['status'],summary=result['summary'],
                                  input_sha256=result['input_sha256']),ensure_ascii=False,indent=2))
            return 0 if result['status']=='complete' else 1
        model = load_model(args.case)
        provenance = verify_sources(model, args.case)
        if any(source['status'] in ('changed', 'unavailable') for source in provenance):
            raise ValueError('已登记的本地来源发生变化或不可读取，请重新核对来源')
        if args.command == 'inspect':
            result = inspect_model(model)
            result['source_verification'] = provenance
        else:
            if args.command == 'fitted-application':
                input_path = args.fit_report
                result = analyze_fitted_application(model, read_json(args.fit_report), read_json(args.protocol),
                    reference_extension=read_json(args.reference_extension) if args.reference_extension else None)
                outputs = {'response_differences.csv': response_difference_csv(result),
                           'residuals.csv': residual_csv(result['residual_audit']),
                           'fitted_responses.csv': csv_report(result['fitted_application']['sweep_snapshot'])}
                if result['reference_application'] is not None:
                    outputs['reference_responses.csv'] = csv_report(result['reference_application']['sweep_snapshot'])
            elif args.command == 'pca':
                input_path = args.samples
                result = analyze_pca(model, read_json(args.samples), read_json(args.config))
                outputs = {}
            elif args.command == 'protocol-audit':
                input_path = args.protocol
                result = audit_protocols(model, read_json(input_path), read_json(args.readings))
                outputs = {}
            elif args.command == 'precision':
                input_path = args.protocol
                result = check_precision(model, read_json(input_path))
                outputs = {}
            elif args.command == 'residuals':
                input_path = args.fit_report
                result = diagnose_residuals(model, read_json(input_path))
                outputs = {'residuals.csv': residual_csv(result)}
                if args.plot:
                    outputs['residuals.png'] = plot_residuals(result)
            elif args.command == 'prepare-fit':
                input_path = args.batch
                result = prepare_fit_samples(model, read_json(args.structure), read_json(args.plan),
                                             read_json(args.batch), read_json(args.extraction),
                                             read_json(args.calibration), read_json(args.partition))
                outputs = {'fit_samples.json': json.dumps(result['samples'], ensure_ascii=False, indent=2, allow_nan=False)+'\n'}
            elif args.command == 'application':
                input_path = args.protocol
                result = analyze_application(model, read_json(input_path))
                outputs = {'application_ranking.csv': application_csv(result)}
            elif args.command == 'fit-stability':
                input_path = args.generation
                result = robustness_check(model, read_json(input_path), read_json(args.settings), read_json(args.protocol))
                outputs = {}
            elif args.command in ('fit', 'fit-check'):
                input_path = args.samples if args.command == 'fit' else args.generation
                result = (fit_parameters(model, read_json(input_path), read_json(args.settings))
                          if args.command == 'fit' else fit_check(model, read_json(input_path), read_json(args.settings)))
                outputs = {}
            elif args.command == 'extract':
                input_path = args.batch
                result = extract_measurements(model, read_json(args.structure), read_json(args.plan),
                                              read_json(input_path), read_json(args.extraction),
                                              read_json(args.calibration) if args.calibration else None)
                outputs = {'measurements.json': json.dumps(result['measurements'], ensure_ascii=False,
                                                           indent=2, allow_nan=False) + '\n'}
            elif args.command == 'experiments':
                input_path = args.batch
                result = inspect_experiments(model, read_json(args.structure), read_json(args.plan), read_json(input_path))
                outputs = {}
            elif args.command == 'identify':
                input_path = args.records
                result = identify_dependencies(model, read_json(args.structure), read_json(input_path))
                outputs = {}
            elif args.command == 'structure':
                input_path = args.structure
                result = inspect_structure(model, read_json(input_path))
                outputs = {'fdna_summary.md': render_structure(result), 'structure_mapping.csv': structure_csv(result)}
            elif args.command == 'normalize':
                input_path = args.measurements
                result = normalize_measurements(model, read_json(args.calibration), read_json(input_path))
                outputs = {'fdna_summary.md': render_se_summary(result), 'se_values.csv': se_csv(result)}
            elif args.command in ('sweep', 'compare'):
                input_path = args.protocol
                protocol = read_json(input_path)
                if args.command == 'compare':
                    result = compare_readings(model, protocol, read_json(args.readings))
                    outputs = {'fdna_summary.md': render_comparison(result)}
                    if args.plot:
                        outputs['fdna_comparison.png'] = plot_comparison(result, model)
                else:
                    result = sweep(model, protocol)
                    outputs = {
                        'fdna_sweep.csv': csv_report(result),
                        'fdna_summary.md': render_sweep_summary(result),
                    }
                    if args.plot:
                        outputs['fdna_sweep.png'] = plot_sweep(result)
                outputs['protocol_snapshot.json'] = json.dumps(protocol, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
            else:
                input_path = args.scenario
                scenario = read_json(input_path)
                if args.model == ADDITIVE_MODEL:
                    if args.extension is None:
                        raise ValueError('δ扩展求值须提供--extension，不能默认δ为零')
                    result = evaluate_additive(model, scenario, read_json(args.extension))
                else:
                    if args.extension is not None:
                        raise ValueError('基础式不接受δ配置，请明确选择δ扩展模型')
                    result = evaluate(model, scenario)
                outputs = {
                    'scenario_snapshot.json': json.dumps(scenario, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                }
                if args.model == MODEL:
                    outputs['fdna_summary.md'] = render_summary(result)
            result['source_verification'] = provenance
            module = Path(__file__).resolve().parent
            output = args.output_dir.resolve()
            if output == module or module in output.parents:
                raise ValueError('结果不可写入能力依赖模块目录')
            protected = [args.case, input_path] + [
                (args.case.resolve().parent / source['path']).resolve()
                for source in model['sources'] if 'path' in source]
            if args.command in ('compare', 'protocol-audit'):
                protected.append(args.readings)
            if args.command == 'pca':
                protected.append(args.config)
            if args.command == 'fitted-application':
                protected.append(args.protocol)
                if args.reference_extension:
                    protected.append(args.reference_extension)
            if args.command == 'evaluate' and args.extension:
                protected.append(args.extension)
            if args.command == 'normalize':
                protected.append(args.calibration)
            if args.command in ('fit', 'fit-check', 'fit-stability'):
                protected.append(args.settings)
            if args.command == 'fit-stability':
                protected.append(args.protocol)
            if args.command in ('identify', 'experiments', 'extract'):
                protected.append(args.structure)
            if args.command in ('experiments', 'extract'):
                protected.append(args.plan)
            if args.command == 'extract':
                protected.append(args.extraction)
                if args.calibration:
                    protected.append(args.calibration)
            if args.command == 'prepare-fit':
                protected.extend([args.structure, args.plan, args.extraction, args.calibration, args.partition])
            write_outputs(args.output_dir, {
                'case_snapshot.json': json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                'fdna_report.json': json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                **outputs,
            }, protected=protected)
        printed = ({k: v for k, v in result.items() if k not in
                    ('rows', 'traces', 'baseline', 'comparisons', 'readings_snapshot', 'records',
                     'fit_inputs', 'measurements_snapshot', 'calibration_snapshot', 'structure_snapshot',
                     'graph', 'activity_order_relations', 'candidates', 'records_snapshot',
                     'runs', 'closures', 'plan_snapshot', 'batch_snapshot', 'measurements',
                     'normalization', 'experiment_check', 'config_snapshot', 'receivers', 'samples_snapshot',
                     'known_parameter_comparison', 'trials', 'sweep_snapshot', 'rankings', 'samples', 'extraction_snapshot',
                     'lineage', 'partition_snapshot', 'series', 'fit_snapshot', 'witnesses', 'tested_settings',
                     'candidate_results', 'parameter_bounds', 'protocol_snapshot', 'residual_audit',
                     'fitted_application', 'reference_application', 'response_differences')}
                   if args.command in ('fitted-application', 'pca', 'sweep', 'compare', 'normalize', 'structure', 'identify', 'experiments', 'extract', 'fit', 'fit-check', 'fit-stability', 'application', 'prepare-fit', 'residuals', 'precision', 'protocol-audit') else result)
        print(json.dumps(printed, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if result['status'] == 'blocked' else 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'invalid', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
