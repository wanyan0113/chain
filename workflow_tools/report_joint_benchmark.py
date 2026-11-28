"""Archive controlled benchmark records and derive a concise, auditable report."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import statistics
from itertools import product

from workflow_tools.benchmark_joint import save,ROOT,VERSION


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',action='append',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--test-log',type=Path)
    args=parser.parse_args(argv)
    args.output_dir=args.output_dir.resolve()
    if args.output_dir.exists():raise ValueError('choose a new archive directory')
    rows={};inventory=[];batches=[];excluded=[]
    for index,source in enumerate(args.source,1):
        batch=f'batch-{index}';target=args.output_dir/'evidence'/batch
        for filename in ('environment.json','protocol.json','benchmark.json'):
            target.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/filename,target/filename)
        if (source/'hardware.json').exists():shutil.copyfile(source/'hardware.json',target/'hardware.json')
        if (source/'harness-source.py').exists():shutil.copyfile(source/'harness-source.py',target/'harness-source.py')
        data=json.loads((source/'benchmark.json').read_text(encoding='utf-8'))
        batches.append(dict(id=batch,source=str(source)))
        for row in data['cases']:
            name=row['case']['id']
            if row['case']['group']=='P1' and all(r['status']=='completed' for r in row['repetitions']):
                config=json.loads((source/name/'input.json').read_text(encoding='utf-8'))
                enabled=any(f['fact']['predicate']=='needs_analysis' and f['value'] is True
                            for f in config['inputs'][0][0]['request']['static_facts'])
                counts_match=all(sum(f['statistics']['candidate_count'] for f in r['stage_metrics']['flow_counts'])==row['case']['value']
                                 for r in row['repetitions'])
                if not enabled or not counts_match:
                    excluded.append(dict(batch=batch,case=name,reason='ineffective_experimental_axis'))
                    continue
            complete=all(r['status']=='completed' for r in row['repetitions'])
            if not complete:
                excluded.append(dict(batch=batch,case=name,reason='failed_or_timed_out'))
                failed=next(r for r in row['repetitions'] if r['status']!='completed')
                shutil.copyfile(source/name/f"run-{failed['repetition']}"/'worker.log',target/f'{name}-first-failure.log')
                continue
            if name in rows:raise ValueError('duplicate completed case: '+name)
            if len(row['repetitions'])<5 or not row['summary']['semantic_repeatable']:
                raise ValueError('insufficient repetitions or unstable semantics')
            target_input=target/name/'input.json';target_input.parent.mkdir()
            shutil.copyfile(source/name/'input.json',target_input)
            for r in row['repetitions']:
                report=source/name/f"run-{r['repetition']}"/'joint_report.json'
                inventory.append(dict(batch=batch,case=name,repetition=r['repetition'],
                    path=str(report),bytes=report.stat().st_size,sha256=hashlib.sha256(report.read_bytes()).hexdigest()))
            rows[name]=dict(row,batch=batch)
            first=json.loads((source/name/'run-1/joint_report.json').read_text(encoding='utf-8'))
            rows[name]['per_flow_unique_chain_counts']=[dict(task_id=flow['task_id'],flow_id=flow['flow_id'],
                count=sum(c['flow_id']==flow['flow_id'] and c['task_id']==flow['task_id']
                          for c in first['candidates'].values())) for flow in first['flows'].values()]
            if row['repetitions'][0]['correctness']['global_optimality']=='bounded_global_infeasible':
                from tests.joint_reference import chains,feasible
                report=json.loads((source/name/'run-1/joint_report.json').read_text(encoding='utf-8'))
                snapshot=report['input_snapshot'];scene=snapshot['scene'];mapping=snapshot['mapping']
                groups=[]
                for job in snapshot['jobs']:
                    expected=chains(job,scene,mapping)
                    actual={}
                    for candidate in report['candidates'].values():
                        if candidate['task_id']==job['task_id']:
                            key=(tuple(s['activity_id'] for s in candidate['identity_payload']['activity_instance_sequence']),
                                 tuple(s['artifact'] for s in candidate['chain']['plan']['steps']))
                            actual[key]=candidate
                    if set(actual)!=set(expected):raise AssertionError('infeasibility candidate coverage differs from reference')
                    groups.append(list(expected.values()))
                total=0;feasible_count=0
                for chosen in product(*groups):
                    total+=1;feasible_count+=bool(feasible(chosen,scene))
                if feasible_count:raise AssertionError('reported infeasibility contradicted by reference')
                rows[name]['infeasibility_reference']=dict(raw_combinations=total,feasible=0,
                    candidate_coverage='matched',scope='independent complete enumeration on first repetition')
            correct=row['repetitions'][0]['correctness']
            if (correct['global_optimality']=='not_proven' and correct['feasible_solution'] and
                    correct['flow_search_complete'] and correct['chain_generation_complete']):
                from tests.joint_reference import chains,feasible
                report=first;snapshot=report['input_snapshot'];scene=snapshot['scene'];mapping=snapshot['mapping']
                groups=[]
                for job in snapshot['jobs']:
                    expected=chains(job,scene,mapping);group=[]
                    for chain in expected.values():
                        normalized={}
                        for metric in ('time','accuracy','cost'):
                            low=min(c['metrics'][metric] for c in expected.values())
                            high=max(c['metrics'][metric] for c in expected.values());value=chain['metrics'][metric]
                            normalized[metric]=.5 if high==low else ((value-low)/(high-low) if metric=='accuracy' else (high-value)/(high-low))
                        group.append(dict(chain,score=sum(scene['weights'][k]*v for k,v in normalized.items())))
                    groups.append(group)
                scores=[sum(c['score'] for c in chosen)/len(chosen) for chosen in product(*groups) if feasible(chosen,scene)]
                optimum=max(scores);incumbent=report['design_report']['network']['score']
                if incumbent>optimum+1e-12:raise AssertionError('incumbent exceeds independent optimum')
                rows[name]['incumbent_quality_reference']=dict(best_score=optimum,incumbent_score=incumbent,
                    absolute_score_gap=max(0,optimum-incumbent),same_raw_candidate_pool=True,
                    production_optimality_remains=correct['global_optimality'])
    result=dict(benchmark_version=VERSION,cases=list(rows.values()),batches=batches,excluded=excluded,
                full_report_inventory=inventory,domain_validity='pending')
    save(args.output_dir/'results.json',result)
    if args.test_log:shutil.copyfile(args.test_log,args.output_dir/'evidence/benchmark-tests.log')
    lines=['# 阶段二：受控规模与性能基线','',
        f"共{len(rows)}档，每档5次，共{sum(len(r['repetitions']) for r in rows.values())}次完成运行。各档确定性语义摘要一致。",
        '流程、评分和网络语义未改变；本轮没有实施算法优化。精度模型领域有效性仍为pending。','',
        '主时间是run_joint，包含独立核验，不含导入、穷举参考和文件输出；峰值是20ms目标间隔采样的进程树工作集和，含Java，可能重复计入共享页。','',
        '| 案例 | 时间中位数/最小/最大(s) | 采样峰值(MiB) | 流程/唯一链 | 网络节点 | 结论 |',
        '| --- | --- | --- | --- | --- | --- |']
    for name,row in rows.items():
        r=row['repetitions'][0];s=row['summary'];stage=r['stage_metrics'];counts=stage['network_counts'] or {}
        flows=sum(f['statistics']['candidate_count'] for f in stage['flow_counts'])
        lines.append(f"| {name} | {s['median_runtime']:.3f}/{s['min_runtime']:.3f}/{s['max_runtime']:.3f} | "
            f"{s['sampled_peak_memory']/1048576:.1f} | {flows}/{stage['chain_counts']['unique']} | "
            f"{counts.get('nodes','未执行')} | {r['correctness']['global_optimality']} |")
    lines+=['','## 分阶段时间','',
        '以下为各档5次的阶段时间中位数。候选生成包含OWL，不能把各列相加；独立核验含搜索证明复核。OWL包含序列化、推理子进程及JVM启动、HermiT计算和结果读取。','',
        '| 案例 | 流程搜索(s) | 候选生成含OWL(s) | OWL(s) | 联合搜索(s) | 独立核验(s) |',
        '| --- | --- | --- | --- | --- | --- |']
    for name,row in rows.items():
        values=[]
        for label in ('flow_search','chain_generation_inclusive','owl_inference','network_optimization','independent_verification'):
            measurements=[r['stage_metrics'][label]['seconds'] for r in row['repetitions'] if label in r['stage_metrics']]
            values.append(f'{statistics.median(measurements):.3f}' if measurements else '未执行')
        lines.append('| '+name+' | '+' | '.join(values)+' |')
    lines+=['','## 正确性及证据边界','',
        '每次完成运行保存实际覆盖状态、预算终止原因、候选集合摘要、独立核验状态和最优性等级。完整可行案例与独立排列/组合参考对照；未生成可行解的运行不能标为所选网络核验通过。',
        'P4固定库存仅有4份消耗资源，5任务可能无解；这种提早剪枝不能说明任务数量扩大后性能反而更好。P3只测试子类层级，不覆盖复杂规则交互。',
        '只有进程冷启动数据；文件系统缓存不可控，没有可比较的持久JVM热启动组。此基线规模有限，未证明大规模生产能力，也未验证跨平台。',
        '计数、每次阶段时间和阶段工作集观测均见[results.json](results.json)。原始输入、环境和批次报告位于evidence；完整业务报告保留在generated目录，路径、字节数与SHA256列入results.json，不将它们误称为已归档到Git的文件。',
        '早期输入构造或测量工具异常批次按excluded列出，保存首个失败日志；不参与完成运行统计。',
        '完整源文件和归档摘要见[manifest.json](manifest.json)。复现入口及实验变量见[protocol.md](../protocol.md)。','']
    (args.output_dir/'README.md').write_text('\n'.join(lines),encoding='utf-8')
    paths=[p for p in args.output_dir.rglob('*') if p.is_file()]
    paths += [ROOT/'workflow_tools/benchmark_joint.py',ROOT/'workflow_tools/report_joint_benchmark.py',ROOT/'tests/test_joint_benchmark.py',ROOT/'tests/joint_reference.py',
        ROOT/'docs/task-flow/joint-optimization/stage2-performance/protocol.md',ROOT/'docs/task-flow/joint-optimization/stage2-performance/README.md']
    paths += list((ROOT/'knowledge/benchmark-stage2').glob('*.ttl'))
    save(args.output_dir/'manifest.json',dict(format_version='tfdo-performance-evidence-1.0',files=[dict(
        path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
        sha256_lf_normalized=hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()) for p in paths]))
    print(json.dumps(dict(cases=len(rows),runs=len(inventory),excluded=excluded,output=str(args.output_dir)),ensure_ascii=False))


if __name__=='__main__':main()
