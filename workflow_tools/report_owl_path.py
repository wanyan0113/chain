"""Freeze phase-2B direct timing observations, scopes and reproducibility evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import statistics

from workflow_tools.benchmark_joint import ROOT,save


def med(values):return statistics.median(values)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--test-log',type=Path,required=True)
    args=parser.parse_args(argv);output=args.output_dir.resolve();source=args.source.resolve()
    if output.exists():raise ValueError('choose a new archive directory')
    data=json.loads((source/'measurements.json').read_text(encoding='utf-8'))
    if len(data['cases'])!=7:raise AssertionError('incomplete matrix')
    for case in data['cases']:
        if len(case['pipeline'])!=5 or len(case['java_cold'])!=5:raise AssertionError('missing repetitions')
        if not all(r['business_semantics_equal'] and r['actual_owl_calls']==1 and r['verification']['status']=='passed' for r in case['pipeline']):
            raise AssertionError('production pipeline correctness failed')
        if not all(r['rows'][0]['named_entailments_equal'] for r in case['java_cold']):raise AssertionError('API result mismatch')
    by_name={c['case']:c for c in data['cases']}
    if by_name['base']['configuration']['owl_result_hash']!=by_name['one-seal']['configuration']['owl_result_hash']:
        raise AssertionError('candidate-only contrast changed OWL results')
    if by_name['base']['configuration']['input_graph_digest']!=by_name['one-seal']['configuration']['input_graph_digest']:
        raise AssertionError('candidate-only contrast changed OWL input')
    if [by_name[f'flows-{n}']['configuration']['flow_count'] for n in (1,4)]!=[1,4]:
        raise AssertionError('activity-request axis ineffective')
    for case in data['cases']:
        if 'java_same_process' in case:
            if len(case['java_same_process']['rows'])!=6 or not all(r['named_entailments_equal'] for r in case['java_same_process']['rows']):
                raise AssertionError('JVM-repeat evidence incomplete')
        if 'python_same_process' in case:
            hot=case['python_same_process']
            if len(hot['rows'])!=6 or len({r['pid'] for r in hot['rows']})!=1:
                raise AssertionError('Python-repeat lifecycle differs')
    output.mkdir(parents=True)
    for name in ('environment.json','protocol.json','measurements.json'):shutil.copyfile(source/name,output/name)
    shutil.copyfile(args.test_log,output/'tests.log')
    inventory=[]
    for case in data['cases']:
        src=source/case['case'];dst=output/'inputs'/case['case'];dst.mkdir(parents=True)
        for name in ('config.json','input.rdf','query.json','expected-owl.json','subjects.txt','predicates.txt'):
            shutil.copyfile(src/name,dst/name)
        report=src/'reference-joint.json'
        inventory.append(dict(path=str(report),sha256=hashlib.sha256(report.read_bytes()).hexdigest(),bytes=report.stat().st_size))
    save(output/'full-report-inventory.json',inventory)
    lines=['# 阶段二-B：OWL路径与进程开销细分','',
        '7档，各5次生产路径及5次Java冷进程探针；另有同Python进程6次、两个同JVM进程组各6次。重复组第一轮单独保留，后5轮作为进程复用观测。',
        '35次生产路径的候选身份、评分和网络均与普通运行一致，并通过独立核验；7份普通参考通过独立穷举。35次冷Java探针及12次同JVM观测的具名类型/关系均与生产结果一致。','',
        '## 生产路径中位数','',
        '| 案例 | 实际流程/链 | 总时间(s) | OWL调用(s) | Python本体加载(s) | Java子进程整体(s) | Python查询转换(s) |',
        '| --- | --- | --- | --- | --- | --- | --- |']
    for case in data['cases']:
        rows=case['pipeline'];child=[r['owl_batches'][0]['child'][0]['rows'][0] for r in rows];cfg=case['configuration']
        values=[med([r['pipeline_seconds'] for r in rows]),med([r['owl_batches'][0]['seconds'] for r in rows]),
            med([sum(r['timings']['python_ontology_load']) for r in child]),
            med([sum(r['timings']['java_subprocess_inclusive']) for r in child]),
            med([sum(r['timings']['python_query_and_result_conversion']) for r in child])]
        lines.append('| '+case['case']+f" | {cfg['flow_count']}/{cfg['unique_chains']} | "+' | '.join(f'{v:.4f}' for v in values)+' |')
    lines+=['','## Java API直接埋点中位数','',
        '此探针使用相同HermiT jar及完整输入，直接调用OWLAPI/HermiT。它没有替换生产CLI，也不代表已成功拆开原CLI内部成本。构造器、检查、预计算和查询都可能执行推理，不能把某一列冒称全部纯推理时间。','',
        '| 案例 | 启动至READY(s) | 本体加载(s) | Reasoner构造(s) | 一致性(s) | 预计算(s) | 查询/格式化(s) |',
        '| --- | --- | --- | --- | --- | --- | --- |']
    for case in data['cases']:
        cold=case['java_cold']
        vals=[med([r['launch_to_ready_seconds'] for r in cold])]
        for label in ('java_ontology_load','java_reasoner_construct','java_consistency','java_precompute','java_query_and_format'):
            vals.append(med([r['rows'][0]['timings'][label] for r in cold]))
        lines.append('| '+case['case']+' | '+' | '.join(f'{v:.4f}' for v in vals)+' |')
    lines+=['','## 同进程重复','',
        '不复用World、本体对象或Reasoner；每次重新加载。Java同进程组复用JVM及其类加载/JIT状态；Python同进程组仍然为每次推理创建新JVM。这里的热仅指明确记录的进程状态。','',
        '| 组 | 第一轮(s) | 后5轮中位数(s) | 后5轮最小/最大(s) |',
        '| --- | --- | --- | --- |']
    for case in data['cases']:
        if 'java_same_process' in case:
            values=[r['timings']['java_batch'] for r in case['java_same_process']['rows']]
            lines.append(f"| {case['case']}同JVM，新本体/新Reasoner | {values[0]:.4f} | {med(values[1:]):.4f} | {min(values[1:]):.4f}/{max(values[1:]):.4f} |")
        if 'python_same_process' in case:
            values=[r['seconds'] for r in case['python_same_process']['rows']]
            lines.append(f"| base同Python，每轮新JVM | {values[0]:.4f} | {med(values[1:]):.4f} | {min(values[1:]):.4f}/{max(values[1:]):.4f} |")
    lines+=['','## 范围与解释','',
        '原始每次观测、进程PID、Java启动参数、缓存/对象复用标志与输入规模见[measurements.json](measurements.json)。环境及工具版本见[environment.json](environment.json)，源码与编译类摘要见[protocol.json](protocol.json)。',
        'base与one-seal的完整OWL输入及具名结果相同，实际候选分别4/1，覆盖均完整；flows-1与flows-4使用相同基础本体文件，但活动环节实例不同，完整OWL图并不相同，不能将“同本体文件”当作“同OWL输入”。',
        '原CLI中的纯JVM初始化、本体加载、Reasoner构造和分类细分仍未直接测得；相应纯JVM启动字段为null。READY延迟包含操作系统创建、JVM/主类启动与管道交付。禁止用总时间相减伪造内部纯推理时间。',
        '生产调用计时包含实验路由、子进程计时及图指纹开销，不能直接与阶段二未加相同埋点的耗时比较并声称加速。直接Java探针有不同调用顺序与输出方式，不能把它的各阶段时间回填为生产CLI内部时间。',
        '未引入持久JVM、应用缓存、并行或增量优化；同进程观测不能作为生产优化收益证明。有限规模没有测定一般退化点，跨平台与sequential_product_v2领域有效性仍待验证。',
        '完整业务报告在generated目录，其摘要及路径单列[full-report-inventory.json](full-report-inventory.json)；归档文件与源码摘要见[manifest.json](manifest.json)。','']
    (output/'README.md').write_text('\n'.join(lines),encoding='utf-8')
    files=[p for p in output.rglob('*') if p.is_file()]
    files += [ROOT/name for name in ('adapters/owl_infer.py','workflow_tools/owl_profile_worker.py','workflow_tools/benchmark_owl_path.py',
        'workflow_tools/report_owl_path.py','workflow_tools/java/OwlTimingProbe.java','tests/test_owl_profile.py','tests/test_joint_integration.py',
        'docs/task-flow/joint-optimization/stage2b-owl-path/README.md')]
    save(output/'manifest.json',dict(version='tfdo-owl-path-evidence-1.0',files=[dict(path=str(p.relative_to(ROOT)),
        sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
        sha256_lf_normalized=hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()) for p in files]))
    print(json.dumps(dict(matrix_cases=7,production_runs=35,cold_java_runs=35,jvm_repeat_runs=12,python_repeat_runs=6,output=str(output)),ensure_ascii=False))


if __name__=='__main__':main()
