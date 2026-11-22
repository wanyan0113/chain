"""Portable V1 acceptance: explicit file cases, observed inputs and failed-node propagation."""
import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import site
import subprocess
import sys
import tempfile

from .model import ModelError
from .network_workflow import read_samples,fit_network,analyze_dependencies,export_outputs
from .network_preflight import inspect_data
from .workflow_common import csv_text,dumps,write_bundle


def check_cases(directory):
    cases=json.loads((directory/'cases.json').read_text(encoding='utf-8'));results=[];outputs={}
    with tempfile.TemporaryDirectory(prefix='fdna_acceptance_') as tmp:
        for case in cases:
            command=[sys.executable,'-B','-m','fdna.network_preflight','--network',str(directory/case['network']),
                '--samples',str(directory/case['samples']),'--config',str(directory/case['config']),
                '--layout',case['layout'],'--output-dir',str(Path(tmp)/case['id'])]
            completed=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',
                cwd=Path(__file__).resolve().parents[1],timeout=120)
            try:status=json.loads(completed.stdout)['status']
            except (ValueError,KeyError):status='unreadable_output'
            passed=completed.returncode==case['expected_exit'] and status==case['expected_status']
            results.append(dict(case_id=case['id'],expected_status=case['expected_status'],actual_status=status,
                expected_exit=case['expected_exit'],actual_exit=completed.returncode,passed=passed))
            outputs['案例日志/'+case['id']+'.txt']=dumps(dict(command=command,exit_code=completed.returncode))+'\n'+completed.stdout+'\n'+completed.stderr
            if (Path(tmp)/case['id']).exists():
                for p in (Path(tmp)/case['id']).iterdir():
                    if p.is_file():outputs['案例预检查/'+case['id']+'/'+p.name]=p.read_bytes()
    return results,outputs


def acceptance_figures(report):
    import io
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10})
    nodes=report['topology']['receivers'];jobs=report['jobs'];labels={'base':'基础式','fixed_delta':'固定δ','free_delta':'自由δ'}
    outputs={}
    def save(name,fig):
        for ext in ('png','svg'):
            stream=io.BytesIO();fig.savefig(stream,format=ext,dpi=180,bbox_inches='tight');outputs[name+'.'+ext]=stream.getvalue()
        plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(12,4.8),layout='constrained')
    for ax,kind in zip(axes,('local','recursive')):
        x=np.arange(len(nodes))
        for k,j in enumerate(jobs):
            vals=[next(m['rmse'] for m in j['metrics'] if (m['node'],m['split'],m['prediction'])==(n,'holdout',kind)) for n in nodes]
            ax.bar(x+(k-(len(jobs)-1)/2)*.24,vals,.24,label=labels.get(j['model'],j['model']))
        ax.set(xticks=x,xticklabels=nodes,ylabel='对模拟观测目标的留出RMSE（SE点）',title='局部预测' if kind=='local' else '整网递推')
        ax.legend(fontsize=9);ax.grid(axis='y',alpha=.2)
    fig.suptitle('observed入口验收：供给与目标均取模拟观测SE\n单次既有模拟重复、σ=2；不使用干净供给，不是实测校准，无误差条')
    save('图01_observed入口留出误差',fig)
    flags=['LOCAL_RANK_DEFICIENT','DELTA_AT_BOUNDARY','PREDICTION_OUTSIDE_SE_RANGE'];titles=['局部秩不足','δ触界','样本预测越界']
    fig,axes=plt.subplots(1,3,figsize=(12,4.6),layout='constrained')
    for ax,flag,title in zip(axes,flags,titles):
        matrix=np.array([[flag in next(l for l in j['local_fits'] if l['node']==n)['flags'] for j in jobs] for n in nodes],dtype=float)
        ax.imshow(matrix,cmap='YlOrRd',vmin=0,vmax=1,aspect='auto')
        for i in range(len(nodes)):
            for k,j in enumerate(jobs):
                label='不适用' if flag=='DELTA_AT_BOUNDARY' and j['model']!='free_delta' else '是' if matrix[i,k] else '否'
                ax.text(k,i,label,ha='center',va='center',color='white' if matrix[i,k] else '#222222')
        ax.set(xticks=range(len(jobs)),xticklabels=[labels.get(j['model'],j['model']) for j in jobs],yticks=range(len(nodes)),yticklabels=nodes,title=title)
    fig.suptitle('observed入口保留原始诊断\n每格一次局部拟合；分支明确、拟合收敛不等于参数唯一')
    save('图02_observed入口参数与范围提示',fig)
    return outputs


def run(directory):
    directory=Path(directory).resolve()
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    for name,expected in manifest.items():
        if hashlib.sha256((directory/name).read_bytes()).hexdigest()!=expected:raise ModelError('验收案例哈希不一致：'+name)
    results,outputs=check_cases(directory)
    if not all(r['passed'] for r in results):
        return dict(status='failed',case_results=results),{'案例结果.csv':csv_text(results),**outputs}
    read=lambda n:json.loads((directory/n).read_text(encoding='utf-8'))
    network=read('network.json');config=read('config.json');rows=read_samples(directory/'observed.json',network)
    mapping=rows==read_samples(directory/'observed_wide.csv',network)==read_samples(directory/'observed_long.csv',network,'long')
    if not mapping or config['input_role']!='observed' or any('predictor_values' in r for r in rows):raise ModelError('观测输入角色或映射验收失败')
    report=fit_network(network,rows,config);analysis=analyze_dependencies(report,rows,config['analysis'])
    small=read('insufficient_samples_samples.json');failure=fit_network(network,small,config)
    failure_pass=failure['status']=='incomplete' and all(l['status']=='insufficient_samples' for j in failure['jobs'] for l in j['local_fits']) and all(p['recursive_prediction'] is None for j in failure['jobs'] for p in j['points'])
    if not failure_pass:raise ModelError('样本不足未按预期阻断递推')
    flags=Counter(f for j in report['jobs'] for l in j['local_fits'] for f in l['flags'])
    environment=dict(python=sys.version,executable=sys.executable,prefix=sys.prefix,base_prefix=sys.base_prefix,
        isolated_venv=sys.prefix!=sys.base_prefix,user_site_enabled=site.ENABLE_USER_SITE,platform=platform.platform(),
        package_root=str(Path(__file__).resolve().parents[1]),
        distributions={d.metadata['Name']:d.version for d in importlib.metadata.distributions()})
    counts=dict(case_checks=len(results),case_checks_passed=sum(r['passed'] for r in results),mapping_equal=mapping,
        samples=len(rows),train=sum(r['split']=='train' for r in rows),holdout=sum(r['split']=='holdout' for r in rows),
        fit_jobs=len(report['jobs']),local_fits=sum(len(j['local_fits']) for j in report['jobs']),
        fitted_local=sum(l['status']=='fitted' for j in report['jobs'] for l in j['local_fits']),
        insufficient_data_blocks_recursion=failure_pass,flags=dict(flags),
        intervention_effects=len(analysis['effects']),intervention_outside=sum(e['outside'] for e in analysis['effects']))
    source_files=[p for folder in ('fdna','source_data') for p in (Path(__file__).resolve().parents[1]/folder).rglob('*') if p.is_file() and p.suffix in ('.py','.json')]
    source_hashes={str(p.relative_to(Path(__file__).resolve().parents[1])):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    summary=dict(status='passed' if report['status']=='complete' else 'failed',counts=counts,environment=environment,
        fixture_manifest_sha256=hashlib.sha256((directory/'manifest.json').read_bytes()).hexdigest(),source_hashes=source_hashes,
        input_role='observed',simulated=True,real_data_calibration=False,input_measurement_error_modeled=False)
    outputs.update({'案例结果.csv':csv_text(results),'验收摘要.json':dumps(summary),'环境记录.json':dumps(environment),
        '样本不足_状态.json':dumps(failure),'输入来源.json':dumps(read('provenance.json'))})
    outputs.update({'observed/'+name:value for name,value in export_outputs(report,analysis).items()})
    outputs.update(acceptance_figures(report))
    outputs['结果说明.md']=f'''# V1.0 可复跑验收与observed入口演示

逐例验收{len(results)}个文件案例，全部符合预登记状态。缺失节点不补0；非法拓扑与记录拒绝；样本不足报告needs_data，并由完整拟合入口保留失败和受阻递推。JSON、CSV宽表及长表映射一致。

observed演示仅复用既有第22包rep1_sigma2的250条模拟观测、200训练/50留出，移除干净供给字段。三模型固定数据和划分，完成{counts['fitted_local']}/{counts['local_fits']}次局部拟合。固定δ=-5，自由δ边界[-10,10]。这不是新机制或新敏感性矩阵，也不是真实数据校准；供给测量误差仍未建模。

图01展示对模拟观测目标的留出误差。局部预测使用模拟观测上游，整网仅输入根节点；两种误差不能混称。仅一次既有重复，不画SD或置信区间，不与第22包干净供给结果作无条件模型优劣比较。

图02展示每次局部拟合的秩、δ和预测范围标记。计数：{dict(flags)}。模型内退化响应有{counts['intervention_outside']}条越界，保留未裁剪原值。诊断被正确报告是验收要求，不要求所有诊断消失。

Python、包版本、隔离环境与源文件哈希见环境记录和验收摘要。本包案例检查数不等于单元测试数量；完整回归另行保存。失败案例日志保留实际退出码，预期拒绝不算程序失败。
'''
    return summary,outputs


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--cases-dir',type=Path,default=Path('data/fdna/v1_acceptance'));p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args(argv)
    try:
        if a.output_dir.exists() and any(a.output_dir.iterdir()):raise ModelError('输出目录非空')
        summary,outputs=run(a.cases_dir);write_bundle(a.output_dir,outputs)
        print(dumps({k:v for k,v in summary.items() if k not in ('source_hashes','environment')}));return 0 if summary['status']=='passed' else 1
    except (ValueError,OSError,KeyError,TypeError,subprocess.TimeoutExpired) as exc:
        print(dumps(dict(status='invalid',error=str(exc))));return 2


if __name__=='__main__':raise SystemExit(main())
