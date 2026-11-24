"""Fixed-data sensitivity to prespecified random-start seeds; never selects a seed."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .model import ModelError
from .network_workflow import fit_network,validate_samples
from .structure import digest
from .training_refit import compact,compare_control,training_panel
from .workflow_common import csv_text,dumps


PROTOCOL=dict(id='flow06_solver_seed_sensitivity_v1',
    seeds=[20261122,2026112401,2026112402,2026112403,2026112404],control_seed=20261122,
    starts=6,solver='L-BFGS-B',max_nfev=1500,
    data_and_split='unchanged_original_90_training_30_holdout_base_cases',
    changed_factor='random_start_seed_only_neutral_start_retained',warm_start=False,
    select_seed_using_holdout=False,output_clipping=False,numerical_comparison_tolerance=1e-8,
    summary='finite_minimum_median_maximum_not_confidence_interval')


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def seed_config(config,seed):
    """Preserve every configuration field except model random seeds."""
    if type(seed) is not int or seed<0:raise ModelError('种子须为非负整数')
    result=deepcopy(config)
    if set(result['models'])!={'base','free_delta'}:raise ModelError('本协议只比较基础式与自由δ')
    for setting in result['models'].values():
        if any(setting.get(k)!=PROTOCOL[k] for k in ('starts','solver','max_nfev')) or 'initialization' in setting:
            raise ModelError('来源求解配置不符合固定六起点协议')
        setting['seed']=seed
    return result


def ranges(values):
    if not values:return dict(minimum=None,median=None,maximum=None,span=None)
    if any(not math.isfinite(x) for x in values):raise ModelError('摘要包含非有限值')
    return dict(minimum=min(values),median=float(np.median(values)),maximum=max(values),span=max(values)-min(values))


def summarize(records,seeds):
    if len(seeds)!=len(set(seeds)) or {r['seed'] for r in records}!=set(seeds) or len(records)!=len(seeds):
        raise ModelError('种子记录缺失或重复')
    keys=None;groups=defaultdict(list);metrics=defaultdict(list);point_keys=None
    for record in records:
        current={(f['fit_group'],f['model'],f['node']) for f in record['fits']}
        if len(current)!=len(record['fits']) or (keys is not None and current!=keys):raise ModelError('拟合条件缺失或重复')
        keys=current
        current_points={(p['fit_group'],p['model'],p['node'],p['sample_id']) for p in record['holdout_points']}
        if len(current_points)!=len(record['holdout_points']) or (point_keys is not None and current_points!=point_keys):
            raise ModelError('留出预测主键缺失或重复')
        point_keys=current_points
        for fit in record['fits']:groups[fit['fit_group'],fit['model'],fit['node']].append(fit)
        local_keys=set()
        for m in record['metrics']:
            key=(m['fit_group'],m['model'],m['node'],m['split'],m['prediction'])
            if key in local_keys:raise ModelError('指标主键重复')
            local_keys.add(key);metrics[key].append(m)
    parameters=[];diagnostics=[];prediction=[]
    for key,fits in sorted(groups.items()):
        header=dict(zip(('fit_group','model','node'),key));valid=[f for f in fits if f['status']=='fitted']
        values=defaultdict(list);losses=[]
        for f in valid:
            for p in f['parameters']:
                for name in ('alpha','beta'):values[name+':'+p['feeder']].append(p[name])
            if f['delta'] is not None:values['delta'].append(f['delta'])
            losses.append(f['attempts'][f['attempt_selection']['index']]['rmse'])
        for name,vs in sorted(values.items()):
            if len(vs)!=len(valid):raise ModelError('参数维度在种子间改变')
            parameters.append(dict(**header,parameter=name,planned=len(seeds),evaluated=len(vs),**ranges(vs)))
        diagnostics.append(dict(**header,planned=len(seeds),fitted=len(valid),failed=len(fits)-len(valid),
            selected_training_rmse=ranges(losses),flags=dict(Counter(flag for f in fits for flag in f['flags']))))
    for key,ms in sorted(metrics.items()):
        if len(ms)!=len(seeds):raise ModelError('指标条件不完整')
        valid=[m for m in ms if m['rmse'] is not None and m['evaluated']>0]
        prediction.append(dict(zip(('fit_group','model','node','split','prediction'),key),planned=len(seeds),
            evaluated=len(valid),unavailable=len(seeds)-len(valid),outside=sum(m['outside'] for m in ms),
            **ranges([m['rmse'] for m in valid])))
    point_ranges=[]
    lookup=[{(p['fit_group'],p['model'],p['node'],p['sample_id']):p for p in r['holdout_points']} for r in records]
    for key in sorted(point_keys or []):
        pts=[p[key] for p in lookup]
        if len({p['target'] for p in pts})!=1:raise ModelError('种子间留出目标发生变化')
        for kind in ('local','recursive'):
            vs=[p[kind+'_prediction'] for p in pts if p[kind+'_prediction'] is not None]
            point_ranges.append(dict(zip(('fit_group','model','node','sample_id'),key),prediction=kind,
                evaluated=len(vs),planned=len(seeds),**ranges(vs)))
    return dict(parameters=parameters,diagnostics=diagnostics,metrics=prediction,holdout_points=point_ranges)


def figures(summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from io import BytesIO
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'svg.fonttype':'path'})
    keys=sorted({(r['fit_group'],r['node']) for r in summary['diagnostics']});outputs={}
    colors={'base':'#356f9d','free_delta':'#168c87'}
    def save(fig,name):
        for ext in ('png','svg'):
            buffer=BytesIO();fig.savefig(buffer,format=ext,dpi=160,bbox_inches='tight');outputs[name+'.'+ext]=buffer.getvalue()
        plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(15,8),sharey=True,layout='constrained')
    for ax,kind in zip(axes,('alpha','beta','delta')):
        for i,key in enumerate(keys):
            for j,(mode,color) in enumerate(colors.items()):
                r=next((r for r in summary['parameters'] if (r['fit_group'],r['node'],r['model'])==(*key,mode) and r['parameter'].split(':')[0]==kind),None)
                if r:
                    y=i+(j-.5)*.2;ax.plot([r['minimum'],r['maximum']],[y,y],color=color,lw=2);ax.scatter(r['median'],y,c=color,s=22)
        ax.set(title=kind+'参数范围',yticks=range(len(keys)),yticklabels=[g+' / '+n for g,n in keys],xlabel='参数值');ax.grid(axis='x',alpha=.2)
    axes[0].invert_yaxis();fig.suptitle('固定数据，仅改变随机起点种子：5组六起点拟合\n线=最小至最大，点=中位数；蓝=基础式，绿=自由δ；有限范围不是置信区间',fontsize=13)
    save(fig,'01_种子间参数范围')
    fig,axes=plt.subplots(1,2,figsize=(13,8),sharex=True,sharey=True,layout='constrained')
    for ax,kind in zip(axes,('local','recursive')):
        for i,key in enumerate(keys):
            for j,(mode,color) in enumerate(colors.items()):
                r=next(r for r in summary['metrics'] if (r['fit_group'],r['node'],r['model'],r['split'],r['prediction'])==(*key,mode,'holdout',kind))
                if r['evaluated']:
                    y=i+(j-.5)*.2;ax.plot([r['minimum'],r['maximum']],[y,y],color=color,lw=2);ax.scatter(r['median'],y,c=color,s=22)
        ax.set(title='局部留出RMSE' if kind=='local' else '整网递推留出RMSE',yticks=range(len(keys)),yticklabels=[g+' / '+n for g,n in keys],xlabel='RMSE / SE点');ax.grid(axis='x',alpha=.2)
    for mode,color in colors.items():axes[0].plot([],[],color=color,marker='o',label='基础式' if mode=='base' else '自由δ')
    axes[0].legend(loc='lower right')
    axes[0].invert_yaxis();fig.suptitle('固定原留出集：5个预先登记种子的预测误差范围\n线=最小至最大，点=中位数；不按留出误差选种子，不作为独立泛化测试',fontsize=13)
    save(fig,'02_种子间留出误差范围')
    return outputs


def run(source_dir,protocol_path,output_dir):
    source=Path(source_dir).resolve();out=Path(output_dir).resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):raise ModelError('输出须为空目录，拒绝覆盖')
    if out==source or out.is_relative_to(source):raise ModelError('输出不得位于正式来源包内')
    protocol=read(protocol_path)
    if protocol!=PROTOCOL:raise ModelError('必须使用预先登记的固定种子协议')
    from .flow_scenario import verify
    verified=verify(source)
    original=read(source/'04_FDNA拟合/workflow_report.json');rows=read(source/'04_FDNA拟合/输入_SE记录.json')
    network=original['network_snapshot'];config=original['config_snapshot'];validate_samples(network,rows,config)
    train,held,groups,_=training_panel(rows)
    if (len(train),len(held),len(groups))!=(90,30,3):raise ModelError('协议只适用原06配对面板')
    if digest(dict(network=network,rows=rows,config=config))!=original['input_sha256']:raise ModelError('冻结来源摘要不一致')
    if seed_config(config,protocol['control_seed'])!=config:raise ModelError('控制种子与原设置不一致')
    out.mkdir(parents=True,exist_ok=True);(out/'逐种子记录').mkdir()
    def write(name,value):(out/name).write_text(dumps(value),encoding='utf-8',newline='\n')
    write('预登记方案.json',protocol)
    records=[];control=None
    for index,seed in enumerate(protocol['seeds']):
        print(dumps(dict(stage='seed_started',seed=seed,index=index+1,total=len(protocol['seeds']))),flush=True)
        result=fit_network(network,deepcopy(rows),seed_config(config,seed))
        if seed==protocol['control_seed']:control=compare_control(original,result)
        record=compact(result,index,digest(protocol),original['input_sha256'])
        record.update(seed=seed,config_snapshot=result['config_snapshot'])
        records.append(record);write(f'逐种子记录/seed_{seed}.json',record)
        print(dumps(dict(stage='seed_finished',seed=seed,failed=sum(f['status']!='fitted' for f in record['fits']))),flush=True)
    summary=summarize(records,protocol['seeds'])
    report=dict(status='completed',protocol=protocol,protocol_sha256=digest(protocol),control=control,
        source=dict(directory=str(source),fit_sha256=sha(source/'04_FDNA拟合/workflow_report.json'),input_sha256=original['input_sha256'],data_sha256=original['data_sha256'],source_files_verified=verified),
        audit=dict(seeds=len(records),local_fits=sum(len(r['fits']) for r in records),
            failed_local_fits=sum(f['status']!='fitted' for r in records for f in r['fits']),
            flags=dict(Counter(flag for r in records for f in r['fits'] for flag in f['flags']))),
        summary=summary,policies=dict(training_resampled=False,holdout_changed=False,seed_selected=False,
            original_overwritten=False,confidence_interval=False,solver_changed=False,models_refitted=True))
    write('seed_report.json',report)
    for key,name in [('parameters','参数范围.csv'),('diagnostics','训练损失与诊断.csv'),('metrics','训练与留出误差.csv'),('holdout_points','逐留出预测范围.csv')]:
        (out/name).write_text(csv_text(summary[key]),encoding='utf-8',newline='')
    for name,content in figures(summary).items():(out/name).write_bytes(content)
    verify(source)
    write('manifest.json',{p.relative_to(out).as_posix():sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='固定06数据，仅改变预登记的六起点种子')
    parser.add_argument('--source-dir',required=True);parser.add_argument('--protocol',required=True);parser.add_argument('--output-dir',required=True)
    args=parser.parse_args();run(args.source_dir,args.protocol,args.output_dir)
