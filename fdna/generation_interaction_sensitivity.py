"""Fit the two preregistered interaction controls on existing frozen samples."""
import argparse
from pathlib import Path

from source_data.common import read_json,write_outputs
from .generation_fitting import audit_generation,fit_joint_network,fitting_csvs
from .generation_delta_comparison import run_comparison
from .generation_delta_repeats import summarize_comparison,summary_csvs,render_summary,plot_summary
from .generation_varying_experiment import dumps
from .model import ModelError
from .structure import digest


def validate_frozen_protocol(generation,fit_config,delta_config,primary_base,primary_comparison,primary_summary):
    if generation.get('stage')!='stage2_interaction_generation' or generation.get('status')!='complete':
        raise ModelError('强弱对照须读取已完成的冻结交互样本')
    config=generation['interaction_config_snapshot'];primary=config['primary_group']
    expected=set(config['gamma_groups'])-{primary}
    selected=fit_config['groups']
    if (not expected or set(selected)!=expected or len(selected)!=len(expected) or delta_config['groups']!=selected):
        raise ModelError('只允许拟合预先指定的非主交互组，不能遗漏、重复或重拟合主组')
    generation_sha=digest(generation)
    if (primary_base.get('status')!='complete' or primary_comparison.get('status')!='complete' or primary_summary.get('status')!='complete'
        or primary_base['generation_report_sha256']!=generation_sha or primary_comparison['generation_sha256']!=generation_sha
        or primary_comparison['baseline_fit_sha256']!=digest(primary_base)
        or primary_summary['comparison_sha256']!=digest(primary_comparison)):
        raise ModelError('冻结主组拟合或摘要的版本不一致')
    if primary_base['config_snapshot']['groups']!=[primary] or primary_comparison['config_snapshot']['groups']!=[primary]:
        raise ModelError('冻结来源不是预先指定的主交互组')
    if fit_config['settings']!=primary_base['config_snapshot']['settings']:
        raise ModelError('强弱对照不得改变主组求解器、多初值或参数设置')
    for key in ('noise_std','repetitions'):
        if fit_config[key]!=primary_base['config_snapshot'][key] or delta_config[key]!=fit_config[key]:
            raise ModelError('强弱对照须沿用主组全部噪声档和重复编号')
    for key in ('fixed_delta','free_delta_bounds'):
        if delta_config[key]!=primary_comparison['config_snapshot'][key]:
            raise ModelError('强弱对照不得改变δ对照值或边界')
    return dict(generation_sha256=generation_sha,primary_baseline_sha256=digest(primary_base),
        primary_comparison_sha256=digest(primary_comparison),primary_summary_sha256=digest(primary_summary),
        preregistered_strengths=config['gamma_groups'],fitted_groups=selected,primary_refitted=False,
        new_samples_generated=False,solver_or_delta_settings_changed=False)


def run(args):
    out=args.output_dir;source=args.primary_dir
    if out.exists() and any(out.iterdir()):raise ModelError('结果目录须为空，不覆盖冻结对照')
    generation=read_json(source/'generation/generation_report.json')
    fc=read_json(args.fit_config);dc=read_json(args.delta_config)
    audit=validate_frozen_protocol(generation,fc,dc,read_json(source/'base/fit_report.json'),
        read_json(source/'comparison/delta_comparison_report.json'),read_json(source/'comparison/delta_repeats_report.json'))
    audit_generation(generation)
    write_outputs(out,{'实验预先约定.json':dumps(dict(base_fit=fc,delta=dc)),
        '冻结来源核验.json':dumps(audit)})
    print('Frozen primary protocol verified; fitting weak and strong controls only.',flush=True)
    progress=lambda row:print(dumps(row),flush=True)
    baseline=fit_joint_network(generation,fc,progress=progress)
    write_outputs(out/'base',{'fit_report.json':dumps(baseline),**fitting_csvs(baseline)})
    if baseline['status']!='complete':raise ModelError('基础式存在失败组，已保存，停止δ对照')
    comparison=run_comparison(generation,baseline,dc,allow_repeats=True,progress=progress)
    summary=summarize_comparison(comparison)
    write_outputs(out/'comparison',{'delta_comparison_report.json':dumps(comparison),
        'delta_repeats_report.json':dumps(summary),'交互强弱模型对照.md':render_summary(summary),
        **summary_csvs(summary),**plot_summary(summary)})
    print(dumps(dict(status=summary['status'],counts=summary['counts'],fitted_groups=fc['groups'])),flush=True)
    return 0 if summary['status']=='complete' else 1


def main():
    p=argparse.ArgumentParser(description='冻结样本上的交互强弱拟合敏感性对照')
    for name in ('primary-dir','fit-config','delta-config','output-dir'):p.add_argument('--'+name,type=Path,required=True)
    try:return run(p.parse_args())
    except (ValueError,OSError,KeyError,TypeError,ImportError) as exc:
        print(dumps(dict(status='invalid',error=str(exc))),flush=True);return 2


if __name__=='__main__':raise SystemExit(main())
