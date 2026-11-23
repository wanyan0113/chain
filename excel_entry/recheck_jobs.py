"""One asynchronous full-review subprocess, with server-selected inputs and outputs."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
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

    @staticmethod
    def _urls(ident):
        base='/fdna-results/recheck/jobs/'+ident
        return dict(report_url=base+'/report',json_url=base+'/report.json')

    def _record(self,ident):
        with self._lock:return self._read_record(ident)

    def _read_record(self,ident):
        if not self._state['enabled'] or not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):
            raise RecheckError(404,'未知任务编号')
        directory=self._base/ident
        if not directory.is_dir() or directory.is_symlink() or directory.resolve().parent!=self._base:
            raise RecheckError(404,'任务目录不存在或不在已配置运行范围')
        file=directory/'job_status.json'
        if file.is_symlink():raise RecheckError(409,'任务状态文件不能为符号链接')
        try:state=read(file)
        except (ValueError,OSError) as exc:raise RecheckError(409,'任务状态记录缺失或不可读取') from exc
        if not isinstance(state,dict) or state.get('id')!=ident or state.get('status') not in ('running','passed','failed'):
            raise RecheckError(409,'任务状态记录的编号或格式不一致')
        return directory,state

    def history(self,limit=20):
        """Inspect persisted records; do not resume jobs or rewrite their recorded state."""
        if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=100:raise RecheckError(400,'记录条数须为1至100')
        with self._lock:current=deepcopy(self._state)
        if not current['enabled']:return dict(enabled=False,records=[],limit=limit,read_only=True)
        candidates=[]
        if self._base.is_dir():
            for p in self._base.iterdir():
                if re.fullmatch('[0-9a-f]{32}',p.name) and p.is_dir() and not p.is_symlink():
                    candidates.append(p)
        candidates.sort(key=lambda p:p.stat().st_mtime_ns,reverse=True)
        rows=[]
        for directory in candidates[:limit]:
            ident=directory.name;row=dict(id=ident,status='invalid',recorded_status=None,started_at_utc=None,
                finished_at_utc=None,report_available=False,report_status=None,report_url=None,json_url=None,message=None)
            try:
                directory,state=self._record(ident);raw=state['status']
                active=current.get('id')==ident and current['status']=='running'
                row.update(recorded_status=raw,status='running' if active else 'unconfirmed' if raw=='running' else raw,
                    started_at_utc=state.get('started_at_utc'),finished_at_utc=state.get('finished_at_utc'))
                if active:row['message']='本服务会话正在运行，完成前不提供报告。'
                else:
                    try:
                        report_dir=directory/'report'
                        if not report_dir.resolve().is_relative_to(directory.resolve()):raise ValueError('报告目录越出该任务范围')
                        report=self._read_report(report_dir)
                        if raw=='passed' and (report['status']!='passed' or state.get('completed_tasks')!=len(TASKS)
                                or not state.get('numerical_rechecks_completed')):raise ValueError('任务状态与完整报告不一致')
                        row.update(report_available=True,report_status=report['status'],**self._urls(ident))
                    except (ValueError,OSError,KeyError,TypeError) as exc:
                        if raw=='passed':row.update(status='invalid',message='报告检查未通过：'+str(exc))
                        elif raw=='failed':row['message']=state.get('error') or '任务失败且没有完整报告。'
                        else:row['message']='非本会话的运行记录，状态未确认；未自动续跑。'
                    if raw=='running':row['message']='非本会话的运行状态未确认；即使留有可校验报告，也不自动认定任务完成或重新执行。'
                    elif raw=='failed' and row['report_available']:row['message']=state.get('error') or '失败任务的完整诊断报告仍可读取。'
            except (ValueError,OSError,KeyError,TypeError) as exc:row['message']=str(exc)
            rows.append(row)
        return dict(enabled=True,records=rows,limit=limit,total_records=len(candidates),read_only=True)

    def _save(self):
        if self._job_dir is None:return
        temporary=self._job_dir/'job_status.tmp'
        temporary.write_text(json.dumps(self._state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
        for attempt in range(3):
            try:
                temporary.replace(self._job_dir/'job_status.json');break
            except PermissionError:
                if attempt==2:raise
                time.sleep(.01*(attempt+1))

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
                    **self._urls(ident))
                self._save()
        except Exception as exc:
            with self._lock:
                self._state.update(status='failed',finished_at_utc=now(),error=str(exc),numerical_rechecks_completed=False)
                try:self._save()
                except OSError:pass
        finally:
            if process is not None and process.poll() is None:process.terminate();process.wait()

    def report(self,kind,job_id=None):
        if kind not in ('json','markdown'):raise RecheckError(404,'未知报告类型')
        with self._lock:
            if job_id is None:
                if self._state['status'] not in ('passed','failed') or not self._state.get('report_url'):
                    raise RecheckError(409,'本次任务还没有可用的完整报告。')
                job_id=self._state['id']
            job_dir,state=self._record(job_id)
            if self._state.get('id')==job_id and self._state['status']=='running':
                raise RecheckError(409,'任务仍在运行，尚未提供最终报告。')
            directory=job_dir/'report'
            if not directory.resolve().is_relative_to(job_dir.resolve()):raise RecheckError(409,'报告目录已越出该任务范围')
            report=self._read_report(directory)
            if state['status']=='passed' and (report['status']!='passed' or state.get('completed_tasks')!=len(TASKS)
                    or not state.get('numerical_rechecks_completed')):raise RecheckError(409,'任务状态与完整报告不一致')
            name='review_report.json' if kind=='json' else '成果复核与阅读入口.md'
            return directory/name,(directory/name).read_bytes()

    def close(self):
        self._closing.set()
        with self._lock:process=self._process;thread=self._thread
        if process is not None and process.poll() is None:process.terminate()
        if thread is not None:thread.join(timeout=5)
