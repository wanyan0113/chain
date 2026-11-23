"""Time the frozen historical replay against explicit reusable preparation."""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import platform
import statistics
import time
from unittest.mock import patch

import numpy as np

from fdna.prepared_prediction import PreparedTopology,PreparedPredictor
from fdna.refit_propagation import read,sha,replay,training_ranges,run as run_full_replay
from fdna.workflow_common import dumps,csv_text
from workflow_tools import reference_refit_replay as reference
from workflow_tools.verify_refit_propagation import verify


PROTOCOL=dict(id='flow06_prepared_prediction_runtime_v1',reference_commit='19d31b88baa5f09f8620c96dd6fd7798f7ed2e69',
    panel='08_full_training_control_six_jobs_all_30_original_holdout_cases',trials=5,warmup_per_implementation=1,
    order='alternate_reference_first_and_prepared_first',timed_scope='in_memory_complete_replay_including_validation_and_parameter_preparation',
    excluded=['file_loading','independent_verification','fitting','plotting'],cache_scope='one_model_job_within_one_replay_only',
    comparison='all_saved_arrays_and_missing_masks',full_regression='replay_all_50_existing_refitted_models_and_compare_original_09',
    interpretation='local_runtime_measurement_not_universal_or_end_to_end_solver_speedup')


def code_sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def compare_arrays(left,right):
    if set(left)!=set(right):raise ValueError('数组字段不一致')
    numeric=0;masks=0;maximum=0.
    for name,a in left.items():
        b=right[name]
        if (a.shape!=b.shape or a.dtype!=b.dtype or not np.array_equal(a,b,equal_nan=True)
                or a.tobytes(order='C')!=b.tobytes(order='C')):
            raise ValueError('数组、缺失掩码或精确数值改变: '+name)
        if a.dtype.kind=='b':masks+=a.size
        else:
            numeric+=a.size;valid=np.isfinite(a)&np.isfinite(b)
            if valid.any():maximum=max(maximum,float(np.max(abs(a[valid]-b[valid]))))
    return dict(numeric_values_compared=numeric,mask_values_compared=masks,maximum_difference=maximum,bitwise_equal_including_missing=True)


def figure(report):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'svg.fonttype':'path'})
    fig,axes=plt.subplots(1,2,figsize=(12,5),layout='constrained')
    times=report['timing_summary'];names=['reference','prepared'];labels=['原逐次校验','准备一次后复用'];colors=['#7f99a9','#16847f']
    med=[times[n]['median_seconds'] for n in names]
    axes[0].bar(labels,med,color=colors)
    for i,name in enumerate(names):
        values=[r['seconds'] for r in report['timings'] if r['implementation']==name]
        axes[0].scatter(np.full(len(values),i),values,color='#18344a',s=20,zorder=3)
        axes[0].text(i,max(values)*1.03,f'{med[i]:.4f} s',ha='center')
    axes[0].set(ylabel='完整内存传播回放耗时 / 秒',title='5轮计时：柱为中位数，点为每轮值',ylim=(0,max(r['seconds'] for r in report['timings'])*1.22))
    counts=report['call_counts'];x=np.arange(2)
    axes[1].bar(x-.18,[counts[n]['topology_validations'] for n in names],.36,label='完整拓扑校验',color='#7f99a9')
    axes[1].bar(x+.18,[counts[n]['dag_evaluations'] for n in names],.36,label='DAG计算',color='#16847f')
    for i,name in enumerate(names):
        for offset,field in [(-.18,'topology_validations'),(.18,'dag_evaluations')]:
            value=counts[name][field];axes[1].text(i+offset,value+4,str(value),ha='center')
    axes[1].set(xticks=x,xticklabels=labels,ylabel='每次6模型×30基准回放的调用次数',title='相同输入与输出，减少重复工作',ylim=(0,330));axes[1].legend()
    fig.suptitle('传播运行效率优化：本机固定面板测试\n包括每轮准备；不含文件加载、拟合、独立核验或绘图，不代表端到端求解加速',fontsize=13)
    outputs={}
    for ext in ('png','svg'):
        stream=BytesIO();fig.savefig(stream,format=ext,dpi=180,bbox_inches='tight');outputs['01_传播计算性能对照.'+ext]=stream.getvalue()
    plt.close(fig);return outputs


def run(source_dir,frozen_report,protocol_path,output_dir):
    source=Path(source_dir);out=Path(output_dir)
    if out.exists() and any(out.iterdir()):raise ValueError('拒绝覆盖非空输出目录')
    if read(protocol_path)!=PROTOCOL:raise ValueError('基准方案与登记协议不同')
    source_audit=verify(source);saved=read(source/'propagation_report.json');refits=Path(saved['source_refit_directory'])
    refit_report=read(refits/'refit_report.json');original=Path(refit_report['source']['directory'])
    network=read(original/'04_FDNA拟合/workflow_report.json')['network_snapshot']
    rows=read(original/'04_FDNA拟合/输入_SE记录.json');draws=read(refits/'训练成组抽样索引.json')
    record=read(refits/'控制复跑.json')['record'];axes=saved['axes']
    args=(network,record,rows,[tuple(j) for j in axes['jobs']],axes['base_cases'],[tuple(p) for p in axes['pairs']],axes['fractions'],
          training_ranges(network,rows,draws['training_base_cases']))
    implementations=dict(reference=reference.replay,prepared=replay);baseline=None
    for name,fn in implementations.items():
        current=fn(*args)
        if baseline is None:baseline=current
        else:control_comparison=compare_arrays(baseline,current)
    timings=[]
    for trial in range(PROTOCOL['trials']):
        for position,name in enumerate(['reference','prepared'] if trial%2==0 else ['prepared','reference']):
            started=time.perf_counter();current=implementations[name](*args);elapsed=time.perf_counter()-started
            compare_arrays(baseline,current)
            timings.append(dict(trial=trial+1,position=position+1,implementation=name,seconds=elapsed))
    counts={}
    with patch.object(reference,'layout',wraps=reference.layout) as top,patch.object(reference,'predict',wraps=reference.predict) as pred:
        reference.replay(*args);counts['reference']=dict(topology_validations=top.call_count,dag_evaluations=pred.call_count)
    with patch.object(PreparedTopology,'from_network',wraps=PreparedTopology.from_network) as top,patch.object(PreparedPredictor,'predict',autospec=True,side_effect=PreparedPredictor.predict) as pred:
        replay(*args);counts['prepared']=dict(topology_validations=top.call_count,dag_evaluations=pred.call_count)
    if counts!={'reference':{'topology_validations':277,'dag_evaluations':276},'prepared':{'topology_validations':1,'dag_evaluations':186}}:
        raise ValueError('完整固定面板的实际调用次数与设计不同')
    out.mkdir(parents=True)
    print(dumps(dict(stage='timing_complete',timings=timings,call_counts=counts)),flush=True)
    started=time.perf_counter()
    run_full_replay(refits,frozen_report,Path(__file__).resolve().parents[1]/'data/fdna/refit_propagation_protocol.json',out/'全量复跑')
    full_seconds=time.perf_counter()-started
    with np.load(source/'逐模型逐基准数组.npz') as a,np.load(out/'全量复跑/逐模型逐基准数组.npz') as b:
        full_comparison=compare_arrays({k:a[k] for k in a.files},{k:b[k] for k in b.files})
    independent=verify(out/'全量复跑')
    (out/'全量复跑/独立标量复核.json').write_text(dumps(independent),encoding='utf-8',newline='\n')
    summary={}
    for name in implementations:
        values=[r['seconds'] for r in timings if r['implementation']==name]
        summary[name]=dict(minimum_seconds=min(values),median_seconds=statistics.median(values),maximum_seconds=max(values),trials=len(values))
    speedup=summary['reference']['median_seconds']/summary['prepared']['median_seconds']
    report=dict(protocol=PROTOCOL,timings=timings,timing_summary=summary,median_speedup=speedup,call_counts=counts,
        control_array_comparison=control_comparison,all_50_models_comparison=full_comparison,independent_verification=independent,
        full_replay_seconds_including_io_verification_and_figures=full_seconds,source_directory=str(source.resolve()),
        source_manifest_sha256=sha(source/'manifest.json'),source_verification=source_audit,
        source_code_hash_policy='normalize_CRLF_to_LF',reference_file_sha256=code_sha(Path(reference.__file__)),original_reference_source_sha256=reference.SOURCE_FILE_SHA256,
        optimized_files_sha256={str(p.relative_to(Path(__file__).resolve().parents[1])).replace('\\','/'):code_sha(p) for p in
            [Path(__file__).resolve().parents[1]/'fdna/prepared_prediction.py',Path(__file__).resolve().parents[1]/'fdna/refit_propagation.py']},
        environment=dict(python=platform.python_version(),numpy=np.__version__,platform=platform.platform()))
    (out/'benchmark_report.json').write_text(dumps(report),encoding='utf-8',newline='\n')
    (out/'逐轮计时.csv').write_text(csv_text(timings),encoding='utf-8',newline='')
    (out/'固定方案.json').write_text(dumps(PROTOCOL),encoding='utf-8',newline='\n')
    for name,value in figure(report).items():(out/name).write_bytes(value)
    (out/'结果说明.md').write_text(f'''# 传播计算效率优化

同一原30个留出基准、6个模型任务、9组关系、五档比例，保留参数、数据、公式、缺失掩码与未裁剪输出。每个显式准备对象复制并冻结拓扑和参数，不保留观测；每次调用仍检查根输入和干预维度。相同模型×根输入×干预节点×比例的多个目标共用一次DAG计算，缓存仅存于该模型任务，不跨模型、输入面板或重拟合轮次。

![性能对照](01_传播计算性能对照.png)

参考为提交19d31b8中原layout/predict/replay三个函数的原样副本，专供性能对照，不用于正式计算。预热每种实现1次后，5轮交替顺序计时；每轮包含完整内存回放及准备，排除文件加载、拟合、绘图和独立核验。原实现中位数{summary['reference']['median_seconds']:.6f}秒，新实现{summary['prepared']['median_seconds']:.6f}秒，本机此固定面板中位数比值{speedup:.2f}倍；不是端到端求解器加速或通用性能保证。

实际完整拓扑校验从每次回放277次降为1次，DAG计算从276次降为186次。参数在每个模型任务绑定时检查一次；不使用进程级全局缓存，源字典变更不会偷偷改变已准备模型，使用新参数须重新绑定。

优化后重新回放已有50轮实验模型的405000条响应，与原09包比较{full_comparison['numeric_values_compared']}个数值和{full_comparison['mask_values_compared']}个布尔掩码，逐元素完全一致，包含NaN位置。独立标量复核405000条响应及270行摘要通过。没有新拟合、新SE、改边或覆盖原09。

性能复跑的完整结果保存在全量复跑子目录；该完整过程含读取、核验和绘图的用时另存报告，不与内存计时混算。优化不改变预测精度、参数可辨识性或真实有效性。当前删边对照中保留方程的局部训练目标与输入不变，重复拟合本身不会增加新的结构证据；完整替代结构重估仍待有明确语义的新结构。
''',encoding='utf-8',newline='\n')
    # Verify immutable upstream source again; our output is a new sibling package.
    for name,value in read(source/'manifest.json').items():
        if sha(source/name)!=value:raise ValueError('原09包发生变化')
    inventory={p.relative_to(out).as_posix():sha(p) for p in sorted(out.rglob('*')) if p.is_file()}
    (out/'manifest.json').write_text(dumps(inventory),encoding='utf-8',newline='\n')
    print(dumps(dict(speedup=speedup,all_50_models_comparison=full_comparison,independent=independent)));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for key in ('source-dir','frozen-report','protocol','output-dir'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();run(args.source_dir,args.frozen_report,args.protocol,args.output_dir)
