from http.server import HTTPServer
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from excel_entry.app import Handler,ROOT
from excel_entry.recheck_jobs import RecheckJobs,RecheckError
from fdna.evidence_review import TASKS


def wait_for(fn):
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        result=fn()
        if result:return result
        time.sleep(.01)
    raise AssertionError('后台测试任务未按预期结束')


class FakeProcess:
    def __init__(self,release,code):self.release=release;self.code=code;self.returncode=None;self.stopped=False;self.stdout=self.lines()
    def lines(self):
        for i,task in enumerate(TASKS):
            yield json.dumps(dict(stage='started',task=task[0],title=task[1]))+'\n'
            if i==0:self.release.wait(timeout=3)
            if self.stopped:return
            yield json.dumps(dict(stage='finished',task=task[0],status='failed' if self.code==1 and i==0 else 'passed',verifier_invoked=True))+'\n'
    def poll(self):return self.returncode
    def wait(self):self.returncode=-15 if self.stopped else self.code;return self.returncode
    def terminate(self):self.stopped=True;self.release.set()


class JobFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name);self.release=threading.Event();self.calls=[];self.code=0;self.corrupt=False
        self.jobs=RecheckJobs(ROOT,self.project,popen_factory=self.factory);self.addCleanup(self.jobs.close)
    def factory(self,command,**kwargs):
        self.calls.append(command);self.assertEqual(command[1:4],['-B','-m','fdna']);self.assertIn('--mode',command)
        self.assertEqual(command[command.index('--mode')+1],'full')
        self.output=Path(command[command.index('--output-dir')+1]);self.output.mkdir(parents=True)
        self.assertTrue(self.output.is_relative_to(self.project/'generated/关于优化/15_网页完整复核/运行记录'))
        checks=[dict(id=t[0],title=t[1],status='failed' if self.code==1 and i==0 else 'passed',verifier_invoked=True) for i,t in enumerate(TASKS)]
        report=dict(mode='full',status='passed' if self.code==0 else 'failed',full_checks=checks,failed_checks=int(self.code==1),
                    full_verifiers_invoked=10,numerical_rechecks_completed=self.code==0)
        (self.output/'review_report.json').write_text(json.dumps(report),encoding='utf-8')
        (self.output/'成果复核与阅读入口.md').write_text('# 本次测试复核\n\n结果与状态分开。',encoding='utf-8')
        (self.output/'目录版本.json').write_text('{}')
        manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.output.iterdir()}
        (self.output/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        if self.corrupt:(self.output/'review_report.json').write_text('{}')
        return FakeProcess(self.release,self.code)


class RecheckJobTests(JobFixture):
    def test_disabled_and_protected_configuration(self):
        disabled=RecheckJobs(ROOT)
        self.assertFalse(disabled.snapshot()['enabled'])
        with self.assertRaises(RecheckError):disabled.start()
        with self.assertRaises(ValueError):RecheckJobs(ROOT,ROOT/'docs')
        with self.assertRaises(ValueError):RecheckJobs(ROOT,self.project/'missing')

    def test_background_progress_duplicate_start_and_immutable_snapshots(self):
        first=self.jobs.start();wait_for(lambda:self.calls)
        wait_for(lambda:self.jobs.snapshot()['tasks'][0]['status']=='running')
        second=self.jobs.start();self.assertEqual(first['id'],second['id']);self.assertTrue(second['reused_running'])
        second['tasks'][0]['status']='fake';self.assertNotEqual(self.jobs.snapshot()['tasks'][0]['status'],'fake')
        self.assertEqual(len(self.calls),1)
        with self.assertRaises(RecheckError):self.jobs.report('json')
        self.release.set();done=wait_for(lambda:self.jobs.snapshot() if self.jobs.snapshot()['status']!='running' else None)
        self.assertEqual(done['status'],'passed');self.assertEqual(done['completed_tasks'],10)
        self.assertTrue(all(t['verifier_invoked'] for t in done['tasks']))
        self.assertTrue((self.output.parent/'job_status.json').is_file());self.assertTrue((self.output.parent/'worker.log').is_file())
        self.assertEqual(json.loads(self.jobs.report('json')[1])['mode'],'full')

    def test_failed_verifier_keeps_failed_report_instead_of_success(self):
        self.code=1;self.release.set();self.jobs.start();done=wait_for(lambda:self.jobs.snapshot() if self.jobs.snapshot()['status']!='running' else None)
        self.assertEqual(done['status'],'failed');self.assertFalse(done['numerical_rechecks_completed'])
        self.assertTrue(done['report_url']);self.assertEqual(json.loads(self.jobs.report('json')[1])['status'],'failed')

    def test_unusable_process_or_changed_report_never_passes(self):
        self.corrupt=True;self.release.set();self.jobs.start();done=wait_for(lambda:self.jobs.snapshot() if self.jobs.snapshot()['status']!='running' else None)
        self.assertEqual(done['status'],'failed');self.assertFalse(done['numerical_rechecks_completed']);self.assertIsNone(done['report_url'])
        with self.assertRaises(RecheckError):self.jobs.report('json')

    def test_completed_report_is_rechecked_on_download(self):
        self.release.set();self.jobs.start();wait_for(lambda:self.jobs.snapshot()['status']=='passed')
        (self.output/'review_report.json').write_text('changed')
        with self.assertRaises(ValueError):self.jobs.report('json')

    def test_close_stops_running_child(self):
        first=self.jobs.start();wait_for(lambda:self.calls)
        self.jobs.close();self.assertEqual(self.jobs.snapshot()['status'],'failed')
        self.assertIn('中断',self.jobs.snapshot()['error'])
        with self.assertRaises(RecheckError):self.jobs.start()
        self.assertTrue((self.output.parent/'job_status.json').is_file())

    def test_initial_record_failure_cannot_leave_a_phantom_running_job(self):
        with patch.object(self.jobs,'_save',side_effect=OSError('disk error')),self.assertRaises(RecheckError):self.jobs.start()
        self.assertEqual(self.jobs.snapshot()['status'],'failed');self.assertFalse(self.calls)
        self.release.set();self.jobs.start();wait_for(lambda:self.jobs.snapshot()['status']=='passed')
        self.assertEqual(len(self.calls),1)

    def test_second_completed_run_keeps_first_report_and_uses_new_directory(self):
        self.release.set();first=self.jobs.start();wait_for(lambda:self.jobs.snapshot()['status']=='passed')
        old=self.output;old_bytes=(old/'review_report.json').read_bytes()
        second=self.jobs.start();wait_for(lambda:self.jobs.snapshot()['status']=='passed')
        self.assertNotEqual(first['id'],second['id']);self.assertNotEqual(old,self.output)
        self.assertEqual(old_bytes,(old/'review_report.json').read_bytes());self.assertEqual(len(self.calls),2)


class RecheckHTTPTests(JobFixture):
    def setUp(self):
        super().setUp()
        class QuietHandler(Handler):
            def log_message(self,*args):pass
        self.server=HTTPServer(('127.0.0.1',0),QuietHandler);self.server.review_jobs=self.jobs
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop_server);self.base=f'http://127.0.0.1:{self.server.server_port}'
    def stop_server(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def post(self,data=b'{}',origin=None):
        return urlopen(Request(self.base+'/fdna-results/recheck',data=data,headers={'Origin':origin or self.base,'Content-Type':'application/json'}))

    def test_start_is_async_status_responds_and_report_is_bound_to_job(self):
        with self.post() as response:self.assertEqual(response.status,202);first=json.load(response)
        with urlopen(self.base+'/fdna-results/recheck/status') as response:self.assertEqual(json.load(response)['status'],'running')
        with self.post() as response:self.assertEqual(json.load(response)['id'],first['id'])
        self.release.set();wait_for(lambda:self.jobs.snapshot()['status']=='passed')
        with urlopen(self.base+'/fdna-results/recheck/report.json') as response:
            self.assertIn('attachment',response.headers['Content-Disposition']);self.assertEqual(json.load(response)['mode'],'full')
        with urlopen(self.base+'/fdna-results/recheck/report') as response:self.assertIn('本次测试复核',response.read().decode('utf-8'))
        self.assertEqual(len(self.calls),1)

    def test_reject_cross_origin_and_browser_selected_paths(self):
        for payload,origin,expected in [(b'{}','https://example.com',403),(b'{"project_root":"other"}',None,400),(b'x'*65,None,400)]:
            with self.assertRaises(HTTPError) as ctx:self.post(payload,origin)
            self.assertEqual(ctx.exception.code,expected);ctx.exception.close()
        self.assertFalse(self.calls)


if __name__=='__main__':unittest.main()
