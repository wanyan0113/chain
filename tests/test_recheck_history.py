import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from excel_entry.app import ROOT
from excel_entry.recheck_jobs import RecheckJobs,RecheckError
from tests import test_recheck_jobs as fixtures


class RecheckHistoryTests(fixtures.JobFixture):
    def complete(self):
        self.release.set();state=self.jobs.start();fixtures.wait_for(lambda:self.jobs.snapshot()['status']=='passed')
        return state['id'],self.output

    def test_restart_lists_prior_report_without_starting_any_process(self):
        ident,directory=self.complete();before=(directory.parent/'job_status.json').read_bytes()
        def forbidden(*args,**kwargs):raise AssertionError('历史查询不能启动子进程')
        restarted=RecheckJobs(ROOT,self.project,popen_factory=forbidden);self.addCleanup(restarted.close)
        result=restarted.history();row=result['records'][0]
        self.assertTrue(result['read_only']);self.assertEqual(row['id'],ident);self.assertEqual(row['status'],'passed')
        self.assertTrue(row['report_available']);self.assertIn('/jobs/'+ident+'/',row['json_url'])
        self.assertEqual(restarted.snapshot()['status'],'idle')
        self.assertEqual(before,(directory.parent/'job_status.json').read_bytes())
        self.assertEqual(json.loads(restarted.report('json',ident)[1])['mode'],'full')

    def test_stable_report_address_keeps_first_result_after_second_run(self):
        first,old=self.complete();first_bytes=self.jobs.report('json',first)[1]
        second,new=self.complete();self.assertNotEqual(first,second)
        self.assertEqual(first_bytes,self.jobs.report('json',first)[1])
        rows=self.jobs.history()['records'];self.assertEqual({r['id'] for r in rows},{first,second})
        self.assertNotEqual(next(r['json_url'] for r in rows if r['id']==first),next(r['json_url'] for r in rows if r['id']==second))

    def test_non_session_running_record_is_unconfirmed_not_resumed_or_upgraded(self):
        ident,directory=self.complete();path=directory.parent/'job_status.json'
        state=json.loads(path.read_text(encoding='utf-8'));state['status']='running';path.write_text(json.dumps(state),encoding='utf-8')
        before=path.read_bytes();restarted=RecheckJobs(ROOT,self.project)
        row=restarted.history()['records'][0]
        self.assertEqual(row['status'],'unconfirmed');self.assertEqual(row['recorded_status'],'running')
        self.assertEqual(row['report_status'],'passed');self.assertTrue(row['report_available'])
        self.assertEqual(restarted.snapshot()['status'],'idle');self.assertEqual(path.read_bytes(),before)

    def test_corrupt_report_and_bad_state_are_visible_and_do_not_hide_good_runs(self):
        first,old=self.complete();second,new=self.complete()
        (old/'review_report.json').write_text('changed')
        bad=new.parent.parent/('f'*32);bad.mkdir();(bad/'job_status.json').write_text('{bad json')
        rows={r['id']:r for r in self.jobs.history()['records']}
        self.assertEqual(rows[first]['status'],'invalid');self.assertFalse(rows[first]['report_available'])
        self.assertEqual(rows[second]['status'],'passed');self.assertEqual(rows['f'*32]['status'],'invalid')
        with self.assertRaises(ValueError):self.jobs.report('json',first)

    def test_ids_and_metadata_cannot_redirect_to_another_directory(self):
        ident,directory=self.complete()
        for bad in ('../report','C:\\other','a'*31,'b'*32):
            with self.subTest(bad=bad),self.assertRaises(RecheckError):self.jobs.report('json',bad)
        state=json.loads((directory.parent/'job_status.json').read_text(encoding='utf-8'));state['id']='a'*32
        (directory.parent/'job_status.json').write_text(json.dumps(state),encoding='utf-8')
        with self.assertRaises(RecheckError):self.jobs.report('json',ident)
        self.assertEqual(self.jobs.history()['records'][0]['status'],'invalid')

    def test_current_running_job_is_shown_but_has_no_premature_report(self):
        state=self.jobs.start();fixtures.wait_for(lambda:self.calls)
        row=self.jobs.history()['records'][0]
        self.assertEqual(row['status'],'running');self.assertFalse(row['report_available'])
        with self.assertRaises(RecheckError):self.jobs.report('json',state['id'])
        self.release.set()

    def test_empty_disabled_and_bounded_history(self):
        self.assertEqual(RecheckJobs(ROOT).history()['records'],[])
        self.assertEqual(self.jobs.history()['records'],[])
        first,_=self.complete();second,_=self.complete();self.assertEqual(len(self.jobs.history(1)['records']),1)
        for bad in (0,101,True,None):
            with self.subTest(bad=bad),self.assertRaises(RecheckError):self.jobs.history(bad)

    def test_transient_windows_replace_denial_is_retried_without_failing_the_job(self):
        original=Path.replace;attempts=[]
        def busy_once(path,target):
            attempts.append(str(target))
            if len(attempts)==1:raise PermissionError('file temporarily open')
            return original(path,target)
        with patch.object(Path,'replace',autospec=True,side_effect=busy_once),patch('excel_entry.recheck_jobs.time.sleep'):
            state=self.jobs.start()
        self.assertGreaterEqual(len(attempts),2);self.assertEqual(state['status'],'running')
        self.release.set();fixtures.wait_for(lambda:self.jobs.snapshot()['status']=='passed')

    def test_permanent_windows_replace_denial_is_bounded_and_not_left_running(self):
        with patch.object(Path,'replace',side_effect=PermissionError('not writable')) as replace,patch('excel_entry.recheck_jobs.time.sleep'):
            with self.assertRaises(RecheckError):self.jobs.start()
        self.assertEqual(replace.call_count,3);self.assertEqual(self.jobs.snapshot()['status'],'failed')
        self.assertFalse(self.calls)


class RecheckHistoryHTTPTests(fixtures.JobFixture):
    def setUp(self):
        from http.server import HTTPServer
        import threading
        from excel_entry.app import Handler
        super().setUp()
        class QuietHandler(Handler):
            def log_message(self,*args):pass
        self.server=HTTPServer(('127.0.0.1',0),QuietHandler);self.server.review_jobs=self.jobs
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}';self.addCleanup(self.stop_server)
    def stop_server(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def test_persisted_history_and_per_id_download_survive_service_object_restart(self):
        self.release.set();first=self.jobs.start();fixtures.wait_for(lambda:self.jobs.snapshot()['status']=='passed')
        saved=self.jobs.report('json',first['id'])[1]
        second=self.jobs.start();fixtures.wait_for(lambda:self.jobs.snapshot()['status']=='passed')
        self.server.review_jobs=RecheckJobs(ROOT,self.project)
        self.addCleanup(self.server.review_jobs.close)
        with urlopen(self.base+'/fdna-results/recheck/history') as response:history=json.load(response)
        row=next(r for r in history['records'] if r['id']==first['id'])
        with urlopen(self.base+row['json_url']) as response:self.assertEqual(response.read(),saved)
        with urlopen(self.base+row['report_url']) as response:self.assertIn('指定网页任务',response.read().decode('utf-8'))
        with urlopen(self.base+'/fdna-results/recheck/status') as response:self.assertEqual(json.load(response)['status'],'idle')
        with self.assertRaises(HTTPError) as ctx:urlopen(self.base+'/fdna-results/recheck/jobs/'+'0'*32+'/report.json')
        self.assertEqual(ctx.exception.code,404);ctx.exception.close()


if __name__=='__main__':unittest.main()
