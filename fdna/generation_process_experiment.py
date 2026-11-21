"""Run the process-innovation extension without refitting its frozen controls."""
import argparse
from pathlib import Path

from source_data.common import read_json,write_outputs
from .generation_process import run_process_network_check
from .generation_fitting import fit_joint_network,fitting_csvs
from .generation_delta_comparison import run_comparison
from .generation_delta_repeats import summarize_comparison,summary_csvs,render_summary,plot_summary
from .generation_varying_experiment import csv_rows,dumps
from .model import ModelError
from .structure import digest


def run(args):
    out=args.output_dir
    if out.exists() and any(out.iterdir()): raise ModelError('结果目录须为空，不能覆盖冻结结果')
    config=read_json(args.config);fc=read_json(args.fit_config);dc=read_json(args.delta_config)
    write_outputs(out,{'实验预先约定.json':dumps(dict(generation=config,base_fit=fc,delta=dc))})
    generation=run_process_network_check(read_json(args.network),read_json(args.nodes),config)
    if generation['source_varying_generation_sha256']!=digest(read_json(args.frozen_generation)):
        raise ModelError('过程波动来源与冻结变化条件报告不一致')
    write_outputs(out/'generation',{'generation_report.json':dumps(generation),
        'process_samples.csv':csv_rows(generation['process_samples']),
        'structural_by_split.csv':csv_rows(generation['process_structural_by_split'])})
    print('Frozen varying controls verified; fitting process group only.',flush=True)
    progress=lambda row:print(dumps(row),flush=True)
    baseline=fit_joint_network(generation,fc,progress=progress)
    write_outputs(out/'base',{'fit_report.json':dumps(baseline),**fitting_csvs(baseline)})
    if baseline['status']!='complete': raise ModelError('基础式存在失败组，结果已保存，停止δ对照')
    comparison=run_comparison(generation,baseline,dc,allow_repeats=True,progress=progress)
    summary=summarize_comparison(comparison)
    write_outputs(out/'comparison',{'delta_comparison_report.json':dumps(comparison),
        'delta_repeats_report.json':dumps(summary),'过程波动模型对照.md':render_summary(summary),
        **summary_csvs(summary),**plot_summary(summary)})
    print(dumps(dict(status=summary['status'],counts=summary['counts'])),flush=True)
    return 0 if summary['status']=='complete' else 1


def main():
    p=argparse.ArgumentParser(description='变化条件基础上的配对过程波动扩展实验')
    for name in ('network','nodes','config','fit-config','delta-config','frozen-generation','output-dir'):
        p.add_argument('--'+name,type=Path,required=True)
    try: return run(p.parse_args())
    except (ValueError,OSError,KeyError,TypeError,ImportError) as exc:
        print(dumps(dict(status='invalid',error=str(exc))),flush=True);return 2


if __name__=='__main__': raise SystemExit(main())
