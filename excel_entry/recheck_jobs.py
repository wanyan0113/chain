"""One asynchronous full-review subprocess, with server-selected inputs and outputs."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from fdna.evidence_review import TASKS,read


def now():return datetime.now(timezone.utc).isoformat()


class RecheckError(ValueError):
    def __init__(self,status,message):super().__init__(message);self.status=status


class RecheckJobs:
    def __init__(self,repository,project_root=None,*,popen_factory=None):
        self.repository=Path(repository).resolve()
        self.project_root=Path(project_root).resolve() if project_root is not None else None
        self._factory=popen_factory or subprocess.Popen
        self._lock=threading.RLock();self._closing=threading.Event();self._thread=None;self._process=None;self._job_dir=None
        self._state=dict(enabled=self.project_root is not None,status='idle',id=None,tasks=[],completed_tasks=0,total_tasks=len(TASKS))
        if self.project_root is not None:
            if not self.project_root.is_dir():raise ValueError('完整复核来源根目录不存在')
            self._base=(self.project_root/'generated/关于优化/15_网页完整复核/运行记录').resolve()
            protected=[(self.repository/p).resolve() for p in ('docs','fdna','data','tests','workflow_tools','.git')]
            protected.extend((self.project_root/t[3]).resolve() for t in TASKS)
            if not self._base.is_relative_to(self.project_root) or any(self._base==p or self._base.is_relative_to(p) for p in protected):
                raise ValueError('网页复核输出不能位于代码、发布成果或旧来源包内')

    def snapshot(self):
        with self._lock:
            result=deepcopy(self._state)
            if not result['enabled']:result['message']='未配置完整复核来源；启动服务时设置 --review-project-root。'
            return result

    def _save(self):
        if self._job_dir is None:return
        temporary=self._job_dir/'job_status.tmp'
        temporary.write_text(json.dumps(self._state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
        temporary.replace(self._job_dir/'job_status.json')

    def start(self):
        with self._lock:
            if not self._state['enabled']:raise RecheckError(409,'服务器尚未配置完整复核来源，请使用 --review-project-root 启动。')
            if self._closing.is_set():raise RecheckError(409,'服务正在关闭，不能启动新任务。')
            if self._state['status']=='running':return dict(self.snapshot(),reused_running=True)
            ident=uuid.uuid4().hex;self._job_dir=self._base/ident;self._job_dir.mkdir(parents=True,exist_ok=False)
            self._state=dict(enabled=True,status='running',id=ident,started_at_utc=now(),finished_at_utc=None,
                completed_tasks=0,total_tasks=len(TASKS),tasks=[dict(id=t[0],title=t[1],status='pending',verifier_invoked=False) for t in TASKS],
                numerical_rechecks_completed=False,performance_remeasured=False,error=None,report_url=None,json_url=None)
            try:self._save()
            except OSError as exc:
                self._state.update(status='failed',finished_at_utc=now(),error='无法保存初始任务记录，未启动独立复核。')
                raise RecheckError(409,self._state['error']) from exc
            self._thread=threading.Thread(target=self._work,args=(ident,self._job_dir),daemon=True)
            self._thread.start();return dict(self.snapshot(),reused_running=False)

    def _progress(self,event):
        with self._lock:
            task=next((r for r in self._state['tasks'] if r['id']==event.get('task')),None)
            if task is None:return
            if event.get('stage')=='started':task.update(status='running',verifier_invoked=None)
            elif event.get('stage')=='finished' and event.get('status') in ('passed','failed'):
                task.update(status=event['status'],verifier_invoked=event.get('verifier_invoked'))
            self._state['completed_tasks']=sum(t['status'] in ('passed','failed') for t in self._state['tasks'])
            self._save()

    @staticmethod
    def _read_report(directory):
        manifest=read(directory/'manifest.json')
        expected={'review_report.json','成果复核与阅读入口.md','目录版本.json'}
        if not isinstance(manifest,dict) or set(manifest)!=expected:raise ValueError('复核产物清单不完整')
        for name,value in manifest.items():
            path=directory/name
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=value:raise ValueError('复核产物摘要不一致: '+name)
        report=read(directory/'review_report.json')
        if not isinstance(report,dict) or not isinstance(report.get('full_checks'),list) or any(not isinstance(r,dict) for r in report['full_checks']):
            raise ValueError('复核报告或任务记录格式错误')
        if report.get('mode')!='full' or [r['id'] for r in report.get('full_checks',[])]!=[t[0] for t in TASKS]:raise ValueError('缺少完整模式的十项任务记录')
        if report.get('status') not in ('passed','failed'):raise ValueError('复核未返回最终状态')
        if report['status']=='passed' and (report.get('failed_checks')!=0 or not report.get('numerical_rechecks_completed') or report.get('full_verifiers_invoked')!=len(TASKS)
                or any(r.get('status')!='passed' or not r.get('verifier_invoked') for r in report['full_checks'])):
            raise ValueError('通过状态与数值执行记录不一致')
        return report

    def _work(self,ident,directory):
        report_dir=directory/'report';process=None
        try:
            command=[sys.executable,'-B','-m','fdna','review-results','--mode','full','--project-root',str(self.project_root),'--output-dir',str(report_dir)]
            environment=dict(os.environ,PYTHONIOENCODING='utf-8')
            process=self._factory(command,cwd=self.repository,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                text=True,encoding='utf-8',bufsize=1,env=environment)
            with self._lock:
                self._process=process
                if self._closing.is_set():process.terminate()
            with (directory/'worker.log').open('w',encoding='utf-8',newline='\n') as log:
                for line in process.stdout:
                    log.write(line);log.flush()
                    try:event=json.loads(line)
                    except (ValueError,TypeError):continue
                    if isinstance(event,dict) and event.get('stage') in ('started','finished'):self._progress(event)
            code=process.wait()
            if self._closing.is_set():raise RuntimeError('服务关闭，复核任务已中断。')
            if code not in (0,1):raise RuntimeError('完整复核未完成，请检查配置的来源目录、FDNA依赖及任务日志。')
            report=self._read_report(report_dir)
            if (code==0)!=(report['status']=='passed'):raise ValueError('退出码与最终复核状态不一致')
            with self._lock:
                self._state.update(status=report['status'],finished_at_utc=now(),completed_tasks=len(TASKS),
                    tasks=[dict(id=r['id'],title=r['title'],status=r['status'],verifier_invoked=r['verifier_invoked'],error=r.get('error')) for r in report['full_checks']],
                    failed_checks=report['failed_checks'],full_verifiers_invoked=report['full_verifiers_invoked'],
                    numerical_rechecks_completed=report['numerical_rechecks_completed'],
                    error=None if report['status']=='passed' else '材料或独立核验未全部通过，请查看本次报告。',
                    report_url='/fdna-results/recheck/report',json_url='/fdna-results/recheck/report.json')
                self._save()
        except Exception as exc:
            with self._lock:
                self._state.update(status='failed',finished_at_utc=now(),error=str(exc),numerical_rechecks_completed=False)
                try:self._save()
                except OSError:pass
        finally:
            if process is not None and process.poll() is None:process.terminate();process.wait()

    def report(self,kind):
        if kind not in ('json','markdown'):raise RecheckError(404,'未知报告类型')
        with self._lock:
            if self._state['status'] not in ('passed','failed') or not self._state.get('report_url'):
                raise RecheckError(409,'本次任务还没有可用的完整报告。')
            directory=self._job_dir/'report'
            if not directory.resolve().is_relative_to(self._base):raise RecheckError(409,'报告目录已越出本次运行范围')
            self._read_report(directory)
            name='review_report.json' if kind=='json' else '成果复核与阅读入口.md'
            return directory/name,(directory/name).read_bytes()

    def close(self):
        self._closing.set()
        with self._lock:process=self._process;thread=self._thread
        if process is not None and process.poll() is None:process.terminate()
        if thread is not None:thread.join(timeout=5)
