"""Controlled Windows joint-pipeline baseline; no production algorithm changes."""
import argparse
from contextlib import ExitStack
from copy import deepcopy
import ctypes
from ctypes import wintypes
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from workflow_tools.verify_joint_optimization import inputs, ROOT

VERSION='tfdo-benchmark-1.0'


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def cases():
    return ([dict(id=f'P1-flows-{n}',group='P1',value=n) for n in (1,2,4)] +
            [dict(id=f'P2-artifacts-{n}',group='P2',value=n) for n in (1,2,3)] +
            [dict(id=f'P3-classes-{n}',group='P3',value=n) for n in (0,32,128)] +
            [dict(id=f'P4-tasks-{n}',group='P4',value=n) for n in (1,3,5)] +
            [dict(id='P4-capacity-1',group='P4_conflict',value=1)] +
            [dict(id=f'P5-{kind}-{n}',group='P5',budget=kind,value=n)
             for kind,n in [('flow',1),('analysis',1),('chain',1),('network',1),('network',8)]])


def build(case):
    jobs,scene,mapping=inputs();kwargs={}
    group,n=case['group'],case['value']
    if group in ('P1','P2','P3','P4','P4_conflict'):
        count=n if group=='P4' else 3 if group=='P4_conflict' else 1
        job,task=jobs[0],scene['tasks'][0]
        contexts=[c for c in scene['contexts'] if c['task']==task['id']]
        jobs=[];scene['tasks']=[];scene['contexts']=[]
        for i in range(count):
            tid=f'T{i+1}'
            jobs.append(dict(deepcopy(job),task_id=tid))
            scene['tasks'].append(dict(deepcopy(task),id=tid))
            scene['contexts'].extend(dict(deepcopy(c),task=tid) for c in contexts)
    if group=='P1':
        next(f for f in jobs[0]['request']['static_facts'] if f['fact']['predicate']=='needs_analysis')['value']=True
        cat=jobs[0]['catalog']
        original=next(a for a in cat['activities'] if a['id']=='analyze_secondary')
        entry=next(e for e in mapping['entries'] if e['activity_id']=='analyze_secondary')
        cat['activities']=[a for a in cat['activities'] if not a['id'].startswith('analyze_')]
        mapping['entries']=[e for e in mapping['entries'] if not e['activity_id'].startswith('analyze_')]
        for i in range(n):
            aid=f'analyze_variant_{i+1}'
            cat['activities'].append(dict(deepcopy(original),id=aid))
            mapping['entries'].append(dict(deepcopy(entry),activity_id=aid))
        from task_flow.contracts import canonical_sha256
        jobs[0]['request']['catalog_sha256']=canonical_sha256(cat)
    if group=='P2' and n>1:
        arts,contexts,links=deepcopy(scene['artifacts']),deepcopy(scene['contexts']),deepcopy(scene['links'])
        ids={a['id']:[a['id']]+[a['id']+f'-copy-{i}' for i in range(1,n)] for a in arts}
        scene['artifacts']=[dict(deepcopy(a),id=aid) for a in arts for aid in ids[a['id']]]
        scene['contexts']=[dict(deepcopy(c),artifact=aid) for c in contexts for aid in ids[c['artifact']]]
        scene['links']=[dict(deepcopy(e),**{'from':a,'to':b}) for e in links
                        for a in ids[e['from']] for b in ids[e['to']]]
    if group=='P3' and n:
        path=ROOT/'knowledge/benchmark-stage2'/f'hierarchy-{n}.ttl'
        lines=['@prefix b: <urn:tfdo-benchmark:> .','@prefix owl: <http://www.w3.org/2002/07/owl#> .',
               '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .']
        for i in range(n):
            lines.append(f'b:C{i} a owl:Class'+(f'; rdfs:subClassOf b:C{i-1}' if i else '')+' .')
        lines.append(f'b:sample a owl:NamedIndividual, b:C{n-1} .')
        content='\n'.join(lines)+'\n'
        if path.exists() and path.read_text(encoding='utf-8')!=content:
            raise ValueError('refusing to overwrite different benchmark ontology')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding='utf-8')
        scene['knowledge']['ontology_files'].append(f'benchmark-stage2/hierarchy-{n}.ttl')
    if group=='P4_conflict':
        for a in scene['artifacts']:
            if not a['consumable']:a['capacity']=n
    if group=='P5':
        if case['budget']=='flow':kwargs['max_flows_per_task']=n
        elif case['budget']=='analysis':kwargs['max_flow_analyses']=n
        elif case['budget']=='chain':scene['policies']['max_chain_combinations']=n
        else:scene['policies']['max_network_search_nodes']=n
    return jobs,scene,mapping,kwargs


class Memory:
    """20ms sampled working sets: Python plus subprocesses launched in worker."""
    def __init__(self):
        self.pids={os.getpid()};self.peak=0;self.python_peak=0;self.samples=0
        self.stop=threading.Event();self.active=[]
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.psapi=ctypes.WinDLL('psapi',use_last_error=True)
        self.api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        self.api.OpenProcess.restype=wintypes.HANDLE
        self.api.CloseHandle.argtypes=[wintypes.HANDLE]
        class ProcessEntry(ctypes.Structure):
            _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),('th32ProcessID',wintypes.DWORD),
                ('th32DefaultHeapID',ctypes.c_size_t),('th32ModuleID',wintypes.DWORD),('cntThreads',wintypes.DWORD),
                ('th32ParentProcessID',wintypes.DWORD),('pcPriClassBase',wintypes.LONG),
                ('dwFlags',wintypes.DWORD),('szExeFile',wintypes.WCHAR*260)]
        self.ProcessEntry=ProcessEntry;self.discovered={};self.peaks={}
        self.api.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
        self.api.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
        for name in ('Process32FirstW','Process32NextW'):
            getattr(self.api,name).argtypes=[wintypes.HANDLE,ctypes.POINTER(ProcessEntry)]
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(x,ctypes.c_size_t) for x in
                ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
                 'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
        self.Counters=Counters
        self.psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    def rss(self,pid):
        handle=self.api.OpenProcess(0x410,False,pid)
        if not handle:return None
        try:
            c=self.Counters();c.cb=ctypes.sizeof(c)
            return c.WorkingSetSize if self.psapi.GetProcessMemoryInfo(handle,ctypes.byref(c),c.cb) else None
        finally:self.api.CloseHandle(handle)
    def sample(self):
        snapshot=self.api.CreateToolhelp32Snapshot(2,0)
        if snapshot and snapshot!=ctypes.c_void_p(-1).value:
            try:
                entry=self.ProcessEntry();entry.dwSize=ctypes.sizeof(entry);entries=[]
                valid=self.api.Process32FirstW(snapshot,ctypes.byref(entry))
                while valid:
                    entries.append((entry.th32ProcessID,entry.th32ParentProcessID,entry.szExeFile))
                    valid=self.api.Process32NextW(snapshot,ctypes.byref(entry))
                for _ in range(4):
                    for pid,parent,name in entries:
                        if parent in self.pids:
                            self.pids.add(pid);self.discovered[pid]=dict(pid=pid,parent=parent,name=name)
            finally:self.api.CloseHandle(snapshot)
        own=self.rss(os.getpid())
        values=[]
        for pid in list(self.pids):
            value=self.rss(pid);values.append(value)
            if value is not None:self.peaks[pid]=max(self.peaks.get(pid,0),value)
        total=sum(v for v in values if v is not None)
        self.peak=max(self.peak,total)
        for observation in list(self.active):
            observation['sampled_process_tree_peak_rss_bytes']=max(observation.get('sampled_process_tree_peak_rss_bytes',0),total)
        if own is not None:self.python_peak=max(self.python_peak,own)
        self.samples+=1
        return own
    def loop(self):
        while not self.stop.wait(.02):self.sample()


def worker(config_path,out):
    if os.name!='nt':raise RuntimeError('baseline memory sampler currently requires Windows')
    config=json.loads(config_path.read_text(encoding='utf-8'))
    jobs,scene,mapping,kwargs=config['inputs']
    import task_flow.joint as joint
    import task_flow.joint_verify as verifier
    import adapters.owl_infer as owl
    from task_flow.planner import SearchSession
    from tests.joint_reference import audit_joint
    metrics={};memory=Memory();processes=[]
    popen=subprocess.Popen
    def launch(*args,**kw):
        p=popen(*args,**kw);memory.pids.add(p.pid)
        processes.append(dict(pid=p.pid,command=str(args[0] if args else kw.get('args'))))
        return p
    def timed(name,fn):
        def call(*args,**kw):
            observation={};memory.active.append(observation)
            before=memory.sample();start=time.perf_counter()
            try:return fn(*args,**kw)
            finally:
                elapsed=time.perf_counter()-start;after=memory.sample()
                assert memory.active.pop() is observation
                row=metrics.setdefault(name,dict(calls=0,seconds=0.,observations=[]))
                row['calls']+=1;row['seconds']+=elapsed
                row['observations'].append(dict(observation,seconds=elapsed,rss_before=before,rss_after=after,
                    rss_delta=None if before is None or after is None else after-before))
        return call
    thread=threading.Thread(target=memory.loop,daemon=True);thread.start()
    start=time.perf_counter()
    try:
        with ExitStack() as stack:
            stack.enter_context(patch('subprocess.Popen',launch))
            for module,attr,label in [(joint,'generate_candidates','chain_generation_inclusive'),
                (joint,'verify_candidate','flow_replay'),(joint,'compile_tuple','flow_compile'),
                (joint,'evaluate','normalization'),(joint,'optimize','network_optimization'),
                (verifier,'verify_joint','independent_verification'),(owl,'infer_owl','owl_inference'),
                (SearchSession,'run','flow_search')]:
                stack.enter_context(patch.object(module,attr,timed(label,getattr(module,attr))))
            report=joint.run_joint(jobs,scene,mapping,**kwargs)
        elapsed=time.perf_counter()-start
    finally:
        memory.stop.set();thread.join();memory.sample()
    # Independent oracle is excluded from the measured production pipeline.
    oracle=None
    if report['global_optimality']=='bounded_global_optimum':oracle=audit_joint(report)
    stage={**metrics,'memory':dict(sample_interval_ms=20,samples=memory.samples,
        sampled_python_peak_rss_bytes=memory.python_peak,sampled_python_plus_children_peak_rss_bytes=memory.peak,
        scope='sum of sampled working sets; shared pages may be double counted; not a true simultaneous OS peak',
        tracked_subprocess_count=len(processes))}
    stage['memory']['discovered_descendants']=[dict(row,sampled_peak_rss_bytes=memory.peaks.get(pid))
                                               for pid,row in memory.discovered.items()]
    owl_results=[b['report']['owl_reasoning'] for b in report['generation_batches'] if b['status']=='generated']
    stage['owl_result_sizes']=[dict(facts=len(r['facts']),subjects=len(r['types']),
        types=sum(len(v) for v in r['types'].values())) for r in owl_results]
    stage['flow_counts']=[dict(task_id=p['task_id'],statistics=p['search']['statistics'],
        status=p['search']['search_status'],reason=p['search']['termination_reason']) for p in report['planning']]
    stage['chain_counts']=dict(generated=report['generated_chain_count'],unique=report['unique_chain_count'],
        duplicates=report['generated_chain_count']-report['unique_chain_count'],batches=len(report['generation_batches']),
        per_task=[dict(task=t['task'],generation=t['sequence_generation'],chains=len(t['chains']))
                  for b in report['generation_batches'] if b['status']=='generated' for t in b['report']['tasks']])
    stage['network_counts']=report.get('network_search')
    correctness=dict(integration_status=report['integration_status'],global_optimality=report['global_optimality'],
        flow_search_complete=report['flow_search_complete'],chain_generation_complete=report['chain_generation_complete'],
        network_search_complete=report.get('network_search',{}).get('search_complete'),
        independent_verification=report.get('verification',{}).get('status'),oracle=oracle,
        termination_reasons=report['termination_reasons'],termination_reason=report.get('termination_reason'),
        feasible_solution=bool(report['selected_candidate_ids']),domain_validity='pending')
    semantic=dict(candidate_set_hash=report['candidate_set_hash'],selected=report['selected_candidate_ids'],
        evaluation=report.get('evaluation'),correctness=correctness)
    save(out/'joint_report.json',report)
    save(out/'performance.json',dict(benchmark_version=VERSION,case=config['case'],
        input_hash=config['input_hash'],stage_metrics=stage,correctness=correctness,
        semantic_hash=digest(semantic),pipeline_seconds=elapsed,processes=processes,
        output_report_bytes=(out/'joint_report.json').stat().st_size,
        timing_scope='run_joint only; inclusive stages overlap; excludes imports, oracle and output serialization',
        startup='fresh Python worker and fresh HermiT JVM per inference; OS filesystem cache uncontrolled'))


def environment(hardware=None):
    def command(args):
        r=subprocess.run(args,capture_output=True,text=True,errors='replace');return (r.stdout+r.stderr).strip()
    hermit=list((Path(importlib.metadata.distribution('owlready2').locate_file('owlready2'))/'hermit').glob('*.jar'))
    return dict(python=sys.version,executable=sys.executable,platform=platform.platform(),machine=platform.machine(),
        processor=platform.processor(),java=command(['java','-version']),git_revision=command(['git','rev-parse','HEAD']),
        cpu_and_memory=hardware if hardware else command(['powershell','-NoProfile','-Command',
          'Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json -Compress; (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory']),
        dependencies={d.metadata['Name']:d.version for d in importlib.metadata.distributions()},
        hermit_jars={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in hermit},
        source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
            for folder in ('task_flow','design','adapters') for p in (ROOT/folder).glob('*.py')},
        harness_sha256_lf=hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n',b'\n')).hexdigest())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--repetitions',type=int,default=5)
    parser.add_argument('--case',action='append')
    parser.add_argument('--worker',type=Path)
    parser.add_argument('--timeout-s',type=float,default=180)
    parser.add_argument('--hardware-json',type=Path)
    args=parser.parse_args(argv)
    if args.worker:return worker(args.worker,args.output_dir)
    if args.repetitions<5:raise ValueError('baseline requires at least five repetitions')
    if args.output_dir.exists():raise ValueError('choose a new output directory')
    selected=[c for c in cases() if not args.case or c['id'] in args.case]
    if not selected or (args.case and set(args.case)-{c['id'] for c in selected}):raise ValueError('unknown case')
    args.output_dir.mkdir(parents=True)
    hardware=json.loads(args.hardware_json.read_text(encoding='utf-8-sig')) if args.hardware_json else None
    save(args.output_dir/'environment.json',environment(hardware))
    configurations=[]
    for case in selected:
        data=build(case)
        files={name:hashlib.sha256((ROOT/'knowledge'/name).read_bytes()).hexdigest()
            for name in data[1]['knowledge']['ontology_files']+[data[1]['knowledge']['rules_file']]}
        config=dict(case=case,inputs=data,knowledge_hashes=files,input_hash=digest(dict(inputs=data,knowledge=files)))
        path=args.output_dir/case['id']/'input.json';save(path,config);configurations.append((case,path))
    # All input definitions and experiment conditions frozen before measurement.
    save(args.output_dir/'protocol.json',dict(benchmark_version=VERSION,cases=selected,repetitions=args.repetitions,
         timeout_s=args.timeout_s,memory_interval_ms=20,optimization_changes=False))
    rows=[]
    for case,path in configurations:
        records=[]
        for i in range(args.repetitions):
            out=args.output_dir/case['id']/f'run-{i+1}';out.mkdir()
            started=time.perf_counter()
            with (out/'worker.log').open('w',encoding='utf-8') as log:
                process=subprocess.Popen([sys.executable,'-B','-m',__name__ if __name__!='__main__' else 'workflow_tools.benchmark_joint',
                    '--worker',str(path.resolve()),'--output-dir',str(out.resolve())],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
                try:code=process.wait(timeout=args.timeout_s);status='completed' if code==0 else 'failed'
                except subprocess.TimeoutExpired:
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
                    process.wait();code=None;status='timeout'
            record=dict(repetition=i+1,status=status,exit_code=code,worker_wall_seconds=time.perf_counter()-started,
                        pipeline_seconds=None,stage_metrics=None,correctness=None)
            if status=='completed':record.update(json.loads((out/'performance.json').read_text(encoding='utf-8')))
            if status=='completed' and case['group']=='P1':
                actual=sum(p['statistics']['candidate_count'] for p in record['stage_metrics']['flow_counts'])
                if actual!=case['value']:raise AssertionError('P1 experimental axis did not change actual flow count')
            save(out/'run.json',record);records.append(record)
            print(case['id'],i+1,status,round(record['worker_wall_seconds'],3),flush=True)
        complete=[r for r in records if r['status']=='completed']
        times=[r['pipeline_seconds'] for r in complete]
        row=dict(case=case,repetitions=records,summary=dict(completed=len(complete),
            semantic_repeatable=len({r['semantic_hash'] for r in complete})==1 if len(complete)==len(records) else None,
            median_runtime=statistics.median(times) if times else None,min_runtime=min(times) if times else None,
            max_runtime=max(times) if times else None,
            sampled_peak_memory=max((r['stage_metrics']['memory']['sampled_python_plus_children_peak_rss_bytes'] for r in complete),default=None)))
        rows.append(row);save(args.output_dir/'benchmark.json',dict(benchmark_version=VERSION,cases=rows,domain_validity='pending'))
    if any(r['status']!='completed' for row in rows for r in row['repetitions']):raise RuntimeError('some runs failed or timed out; see raw records')
    if not all(row['summary']['semantic_repeatable'] for row in rows):raise RuntimeError('semantic repeatability failed')


if __name__=='__main__':main()
