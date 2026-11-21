"""Frozen post-baseline diagnostics and descriptive feeder quality; no fitting."""
import argparse
from collections import defaultdict
from copy import deepcopy
import math
from pathlib import Path

from source_data.common import read_json,write_outputs
from .generation_delta_diagnostics import analyze_diagnostics,csv_outputs
from .generation_delta_repeats import distribution
from .generation_varying_experiment import csv_rows,dumps
from .model import ModelError,_finite
from .structure import digest


def validate_bundle_config(config):
    _finite(config)
    if (not isinstance(config,dict) or set(config)!={'format_version','id','basis','policy','low_input_thresholds','sources'}
        or config['format_version']!='1.0' or not isinstance(config['sources'],list) or not config['sources']):
        raise ModelError('扩展诊断配置字段无效')
    thresholds=config['low_input_thresholds']
    if (not isinstance(thresholds,list) or not thresholds or len(set(thresholds))!=len(thresholds)
        or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not 0<v<100 for v in thresholds)):
        raise ModelError('低输入描述阈值须明确、唯一并在(0,100)内')
    ids=set();groups=set()
    for source in config['sources']:
        if (set(source)!={'id','generation_report','experiment_dir','expected_groups','illustration'}
            or not isinstance(source['id'],str) or not source['id'] or source['id'] in ids
            or not isinstance(source['expected_groups'],list) or not source['expected_groups']):
            raise ModelError('诊断来源字段或编号无效')
        if any(not isinstance(g,str) or not g for g in source['expected_groups']):raise ModelError('诊断组编号无效')
        if len(set(source['expected_groups']))!=len(source['expected_groups']) or groups&set(source['expected_groups']):
            raise ModelError('诊断来源中的条件组重复，会重复计数')
        ids.add(source['id']);groups.update(source['expected_groups'])


def feeder_quality(generation,groups,thresholds):
    """Actual clean inputs, one row per repeat/split/receiver (never noise copies)."""
    import numpy as np
    rows=[r for r in generation['clean_runs'] if r['group'] in groups]
    if set(r['group'] for r in rows)!=set(groups):raise ModelError('供给质量统计缺少选定的条件组')
    if len({r['network_run_id'] for r in rows})!=len(rows):raise ModelError('生成状态主键重复')
    result=[];feeders=[];clipping=[]
    for group in groups:
        for rep in range(1,generation['config_snapshot']['repetitions']+1):
            for split in ('train','holdout'):
                selected=[r for r in rows if (r['group'],r['repetition'],r['split'])==(group,rep,split)]
                if not selected:raise ModelError('质量统计分组没有训练或验证样本')
                for node in generation['topology']['receivers']:
                    parents=sorted(selected[0]['node_details'][node]['parent_clean'])
                    if not parents:raise ModelError('接受节点缺少实际供给输入')
                    if any(sorted(r['node_details'][node]['parent_clean'])!=parents for r in selected):raise ModelError('分组内上游清单不一致')
                    inputs=np.array([[r['node_values_clean'][p] for p in parents] for r in selected])
                    target=np.array([r['node_values_clean'][node] for r in selected]);minimum=np.min(inputs,axis=1)
                    deficit=np.maximum(0,minimum-target)
                    head=dict(group=group,repetition=rep,split=split,node=node,count=len(selected))
                    result.append(dict(**head,parent_count=len(parents),target_mean=float(np.mean(target)),
                        target_min=float(np.min(target)),target_max=float(np.max(target)),
                        target_below_min_fraction=float(np.mean(deficit>1e-10)),
                        base_local_rmse_lower_bound=float(np.sqrt(np.mean(deficit**2))),
                        generation_clipped_count=sum(r['node_details'][node]['process_clipped'] for r in selected),
                        target_at_zero_count=int(np.sum(target==0)),target_at_100_count=int(np.sum(target==100)),
                        **{'min_input_below_'+str(t)+'_fraction':float(np.mean(minimum<t)) for t in thresholds}))
                    for j,p in enumerate(parents):
                        v=inputs[:,j];tied=np.sum(np.isclose(inputs,minimum[:,None],rtol=0,atol=1e-10),axis=1)>1
                        is_min=np.isclose(v,minimum,rtol=0,atol=1e-10)
                        feeders.append(dict(**head,feeder=p,mean=float(np.mean(v)),minimum=float(np.min(v)),maximum=float(np.max(v)),
                            unique_minimum_fraction=float(np.mean(is_min&~tied)),tied_minimum_fraction=float(np.mean(is_min&tied)),
                            **{'below_'+str(t)+'_fraction':float(np.mean(v<t)) for t in thresholds}))
                    for j,a in enumerate(parents):
                        for k,b in enumerate(parents):
                            if k<=j:continue
                            x,y=inputs[:,j],inputs[:,k]
                            correlation=float(np.corrcoef(x,y)[0,1]) if np.ptp(x)>0 and np.ptp(y)>0 else None
                            clipping.append(dict(**head,first_feeder=a,second_feeder=b,pearson_r=correlation,
                                undefined_reason='constant_input' if correlation is None else None))
    observations=[]
    sigmas=generation['config_snapshot']['noise_std']
    for group in groups:
        for sigma in sigmas:
            selected=[r for r in generation['observations'] if r['group']==group and r['noise_sigma']==sigma]
            for node in generation['topology']['receivers']:
                observations.append(dict(group=group,noise_sigma=sigma,node=node,count=len(selected),
                    observation_clipped_count=sum(not 0<=r['node_values_clean'][node]+r['observation_noise'][node]<=100 for r in selected),
                    observed_at_zero_count=sum(r['node_values_observed'][node]==0 for r in selected),
                    observed_at_100_count=sum(r['node_values_observed'][node]==100 for r in selected)))
    return dict(node_quality=result,feeder_coverage=feeders,feeder_correlations=clipping,observation_boundaries=observations)


def analyze_source(generation,baseline,comparison,summary,source,policy,thresholds,progress=None):
    if comparison['config_snapshot']['groups']!=source['expected_groups']:
        raise ModelError('实际诊断条件组与预设来源不一致')
    settings=deepcopy(policy);settings['id']=source['id']+'_frozen_diagnostics';settings['illustration']=deepcopy(source['illustration'])
    report,points=analyze_diagnostics(generation,baseline,comparison,summary,settings,progress=progress)
    quality=feeder_quality(generation,source['expected_groups'],thresholds)
    lookup={(r['group'],r['repetition'],r['split'],r['node']):r for r in quality['node_quality']}
    bound_checks=0
    for job in baseline['jobs']:
        if job['group'] not in source['expected_groups']:continue
        for m in job['metrics']:
            if m['target']=='clean' and m['mode']=='local':
                actual=lookup[job['group'],job['repetition'],m['split'],m['node']]['base_local_rmse_lower_bound']
                if not math.isclose(actual,m['rmse_lower_bound'],rel_tol=1e-10,abs_tol=1e-10):raise ModelError('供给质量表与局部结构下界不一致')
                bound_checks+=1
    report['quality_bound_checks']=bound_checks
    report['source_hashes']=dict(generation=digest(generation),baseline=digest(baseline),comparison=digest(comparison),summary=digest(summary))
    return report,points,quality


def run(args):
    config=read_json(args.config);validate_bundle_config(config)
    out=args.output_dir
    if out.exists() and any(out.iterdir()):raise ModelError('扩展诊断目录须为空，不覆盖既有结果')
    write_outputs(out,{'诊断预先约定.json':dumps(config)})
    reports=[];plot_points=[];all_quality=defaultdict(list)
    for source in config['sources']:
        experiment=args.project_root/source['experiment_dir']
        def progress(row):
            if row['audited_local_fits']%30==0:print(dumps(dict(source=source['id'],**row)),flush=True)
        report,points,quality=analyze_source(read_json(args.project_root/source['generation_report']),read_json(experiment/'base/fit_report.json'),
            read_json(experiment/'comparison/delta_comparison_report.json'),read_json(experiment/'comparison/delta_repeats_report.json'),
            source,config['policy'],config['low_input_thresholds'],progress)
        outputs={'diagnostics_report.json':dumps(report),**csv_outputs(report,points)}
        outputs.update({k+'.csv':csv_rows(v) for k,v in quality.items()})
        write_outputs(out/source['id'],outputs)
        reports.append(report)
        wanted={'model','group','noise_sigma','repetition','node','actual_clean','predicted','residual_clean','base_before_delta','delta','branch','outside'}
        plot_points.extend({k:v for k,v in p.items() if k in wanted} for p in points
            if p['split']=='holdout' and p['prediction']=='recursive' and p['noise_sigma'] in config['policy']['plot_noise_std'])
        for k,v in quality.items():all_quality[k].extend(v)
        del points,quality
    from .generation_extended_diagnostics_report import build_bundle_report
    outputs=build_bundle_report(reports,plot_points,dict(all_quality),config)
    write_outputs(out/'summary',outputs)
    print(dumps(dict(status='complete',sources=len(reports),counts={k:sum(r['counts'][k] for r in reports) for k in reports[0]['counts']})),flush=True)
    return 0


def main():
    p=argparse.ArgumentParser(description='变化条件、过程与交互的冻结结果深入诊断')
    p.add_argument('--config',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--project-root',type=Path,default=Path.cwd())
    try:return run(p.parse_args())
    except (ValueError,OSError,KeyError,TypeError,ImportError) as exc:
        print(dumps(dict(status='invalid',error=str(exc))),flush=True);return 2


if __name__=='__main__':raise SystemExit(main())
