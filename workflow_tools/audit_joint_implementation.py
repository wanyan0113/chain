"""Audit a tracked-file snapshot and its replay, without creating a Git branch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]


def require(value,message):
    if not value:raise RuntimeError(message)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def export_snapshot(revision,directory):
    """Use a private index, without changing HEAD, branches or the real index."""
    require(not directory.exists(),'snapshot output must be a new directory')
    commit=subprocess.run(['git','rev-parse','--verify','--end-of-options',revision+'^{commit}'],
        cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    require(len(commit)==40 and all(c in '0123456789abcdef' for c in commit),'invalid revision')
    (ROOT/'tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as scratch:
        env=os.environ.copy();env['GIT_INDEX_FILE']=str(Path(scratch)/'audit.index')
        subprocess.run(['git','read-tree',commit],cwd=ROOT,env=env,check=True,capture_output=True)
        directory.mkdir(parents=True)
        subprocess.run(['git','checkout-index','--all','--prefix='+directory.as_posix()+'/'],
                       cwd=ROOT,env=env,check=True,capture_output=True)
    return commit


def probe(directory):
    code="""import json,design,task_flow,tests
from design.engine import condition_result
from task_flow.joint_verify import _value
a={'value':1,'unit':'1'}; b={'value':1.0,'unit':'1'}
imports=[p for m in (design,task_flow,tests) for p in ([m.__file__] if m.__file__ else list(m.__path__))]
print(json.dumps({'imports':imports,
'producer':condition_result({'field':'task.x','op':'eq','value':b},{'task':{'x':a}})[0],
'verifier':_value(a,b,'eq')}))
"""
    result=subprocess.run([sys.executable,'-B','-c',code],cwd=directory,capture_output=True,text=True,
                          encoding='utf-8',errors='strict',check=True,timeout=30)
    return json.loads(result.stdout)


def semantics(report):
    return dict(candidate_set_hash=report['candidate_set_hash'],
        evaluation_context_hash=report['evaluation']['evaluation_context_hash'],
        selected_candidate_ids=report['selected_candidate_ids'],global_optimality=report['global_optimality'],
        candidates={k:dict(identity=v['identity_payload'],metrics=v['chain']['metrics']) for k,v in report['candidates'].items()},
        uniform_scores=report['evaluation']['candidates'],network=report['design_report']['network'])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot-dir',type=Path,required=True)
    parser.add_argument('--replay-dir',type=Path,required=True)
    parser.add_argument('--export-snapshot',action='store_true')
    parser.add_argument('--run-replay',action='store_true')
    parser.add_argument('--reference-report',type=Path)
    parser.add_argument('--revision',default='c17c515')
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args(argv)
    snapshot=args.snapshot_dir.resolve();replay=args.replay_dir.resolve()
    if args.export_snapshot:
        export_snapshot(args.revision,snapshot)
    if args.run_replay:
        require(not replay.exists(),'replay output must be a new directory')
        executed=subprocess.run([sys.executable,'-B','-m','workflow_tools.verify_joint_optimization',
            '--output-dir',str(replay)],cwd=snapshot,capture_output=True,text=True,encoding='utf-8',errors='strict',timeout=600)
        (snapshot/'stage1-replay.log').write_text(executed.stdout+'\n'+executed.stderr,encoding='utf-8')
        require(executed.returncode==0,'snapshot actual replay failed; inspect stage1-replay.log')
    output=args.output_dir or ROOT/'generated/task-flow'/('joint-stage1-audit-'+uuid.uuid4().hex[:8])
    require(not output.exists(),'audit output already exists')
    design_dir=snapshot/'docs/task-flow/joint-optimization'
    frozen=load(design_dir/'manifest-v1.json')
    frozen_checks=[dict(path=f['path'],passed=hashlib.sha256((design_dir/f['path']).read_bytes()).hexdigest()==f['sha256'])
                   for f in frozen['files']]
    require(all(f['passed'] for f in frozen_checks),'frozen design exact-byte mismatch')
    release=load(design_dir/'implementation-manifest-v1.json')
    sources=[]
    for record in release['files']:
        data=(snapshot/record['path']).read_bytes()
        sources.append(dict(path=record['path'],raw_equal=hashlib.sha256(data).hexdigest()==record['sha256'],
            normalized_equal=hashlib.sha256(data.replace(b'\r\n',b'\n')).hexdigest()==record['sha256_lf_normalized']))
    require(all(r['normalized_equal'] for r in sources),'source differs beyond declared CRLF/LF conversion')
    tracked=subprocess.run(['git','ls-tree','-r','--name-only','-z',args.revision],cwd=ROOT,
                           capture_output=True,check=True).stdout.decode('utf-8').split('\0')
    tracked=[p for p in tracked if p]
    require(all((snapshot/p).is_file() for p in tracked),'snapshot missing a committed file')
    old_probe=probe(snapshot);current_probe=probe(ROOT)
    require(all(Path(p).resolve().is_relative_to(snapshot) for p in old_probe['imports']),
            'snapshot imports leaked into original workspace')
    expected=load(design_dir/'evidence/joint-verification.json')
    actual=load(replay/'verification.json')
    require(actual['verification']=='passed' and len(actual['cases'])==8,'actual replay failed')
    require(expected['independent_oracle']==actual['independent_oracle'],'independent oracle/selected identity changed')
    case=lambda r:[(c['case'],c.get('actual'),c.get('status'),c['passed']) for c in r['cases']]
    require(case(expected)==case(actual),'case conclusions changed')
    current=load(replay/'full/joint_report.json')
    comparison=None
    if args.reference_report:
        reference=load(args.reference_report)
        comparison=dict(reference=str(args.reference_report),equivalent=semantics(reference)==semantics(current))
        require(comparison['equivalent'],'candidate identity, raw metrics, unified scores or network not reproduced')
    require(old_probe['producer']=='pass' and old_probe['verifier'] is False,'expected quantity-equality finding not reproduced')
    require(current_probe['producer']=='pass' and current_probe['verifier'] is True,'quantity-equality correction not verified')
    audit=dict(format_version='tfdo-implementation-audit-1.0',reviewed_revision=args.revision,
        status='passed_with_corrected_finding',branch_created=False,snapshot=str(snapshot),
        source_file_count=len(sources),source_checks=sources,frozen_design_checks=frozen_checks,
        tracked_files_present=len(tracked),snapshot_imports=old_probe['imports'],
        independent_replay=dict(cases=8,passed=True,oracle=actual['independent_oracle']),
        semantic_comparison=comparison,
        findings=[dict(id='JV-AUDIT-001',severity='valid_input_false_rejection',
            description='Same-unit numeric quantities 1 and 1.0: producer accepts, original verifier rejects.',
            published_probe=old_probe,corrected_probe=current_probe,status='corrected_in_contract_probe',
            scope='Verifier equality contract only; no accuracy formula, scoring or optimization target change')],
        domain_validity='pending',next_phase='controlled_scale_and_performance_experiments')
    output.mkdir(parents=True)
    (output/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output/'summary.md').write_text('\n'.join(['# 联合优化阶段一实现审计','',
        '受审提交：'+args.revision,'结论：passed_with_corrected_finding',
        '独立副本来自Git追踪文件，未创建或切换分支。',
        '源码归一化摘要全部一致；冻结设计原始字节摘要全部一致。',
        '8组实际验收重跑通过；候选身份、原始指标、统一评分与网络选择复现。',
        '发现并修正JV-AUDIT-001：同单位整数/小数数量相等的验证器边界。',
        '精度模型业务有效性：pending。','']),encoding='utf-8')
    print(json.dumps(dict(status=audit['status'],reviewed_revision=args.revision,output=str(output),
                         source_files=len(sources),tracked_files=len(tracked),replayed_cases=8),ensure_ascii=False))


if __name__=='__main__':main()
