"""Phase 2B: production-path timings and independently checked Java API probes."""
import argparse
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

from rdflib.compare import to_isomorphic
from workflow_tools.benchmark_joint import ROOT,build,cases,save,digest,environment
from workflow_tools.owl_profile_worker import Timings
from workflow_tools.audit_joint_implementation import semantics

VERSION='tfdo-owl-path-1.0'
MATRIX=[('base','P4-tasks-1'),('flows-1','P1-flows-1'),('flows-4','P1-flows-4'),
        ('artifacts-3','P2-artifacts-3'),('classes-32','P3-classes-32'),('classes-128','P3-classes-128'),
        ('one-seal','P4-tasks-1')]


def owl_semantics(result):
    return dict(status=result['status'],types=result.get('types'),facts=sorted(map(list,result.get('facts',[]))))


def pipeline_worker(config_path,out):
    import adapters.owl_infer as owl
    import task_flow.joint as joint
    import task_flow.joint_verify as verifier
    from task_flow.planner import SearchSession
    config=json.loads(config_path.read_text(encoding='utf-8'));data=config['inputs']
    timer=Timings();batches=[];raw_run=subprocess.run;raw_infer=owl.infer_owl
    def launch(command,*args,**kwargs):
        if isinstance(command,list) and len(command)>3 and command[2:4]==['-m','adapters.owl_infer']:
            command=list(command);command[3]='workflow_tools.owl_profile_worker'
        return raw_run(command,*args,**kwargs)
    def infer(graph,subjects,predicates,**kwargs):
        child=[]
        def dispatch(command,*args,**kw):
            response=launch(command,*args,**kw)
            directory=Path(command[-1])
            if (directory/'profile.json').exists():child.append(json.loads((directory/'profile.json').read_text(encoding='utf-8')))
            return response
        started=time.perf_counter()
        with patch('subprocess.run',dispatch):result=raw_infer(graph,subjects,predicates,observer=timer,**kwargs)
        batches.append(dict(seconds=time.perf_counter()-started,input_triples=len(graph),subjects=len(subjects),
            input_graph_digest=str(to_isomorphic(graph).graph_digest()),result_hash=digest(owl_semantics(result)),
            child=child))
        return result
    start=time.perf_counter()
    with ExitStack() as stack:
        stack.enter_context(patch.object(owl,'infer_owl',infer))
        for module,attr,label in [(joint,'generate_candidates','chain_generation_inclusive'),
            (joint,'optimize','network_optimization'),(joint,'verify_candidate','flow_replay'),
            (joint,'compile_tuple','flow_compile'),(SearchSession,'run','flow_search'),
            (verifier,'verify_joint','independent_verification')]:
            stack.enter_context(patch.object(module,attr,timer.wrap(label,getattr(module,attr))))
        report=joint.run_joint(*data[:3],**data[3])
    total=time.perf_counter()-start
    equivalent=digest(semantics(report))==config['reference_semantic_hash']
    if not equivalent:raise AssertionError('instrumented pipeline changed business semantics')
    if len(batches)!=1:raise AssertionError('expected exactly one actual OWL call')
    if batches[0]['input_graph_digest']!=config['input_graph_digest'] or batches[0]['result_hash']!=config['owl_result_hash']:
        raise AssertionError('OWL input/result drift')
    save(out,dict(pid=os.getpid(),pipeline_seconds=total,timings=timer.values,owl_batches=batches,
        candidate_set_hash=report['candidate_set_hash'],semantic_hash=digest(semantics(report)),
        configured_activities=len(data[0][0]['catalog']['activities']),flow_count=report['flow_count'],
        planning_statistics=[p['search']['statistics'] for p in report['planning']],
        actual_owl_calls=len(batches),unique_chains=report['unique_chain_count'],
        chain_generation=[t['sequence_generation'] for b in report['generation_batches'] for t in b['report']['tasks']],
        network=report['network_search'],verification=report['verification'],
        global_optimality=report['global_optimality'],business_semantics_equal=equivalent))


def prepare(output):
    import adapters.owl_infer as owl
    from task_flow.joint import run_joint
    from tests.joint_reference import audit_joint
    configs=[]
    for name,case_id in MATRIX:
        data=build(next(c for c in cases() if c['id']==case_id))
        if name=='one-seal':
            for artifact in data[1]['artifacts']:
                if artifact['id'].startswith('R-Seal-') and artifact['id']!='R-Seal-A':artifact['available']=False
        directory=output/name;directory.mkdir();captured=[];original=owl.infer_owl
        def capture(graph,subjects,predicates,**kwargs):
            result=original(graph,subjects,predicates,**kwargs)
            graph.serialize(directory/'input.rdf',format='xml')
            save(directory/'query.json',dict(subjects=subjects,predicates=predicates))
            save(directory/'expected-owl.json',result)
            (directory/'subjects.txt').write_text('\n'.join(subjects)+'\n',encoding='utf-8')
            (directory/'predicates.txt').write_text('\n'.join(predicates)+'\n',encoding='utf-8')
            captured.append((str(to_isomorphic(graph).graph_digest()),digest(owl_semantics(result)),len(graph)))
            return result
        with patch.object(owl,'infer_owl',capture):reference=run_joint(*data[:3],**data[3])
        oracle=audit_joint(reference)
        if len(captured)!=1:raise AssertionError('matrix must have a single OWL batch')
        config=dict(name=name,inputs=data,reference_semantic_hash=digest(semantics(reference)),
            input_graph_digest=captured[0][0],owl_result_hash=captured[0][1],input_triples=captured[0][2],
            unique_chains=reference['unique_chain_count'],flow_count=reference['flow_count'],oracle=oracle,
            knowledge_hash=reference['knowledge_snapshot_hash'])
        save(directory/'config.json',config);save(directory/'reference-joint.json',reference)
        configs.append(directory/'config.json');print('prepared',name,'chains',config['unique_chains'],flush=True)
    a=json.loads(configs[0].read_text(encoding='utf-8'));b=json.loads(configs[-1].read_text(encoding='utf-8'))
    if (a['input_graph_digest'],a['owl_result_hash'])!=(b['input_graph_digest'],b['owl_result_hash']):
        raise AssertionError('candidate-only contrast did not preserve exact OWL input and output')
    if (a['unique_chains'],b['unique_chains'])!=(4,1):raise AssertionError('candidate-only axis ineffective')
    return configs


def java_probe(classpath,directory,repeats):
    start=time.perf_counter()
    process=subprocess.Popen(['java','-Xmx2000M','-cp',classpath,'OwlTimingProbe',str(directory/'input.rdf'),
        str(directory/'subjects.txt'),str(directory/'predicates.txt'),str(repeats)],cwd=ROOT,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    created=time.perf_counter()-start
    ready=process.stdout.readline().strip();latency=time.perf_counter()-start
    if not ready.startswith('READY\t'):
        process.kill();_,error=process.communicate();raise RuntimeError('Java probe not ready: '+error)
    text,error=process.communicate(input='GO\n',timeout=120)
    if process.returncode:raise RuntimeError(error)
    rows=[dict(timings={},types={},facts=[]) for _ in range(repeats)]
    for line in text.splitlines():
        parts=line.split('\t');tag=parts[0]
        if tag not in ('M','T','F','END'):continue
        row=rows[int(parts[1])]
        if tag=='M':row['timings'][parts[2]]=int(parts[3])/1e9
        elif tag=='T':row['types'].setdefault(parts[2],[]).append(parts[3])
        elif tag=='F':row['facts'].append(parts[2:])
    expected=json.loads((directory/'expected-owl.json').read_text(encoding='utf-8'))
    query=json.loads((directory/'query.json').read_text(encoding='utf-8'))
    for row in rows:
        row['types']={s:sorted(row['types'].get(s,[])) for s in query['subjects']};row['facts'].sort()
        row['named_entailments_equal']=(row['types']==expected['types'] and row['facts']==sorted(map(list,expected['facts'])))
        if not row['named_entailments_equal']:
            save(directory/'java-mismatch.json',dict(actual=row,expected=expected))
            raise AssertionError('Java API named entailments differ from production CLI')
    return dict(pid=int(ready.split('\t')[1]),os_process_creation_seconds=created,
        launch_to_ready_seconds=latency,total_wall_seconds=time.perf_counter()-start,
        pure_jvm_boot_seconds=None,rows=rows,stderr=error,ontology_reused=False,reasoner_reused=False,
        jvm_reused_between_rows=repeats>1,note='API diagnostic; not a timing decomposition of original CLI internals')


def execute(output,repetitions,hardware,compiled=None):
    if output.exists():raise ValueError('choose a new output directory')
    output.mkdir(parents=True);configs=prepare(output)
    import owlready2.reasoning as reasoning
    if compiled is None:
        compiled=output/'compiled';compiled.mkdir()
        result=subprocess.run(['javac','-encoding','UTF-8','-cp',reasoning._HERMIT_CLASSPATH,'-d',str(compiled),
            str(ROOT/'workflow_tools/java/OwlTimingProbe.java')],capture_output=True,text=True,encoding='utf-8',errors='replace')
        if result.returncode or 'Exception' in result.stderr:raise RuntimeError('javac failed: '+result.stderr)
    compiled=compiled.resolve()
    classpath=str(compiled)+os.pathsep+reasoning._HERMIT_CLASSPATH
    source_paths=[ROOT/'adapters/owl_infer.py',ROOT/'workflow_tools/benchmark_owl_path.py',
        ROOT/'workflow_tools/owl_profile_worker.py',ROOT/'workflow_tools/java/OwlTimingProbe.java']
    save(output/'protocol.json',dict(version=VERSION,repetitions=repetitions,matrix=MATRIX,
        compiled_class_sha256=hashlib.sha256((compiled/'OwlTimingProbe.class').read_bytes()).hexdigest(),
        source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in source_paths},
        optimization_changes=False,pure_jvm_boot_seconds=None,
        startup_note='launch-to-READY includes class bootstrap and pipe delivery; not pure JVM boot',
        lifecycle='fresh world/ontology/reasoner each repetition, including same-process groups'))
    save(output/'environment.json',environment(json.loads(hardware.read_text(encoding='utf-8-sig'))))
    results=[]
    for config in configs:
        directory=config.parent;rows=[];java=[]
        for i in range(repetitions):
            out=directory/f'pipeline-{i+1}.json'
            start=time.perf_counter()
            result=subprocess.run([sys.executable,'-B','-m','workflow_tools.benchmark_owl_path',
                '--pipeline-worker',str(config),'--output-dir',str(out)],cwd=ROOT,capture_output=True,text=True,
                encoding='utf-8',errors='replace',timeout=180)
            if result.returncode:
                (directory/f'pipeline-{i+1}.log').write_text(result.stdout+result.stderr,encoding='utf-8')
                raise RuntimeError('instrumented pipeline failed: '+result.stderr)
            row=json.loads(out.read_text(encoding='utf-8'));row['worker_wall_seconds']=time.perf_counter()-start
            rows.append(row)
            java.append(java_probe(classpath,directory,1))
            print(directory.name,i+1,'pipeline and Java passed',flush=True)
        if len({row['semantic_hash'] for row in rows})!=1:raise AssertionError('pipeline repeatability failed')
        entry=dict(case=directory.name,pipeline=rows,java_cold=java,
                   configuration=json.loads(config.read_text(encoding='utf-8')))
        if directory.name in ('base','classes-128'):
            entry['java_same_process']=java_probe(classpath,directory,repetitions+1)
        if directory.name=='base':
            # Repeat production worker in one Python process; each call still creates a JVM.
            result=subprocess.run([sys.executable,'-B','-m','workflow_tools.owl_profile_worker',str(directory),str(repetitions+1)],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
            if result.returncode:raise RuntimeError(result.stderr)
            hot=json.loads((directory/'profile.json').read_text(encoding='utf-8'))
            for row in hot['rows']:
                if owl_semantics(row['result'])!=owl_semantics(json.loads((directory/'expected-owl.json').read_text(encoding='utf-8'))):
                    raise AssertionError('Python-repeat result drift')
            entry['python_same_process']=hot
        results.append(entry);save(output/'measurements.json',dict(version=VERSION,cases=results,domain_validity='pending'))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--pipeline-worker',type=Path)
    parser.add_argument('--repetitions',type=int,default=5)
    parser.add_argument('--hardware-json',type=Path)
    parser.add_argument('--compiled-dir',type=Path)
    args=parser.parse_args(argv)
    if args.pipeline_worker:return pipeline_worker(args.pipeline_worker.resolve(),args.output_dir.resolve())
    if args.repetitions<5:raise ValueError('at least five repetitions required')
    if not args.hardware_json:raise ValueError('supply a recorded CPU/memory JSON')
    execute(args.output_dir.resolve(),args.repetitions,args.hardware_json,args.compiled_dir)


if __name__=='__main__':main()
