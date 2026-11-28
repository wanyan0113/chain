"""Audit raw labels and archive phase-three controlled evidence, not a domain pass."""
from collections import Counter
import csv
import gzip
import hashlib
import json
from pathlib import Path
import shutil

from workflow_tools.accuracy_validation import Evidence,load,summarize
from workflow_tools.audit_accuracy_data import replay,reference_endpoint
from workflow_tools.benchmark_joint import ROOT,save


def plot(cases,path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(13,3.9),sharey=True)
    groups=[(['B-independent-2','C-independent-3','C-independent-4','C-independent-6'],'Independent, absorbing'),
        (['D-shared-difficulty-2','D-shared-difficulty-4','D-shared-difficulty-6'],'Shared difficulty'),
        (['E-repeated-2','E-repeated-4','E-repeated-6'],'Same operator repeated')]
    by_id={c['case']['id']:c['pooled'] for c in cases}
    for ax,(names,title) in zip(axes,groups):
        rows=[by_id[name] for name in names];x=[r['length'] for r in rows]
        y=[r['signed_error_observed_minus_predicted'] for r in rows]
        lower=[v-r['discrepancy_ci95_simultaneous'][0] for r,v in zip(rows,y)]
        upper=[r['discrepancy_ci95_simultaneous'][1]-v for r,v in zip(rows,y)]
        ax.axhspan(-.05,.05,color='#dde8dd',alpha=.6,label='Predeclared tolerance')
        ax.axhline(0,color='#555555',linewidth=.7)
        ax.errorbar(x,y,yerr=[lower,upper],marker='o',capsize=4,color='#245c83')
        ax.set_title(title);ax.set_xlabel('Number of stages');ax.set_xticks(x);ax.grid(axis='y',alpha=.2)
    axes[0].set_ylabel('Observed minus product prediction')
    fig.suptitle('Controlled labels only; nominal 95% conservative envelopes',fontsize=11)
    fig.tight_layout();fig.savefig(path,format='svg');plt.close(fig)


def main(argv=None):
    parser=__import__('argparse').ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--test-log',type=Path,required=True)
    parser.add_argument('--regression-log',type=Path)
    args=parser.parse_args(argv);source=args.source.resolve();output=args.output_dir.resolve()
    if output.exists():raise ValueError('archive already exists')
    protocol=json.loads((source/'protocol.json').read_text(encoding='utf-8'))
    results=json.loads((source/'results.json').read_text(encoding='utf-8'))
    inventory=json.loads((source/'raw-inventory.json').read_text(encoding='utf-8'))
    if len(results['cases'])!=19 or len(inventory)!=95:raise AssertionError('incomplete experiment')
    audited=[];raw_by_name={r['path'].replace('\\','/'):r for r in inventory}
    # Re-read every raw observation and replay labels without the generator.
    for record in results['cases']:
        spec=record['case'];pooled=Evidence(len(spec['stages']))
        for repetition in record['repetitions']:
            relative=f"{spec['id']}/seed-{repetition['seed']}.csv.gz";path=source/relative
            if hashlib.sha256(path.read_bytes()).hexdigest()!=raw_by_name[relative]['sha256']:
                raise AssertionError('raw data digest changed')
            data=load(path,len(spec['stages']));pooled.merge(data)
            recalculated=summarize(data,spec,protocol['acceptance'])
            if recalculated!=repetition['result']:raise AssertionError('per-seed result cannot be reproduced')
            reference=replay(path,spec)
            if reference['endpoint_correct']!=recalculated['observed_correct']:
                raise AssertionError('independent final label counts differ')
            if reference['rows_checked']!=raw_by_name[relative]['rows']:raise AssertionError('raw row coverage changed')
            audited.append(dict(path=relative,**reference))
        combined=summarize(pooled,spec,protocol['acceptance'])
        errors=[r['result']['signed_error_observed_minus_predicted'] for r in record['repetitions']]
        directions_agree=all(e>0 for e in errors) or all(e<0 for e in errors)
        if combined['decision'].startswith('systematic_') and not directions_agree:
            combined['decision']='inconclusive_direction_instability'
        stored={k:v for k,v in record['pooled'].items() if k!='all_seed_error_signs_agree'}
        if combined!=stored:raise AssertionError('pooled summary cannot be reproduced')
    output.mkdir(parents=True)
    for name in ('protocol.json','results.json','raw-inventory.json'):shutil.copyfile(source/name,output/name)
    for r in inventory:
        target=output/'raw'/r['path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/r['path'],target)
    shutil.copyfile(args.test_log,output/'tests.log')
    if args.regression_log:shutil.copyfile(args.regression_log,output/'regression-tests.log')
    save(output/'independent-label-audit.json',dict(status='passed',files=95,rows=sum(r['rows_checked'] for r in audited),
        records=audited,scope='declared synthetic mechanics and label counts; not real-domain truth validation'))
    plot(results['cases'],output/'error-by-length.svg')
    decisions=Counter(r['pooled']['decision'] for r in results['cases'])
    lines=['# 阶段三：串行多环节正确率的受控检验','',
        '**真实领域有效性：pending。数据为合成标签与明示故障注入，不是真实法器试验。**','',
        f"19场景×5种子，95份原始压缩CSV，共{sum(r['rows'] for r in inventory):,}个样本观测。每场景每环节校准25000个样本，端到端评估50000个独立留出样本。",
        '判定规则在运行前冻结：5个百分点容差、按场景名义95% Wilson/Bonferroni包络及每分层类别至少100个样本。没有根据结果重新挑选种子或阈值。','',
        '受控结论计数：'+', '.join(f'{k}={v}' for k,v in sorted(decisions.items()))+'。','',
        '![按环节数分组的预测偏差](error-by-length.svg)','',
        '## 分场景结果','',
        '正偏差表示乘积低估，负偏差表示乘积高估。区间是实测率与预测乘积差异的保守名义95%包络；人群不匹配一行只作描述，不作为有效模型检验。','',
        '| 场景 | 长度 | 乘积预测 | 实测终点正确率 | 偏差及区间 | 判定 |',
        '| --- | --- | --- | --- | --- | --- |']
    translation={'equivalent_within_tolerance':'当前容差内等效','systematic_underprediction':'系统性低估',
        'systematic_overprediction':'系统性高估','blocked_population_mismatch':'人群不匹配，阻塞','inconclusive':'证据不足'}
    for record in results['cases']:
        r=record['pooled'];lo,hi=r['discrepancy_ci95_simultaneous']
        lines.append(f"| {r['case_id']} | {r['length']} | {r['prediction']:.4f} | {r['observed']:.4f} | "
            f"{r['signed_error_observed_minus_predicted']:+.4f} [{lo:+.4f},{hi:+.4f}] | {translation.get(r['decision'],r['decision'])} |")
    lines+=['','## 可作出的结论','',
        '独立且失败吸收的控制组支持乘积在本轮给定人群、正确率及容差内的适用性；它们本身按已知机制构造，只是正控制，不是现实领域验证。',
        '共享难度与重复算子产生相关错误，固定边际率相乘低估；互斥错误使乘积高估。独立翻转仍可能因错误抵消而恢复正确，表明“误差独立”也不能单独保证最终正确率等于乘积。',
        '可信参考核对器在正确输入上正确率为1，但对错误输入有修复作用；忽略这一状态转换会漏算恢复路径。',
        '链式法则的条件率乘积可以复算“全过程每个中间输出正确”的频率，但不是“最终输出正确”的独立预测，且不应复用评估集拟合条件率后再宣称验证通过。',
        '不能把本轮模型反例解释为原论文必然错误。若原文精度指连续误差或其他指标，二元正确率的受控结论不能直接搬用。软件公式及版本保持原样，真实领域有效性继续pending。','',
        '## 证据与复现','',
        '[冻结协议](protocol.json)、[全部分组/种子统计](results.json)、[95份CSV清单](raw-inventory.json)、[独立标签重放](independent-label-audit.json)、[测试日志](tests.log)及[摘要清单](manifest.json)。',
        'raw目录保存全部压缩CSV，每行包含真实标签、故障日志、影子探针输出与实际串行输出；没有只保留平均误差。每环节样本量、置信区间、配对错误列联表、零方差说明、恢复次数、条件样本覆盖均在results.json。',
        '置信包络按各场景分别解释，不作跨19场景同时95%覆盖声明；Wilson近似区间不是必然包含理论概率的保证。解析理论值用于说明已声明机制，没有用它生成最终成功标签。','']
    (output/'README.md').write_text('\n'.join(lines),encoding='utf-8')
    source_names=['workflow_tools/accuracy_validation.py','workflow_tools/validate_accuracy_model.py',
        'workflow_tools/audit_accuracy_data.py','workflow_tools/report_accuracy_validation.py','tests/test_accuracy_validation.py',
        'task_flow/joint_candidates.py','docs/task-flow/joint-optimization/stage3-accuracy/protocol.md',
        'docs/task-flow/joint-optimization/stage3-accuracy/README.md']
    paths=[p for p in output.rglob('*') if p.is_file()]+[ROOT/name for name in source_names]
    save(output/'manifest.json',dict(version='tfdo-accuracy-evidence-1.0',files=[dict(path=str(p.relative_to(ROOT)),
        sha256=hashlib.sha256(p.read_bytes()).hexdigest(),sha256_lf_normalized=hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()) for p in paths]))
    print(json.dumps(dict(cases=19,raw_files=95,rows=sum(r['rows_checked'] for r in audited),decisions=decisions,output=str(output)),ensure_ascii=False))


if __name__=='__main__':main()
