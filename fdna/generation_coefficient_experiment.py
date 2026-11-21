"""Run the first registered coefficient batch against a frozen varying baseline."""
import argparse
from pathlib import Path

from source_data.common import read_json, write_outputs
from .generation_coefficients import run_coefficient_check, coefficient_groups
from .generation_fitting import audit_generation, fit_joint_network, fitting_csvs
from .generation_delta_comparison import run_comparison
from .generation_delta_repeats import summarize_comparison, summary_csvs, plot_summary
from .generation_varying_experiment import dumps
from .model import ModelError
from .structure import digest


def validate_protocol(generation, fc, dc, reference, base, comparison):
    if reference.get('status')!='complete' or base.get('status')!='complete' or comparison.get('status')!='complete':
        raise ModelError('冻结来源未完成')
    if (digest(reference)!=generation['source_varying_generation_sha256']
            or base['generation_report_sha256']!=digest(reference)
            or comparison['generation_sha256']!=digest(reference)
            or comparison['baseline_fit_sha256']!=digest(base)):
        raise ModelError('冻结来源指纹不一致')
    groups=set(coefficient_groups(generation['coefficient_config_snapshot'])[1])
    if set(fc['groups'])!=groups or len(fc['groups'])!=len(groups) or dc['groups']!=fc['groups']:
        raise ModelError('必须完整且仅拟合预设系数组')
    if fc['settings']!=base['config_snapshot']['settings']:
        raise ModelError('系数实验不能改变求解设置')
    for k in ('noise_std','repetitions'):
        if fc[k]!=base['config_snapshot'][k] or dc[k]!=fc[k]:
            raise ModelError('必须保留全部配对噪声及重复编号')
    for k in ('fixed_delta','free_delta_bounds'):
        if dc[k]!=comparison['config_snapshot'][k]:
            raise ModelError('不能改变δ设置')
    return dict(source_generation_sha256=digest(reference), source_base_sha256=digest(base),
        source_comparison_sha256=digest(comparison), baseline_refitted=False, settings_changed=False)


def main():
    p=argparse.ArgumentParser(description='生成系数的配对敏感性正式实验')
    for name in ('config','fit-config','delta-config','reference-dir','output-dir'):
        p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args(); out=args.output_dir; source=args.reference_dir
    if out.exists() and any(out.iterdir()): raise ModelError('输出目录须为空')
    reference=read_json(source/'generation/generation_report.json')
    audit_generation(reference)
    generation=run_coefficient_check(reference['network_snapshot'],reference['node_configs_snapshot'],read_json(args.config))
    fc=read_json(args.fit_config);dc=read_json(args.delta_config)
    audit=validate_protocol(generation,fc,dc,reference,read_json(source/'base/fit_report.json'),
        read_json(source/'comparison/delta_comparison_report.json'))
    write_outputs(out,{'实验预先约定.json':dumps(dict(generation=read_json(args.config),base_fit=fc,delta=dc)),
        '冻结来源核验.json':dumps(audit)})
    write_outputs(out/'generation',{'generation_report.json':dumps(generation)})
    progress=lambda row:print(dumps(row),flush=True)
    baseline=fit_joint_network(generation,fc,progress=progress)
    write_outputs(out/'base',{'fit_report.json':dumps(baseline),**fitting_csvs(baseline)})
    if baseline['status']!='complete': raise ModelError('基础式有失败组，停止对照')
    comparison=run_comparison(generation,baseline,dc,allow_repeats=True,progress=progress)
    summary=summarize_comparison(comparison)
    write_outputs(out/'comparison',{'delta_comparison_report.json':dumps(comparison),
        'delta_repeats_report.json':dumps(summary),**summary_csvs(summary),**plot_summary(summary)})
    print(dumps(dict(status=summary['status'],counts=summary['counts'])),flush=True)
    return 0 if summary['status']=='complete' else 1


if __name__=='__main__':raise SystemExit(main())
