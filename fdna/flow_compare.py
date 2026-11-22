"""Compare a replay with the validated reference; ignore only explicit version fields."""
import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import numpy as np
from .flow_scenario import verify
from .workflow_common import dumps

JSON_FILES=(
    '00_冻结输入/design_input.json','00_冻结输入/experiment.json',
    '01_链网生成/design_report.json','01_链网生成/实际计数与排序.json','01_链网生成/全部网络组合.json',
    '02_能力转换/已登记映射/输入_拓扑.json','02_能力转换/已登记映射/mapping.json','02_能力转换/任务状态对照/slots.json',
    '03_事件与SE/观测SE.json','03_事件与SE/生成审计.json','03_事件与SE/主体状态与划分.json',
    '03_事件与SE/随机流与事件核验.json','04_FDNA拟合/workflow_report.json','04_FDNA拟合/dependency_analysis.json',
    '05_共同来源情景/已知合成H情景.json','分层验收.json','实现文件校验.json')


def normalized(name,value):
    value=deepcopy(value);versions={}
    if name.endswith('workflow_report.json'):
        for ji,j in enumerate(value.get('jobs',[])):
            for li,l in enumerate(j['local_fits']):
                if not l.get('fit'):continue
                solver=l['fit']['solver']
                for field in ('numpy','scipy'):
                    if field in solver:versions[f'jobs/{ji}/local_fits/{li}/solver/{field}']=solver.pop(field)
    target=value.get('random_streams') if name.endswith('随机流与事件核验.json') else None
    if name=='分层验收.json':target=value.get('event_generation',{}).get('audit',{}).get('random_streams')
    if target is not None and 'numpy' in target:versions['generation_rng/numpy']=target.pop('numpy')
    return value,versions


def compare(reference,candidate):
    left,right=Path(reference),Path(candidate)
    with redirect_stdout(io.StringIO()):verify(left);verify(right)
    differences=[];versions=[]
    if (left/'00_冻结输入/场景设计.md').read_bytes()!=(right/'00_冻结输入/场景设计.md').read_bytes():differences.append('00_冻结输入/场景设计.md')
    for name in JSON_FILES:
        a,av=normalized(name,json.loads((left/name).read_text(encoding='utf-8')))
        b,bv=normalized(name,json.loads((right/name).read_text(encoding='utf-8')))
        if a!=b:differences.append(name)
        for key in sorted(set(av)|set(bv)):
            if av.get(key)!=bv.get(key):versions.append(dict(file=name,field=key,reference=av.get(key),candidate=bv.get(key)))
    csv_left={p.relative_to(left).as_posix() for p in left.rglob('*.csv')}
    csv_right={p.relative_to(right).as_posix() for p in right.rglob('*.csv')}
    if csv_left!=csv_right:differences.append('CSV文件集合')
    for name in sorted(csv_left & csv_right):
        if (left/name).read_bytes()!=(right/name).read_bytes():differences.append(name)
    with np.load(left/'03_事件与SE/微观事件.npz',allow_pickle=False) as a,np.load(right/'03_事件与SE/微观事件.npz',allow_pickle=False) as b:
        if set(a.files)!=set(b.files):differences.append('微观事件.npz数组集合')
        for key in set(a.files)&set(b.files):
            if not np.array_equal(a[key],b[key]):differences.append('微观事件.npz/'+key)
        arrays=len(set(a.files)&set(b.files))
    return dict(status='passed' if not differences else 'different',json_files_compared=len(JSON_FILES),
        csv_files_compared=len(csv_left&csv_right),event_arrays_compared=arrays,differences=differences,environment_differences=versions,
        scope='比较冻结MD、场景输入、实际链网、映射、观测与审计、原始事件数组、拟合和分析；每个结果包先验证完整文件哈希。',
        exclusions='图像/RDF序列化、运行环境说明及其清单哈希不要求跨环境字节相同；只允许明确列出的求解器/随机流版本字段不同。')


def main():
    p=argparse.ArgumentParser();p.add_argument('--reference',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--report',type=Path)
    args=p.parse_args()
    if args.report is not None and any(args.report.resolve().is_relative_to(folder.resolve()) for folder in (args.reference,args.candidate)):
        p.error('比较报告须另存结果包外，避免改变包的校验清单')
    result=compare(args.reference,args.candidate)
    if args.report is not None:args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(dumps(result),encoding='utf-8')
    print(dumps(result));return 0 if result['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
