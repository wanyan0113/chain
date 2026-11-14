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
    check = commands.add_parser('inspect', help='核对案例结构与来源')
    check.add_argument('case', type=Path)
    run = commands.add_parser('evaluate', help='依基础式求取效能')
    run.add_argument('case', type=Path)
    run.add_argument('--scenario', type=Path, required=True)
    run.add_argument('--model', choices=[MODEL], required=True)
    run.add_argument('--output-dir', type=Path, required=True)
    scan = commands.add_parser('sweep', help='按明确约定逐项改变无入边节点效能')
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
    args = parser.parse_args(argv)
    try:
        model = load_model(args.case)
        provenance = verify_sources(model, args.case)
        if any(source['status'] in ('changed', 'unavailable') for source in provenance):
            raise ValueError('已登记的本地来源发生变化或不可读取，请重新核对来源')
        if args.command == 'inspect':
            result = inspect_model(model)
            result['source_verification'] = provenance
        else:
            if args.command == 'identify':
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
                result = evaluate(model, scenario)
                outputs = {
                    'scenario_snapshot.json': json.dumps(scenario, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                    'fdna_summary.md': render_summary(result),
                }
            result['source_verification'] = provenance
            module = Path(__file__).resolve().parent
            output = args.output_dir.resolve()
            if output == module or module in output.parents:
                raise ValueError('结果不可写入能力依赖模块目录')
            protected = [args.case, input_path] + [
                (args.case.resolve().parent / source['path']).resolve()
                for source in model['sources'] if 'path' in source]
            if args.command == 'compare':
                protected.append(args.readings)
            if args.command == 'normalize':
                protected.append(args.calibration)
            if args.command == 'identify':
                protected.append(args.structure)
            write_outputs(args.output_dir, {
                'case_snapshot.json': json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                'fdna_report.json': json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                **outputs,
            }, protected=protected)
        printed = ({k: v for k, v in result.items() if k not in
                    ('rows', 'traces', 'baseline', 'comparisons', 'readings_snapshot', 'records',
                     'fit_inputs', 'measurements_snapshot', 'calibration_snapshot', 'structure_snapshot',
                     'graph', 'activity_order_relations', 'candidates', 'records_snapshot')}
                   if args.command in ('sweep', 'compare', 'normalize', 'structure', 'identify') else result)
        print(json.dumps(printed, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if result['status'] == 'blocked' else 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'invalid', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
