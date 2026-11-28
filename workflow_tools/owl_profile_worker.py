"""Experimental timing wrapper around the unchanged production worker contract."""
from contextlib import ExitStack
import json
from pathlib import Path
import os
import subprocess
import sys
import time
from unittest.mock import patch


class Timings:
    def __init__(self):self.stack=[];self.values={}
    def __call__(self,event,name):
        if event=='start':self.stack.append((name,time.perf_counter()))
        else:
            label,start=self.stack.pop()
            if label!=name:raise AssertionError('unbalanced timing events')
            self.values.setdefault(name,[]).append(time.perf_counter()-start)
    def wrap(self,name,fn):
        def call(*args,**kwargs):
            self('start',name)
            try:return fn(*args,**kwargs)
            finally:self('end',name)
        return call


def main():
    directory=Path(sys.argv[1]);repeats=int(sys.argv[2]) if len(sys.argv)>2 else 1
    setup=Timings();setup('start','instrumentation_owlready_first_import')
    import owlready2
    import owlready2.reasoning as reasoning
    setup('end','instrumentation_owlready_first_import')
    from adapters.owl_infer import worker
    rows=[];processes=[];original_popen=subprocess.Popen
    for index in range(repeats):
        times=Timings()
        def launch(*args,**kwargs):
            start=time.perf_counter();p=original_popen(*args,**kwargs)
            processes.append(dict(repetition=index,pid=p.pid,parent_pid=os.getpid(),
                command=args[0] if args else kwargs.get('args'),
                os_process_creation_seconds=time.perf_counter()-start))
            return p
        started=time.perf_counter()
        with ExitStack() as stack:
            stack.enter_context(patch('subprocess.Popen',launch))
            for module,name,label in [(owlready2.World,'save','python_world_ntriples_serialize'),
                (reasoning.subprocess,'check_output','java_subprocess_inclusive'),
                (reasoning,'_apply_reasoning_results','python_apply_class_results'),
                (reasoning,'_apply_inferred_obj_relations','python_apply_relation_results')]:
                stack.enter_context(patch.object(module,name,times.wrap(label,getattr(module,name))))
            result=worker(directory,observer=times)
        rows.append(dict(repetition=index,pid=os.getpid(),seconds=time.perf_counter()-started,
            timings=times.values,result=result,startup='cold_python' if index==0 else 'same_python_process',
            world_reused=False,jvm_reused=False,reasoner_reused=False))
    (directory/'profile.json').write_text(json.dumps(dict(setup_timings=setup.values,rows=rows,
        processes=processes),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main()
