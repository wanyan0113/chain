"""python -m fdna inspect CASE; python -m fdna evaluate CASE --scenario SCENARIO ..."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from source_data.common import read_json, write_outputs
from .engine import MODEL, evaluate
from .model import inspect_model, load_model, verify_sources


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
            scenario = read_json(args.scenario)
            result = evaluate(model, scenario)
            result['source_verification'] = provenance
            module = Path(__file__).resolve().parent
            output = args.output_dir.resolve()
            if output == module or module in output.parents:
                raise ValueError('结果不可写入能力依赖模块目录')
            protected = [args.case, args.scenario] + [
                (args.case.resolve().parent / source['path']).resolve()
                for source in model['sources'] if 'path' in source]
            write_outputs(args.output_dir, {
                'case_snapshot.json': json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                'scenario_snapshot.json': json.dumps(scenario, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                'fdna_report.json': json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                'fdna_summary.md': render_summary(result),
            }, protected=protected)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if result['status'] == 'blocked' else 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'invalid', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
