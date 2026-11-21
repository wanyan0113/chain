"""Reproducible staged runner for the registered varying-condition experiment."""
import argparse
import csv
import io
import json
from pathlib import Path

from source_data.common import read_json, write_outputs
from .generation_varying import run_varying_network_check
from .generation_fitting import fit_joint_network, fitting_csvs
from .generation_delta_comparison import run_comparison
from .generation_delta_repeats import summarize_comparison, summary_csvs, render_summary, plot_summary
from .model import ModelError
from .structure import digest


def csv_rows(rows):
    stream=io.StringIO(newline='')
    if rows:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    return '\ufeff'+stream.getvalue()


def dumps(value): return json.dumps(value,ensure_ascii=False,allow_nan=False)


def run(args):
    out=args.output_dir
    if out.exists() and any(out.iterdir()): raise ModelError('结果目录须为空，不覆盖既有结果')
    config=read_json(args.config); fit_config=read_json(args.fit_config); delta_config=read_json(args.delta_config)
    write_outputs(out,{'实验预先约定.json':dumps(dict(generation=config,base_fit=fit_config,delta=delta_config))})
    generation=run_varying_network_check(read_json(args.network),read_json(args.nodes),config)
    frozen=read_json(args.frozen_generation)
    if generation['source_baseline_sha256']!=digest(frozen):
        raise ModelError('根节点、旧条件组或旧抽样协议与冻结报告不一致')
    write_outputs(out/'generation',{'generation_report.json':dumps(generation),
        'condition_samples.csv':csv_rows(generation['condition_samples']),
        'structural_by_split.csv':csv_rows(generation['structural_by_split'])})
    progress=lambda row:print(dumps(row),flush=True)
    print('Frozen controls verified; fitting varying group only.',flush=True)
    baseline=fit_joint_network(generation,fit_config,progress=progress)
    write_outputs(out/'base',{'fit_report.json':dumps(baseline),**fitting_csvs(baseline)})
    if baseline['status']!='complete': raise ModelError('基础式存在失败组，已保存，不继续δ对照')
    comparison=run_comparison(generation,baseline,delta_config,allow_repeats=True,progress=progress)
    summary=summarize_comparison(comparison)
    write_outputs(out/'comparison',{'delta_comparison_report.json':dumps(comparison),
        'delta_repeats_report.json':dumps(summary),'变化条件结果说明.md':render_summary(summary),
        **summary_csvs(summary),**plot_summary(summary)})
    print(dumps(dict(status=summary['status'],generation=generation['counts'],comparison=summary['counts'])),flush=True)
    return 0 if summary['status']=='complete' else 1


def main():
    parser=argparse.ArgumentParser(description='保留冻结对照，开展逐接受节点独立变化条件实验')
    for name in ('network','nodes','config','fit-config','delta-config','frozen-generation','output-dir'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    try: return run(args)
    except (ValueError,OSError,KeyError,TypeError,ImportError) as exc:
        print(dumps(dict(status='invalid',error=str(exc))),flush=True); return 2


if __name__=='__main__': raise SystemExit(main())
