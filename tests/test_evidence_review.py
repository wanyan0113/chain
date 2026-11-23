from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fdna.evidence_review import (safe_path,check_package,check_document,validate_catalog,run,full_checks,TASKS)
from fdna.model import ModelError


class EvidenceReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=self.root/'repo';self.repo.mkdir()
        self.package=self.repo/'docs/report';self.package.mkdir(parents=True)
        (self.package/'说明.md').write_text('成果',encoding='utf-8')
        self.index={'说明.md':hashlib.sha256((self.package/'说明.md').read_bytes()).hexdigest()}
        (self.package/'manifest.json').write_text(json.dumps(self.index),encoding='utf-8')
        self.entry=dict(id='p',title='成果',directory='docs/report',entrypoint='说明.md',required_files=['说明.md'])
        self.catalog=dict(id='flow06_optimization_evidence_catalog_v1',packages=[self.entry],documents=[],priorities=[
            dict(number=i,title=f'项目{i}',state='implemented_scoped' if i<=5 else 'partial' if i<=7 else 'deferred',
                 evidence=['p'] if i<=7 else [],scope='已记录范围',remaining='保留边界') for i in range(1,10)])
        self.catalog_path=self.root/'catalog.json';self.catalog_path.write_text(json.dumps(self.catalog),encoding='utf-8')

    def test_published_mode_checks_bytes_and_explicitly_does_not_recompute(self):
        before=(self.package/'说明.md').read_bytes()
        with patch('fdna.evidence_review.full_checks',side_effect=AssertionError('不得隐式数值重算')):
            report=run(self.repo,None,self.root/'output',catalog_path=self.catalog_path)
        self.assertEqual(report['status'],'passed');self.assertFalse(report['numerical_rechecks_executed'])
        self.assertFalse(report['performance_remeasured']);self.assertFalse(report['scientific_validity_established'])
        self.assertEqual(report['declared_progress_counts'],dict(implemented_scoped=5,partial=2,deferred=2))
        self.assertEqual(before,(self.package/'说明.md').read_bytes())
        md=(self.root/'output/成果复核与阅读入口.md').read_text(encoding='utf-8')
        self.assertIn('本次没有重新执行数值核验',md);self.assertIn('../repo/docs/report/',md)

    def test_changed_missing_and_extra_files_fail_separately(self):
        (self.package/'说明.md').write_text('已变化',encoding='utf-8')
        self.assertEqual(check_package(self.repo,self.entry)['status'],'failed')
        (self.package/'说明.md').unlink()
        self.assertEqual(check_package(self.repo,self.entry)['status'],'failed')
        (self.package/'说明.md').write_text('成果',encoding='utf-8');(self.package/'unlisted.txt').write_text('extra')
        result=check_package(self.repo,self.entry)
        self.assertEqual(result['status'],'failed');self.assertTrue(any('未登记' in e['error'] for e in result['errors']))

    def test_failed_material_check_keeps_report_and_does_not_upgrade_declared_progress(self):
        (self.package/'说明.md').write_text('changed',encoding='utf-8')
        report=run(self.repo,None,self.root/'failed_output',catalog_path=self.catalog_path)
        self.assertEqual(report['status'],'failed');self.assertEqual(report['failed_checks'],1)
        self.assertFalse(report['numerical_rechecks_executed'])
        self.assertTrue((self.root/'failed_output/review_report.json').is_file())
        self.assertEqual(report['declared_progress_counts']['partial'],2)

    def test_manifest_traversal_and_absolute_paths_are_rejected(self):
        for name in ('../secret','..\\secret','C:\\secret','/etc/passwd','a/../b','a//b'):
            with self.subTest(name=name),self.assertRaises(ValueError):safe_path(self.repo,name)
        (self.package/'manifest.json').write_text(json.dumps({'../../secret':'0'*64}),encoding='utf-8')
        self.assertEqual(check_package(self.repo,self.entry)['status'],'failed')

    def test_required_entries_and_invalid_manifest_are_reported(self):
        extra=dict(self.entry,required_files=['missing.png'])
        self.assertEqual(check_package(self.repo,extra)['status'],'failed')
        (self.package/'manifest.json').write_text('[]')
        self.assertEqual(check_package(self.repo,self.entry)['status'],'failed')

    def test_document_hash_normalizes_line_endings_but_rejects_content_changes(self):
        p=self.repo/'docs/boundary.md';p.write_bytes(b'line\r\n')
        entry=dict(id='d',title='doc',path='docs/boundary.md',normalized_sha256=hashlib.sha256(b'line\n').hexdigest())
        self.assertEqual(check_document(self.repo,entry)['status'],'passed')
        p.write_bytes(b'changed\n');self.assertEqual(check_document(self.repo,entry)['status'],'failed')

    def test_full_checks_keep_failures_and_continue_no_silent_skip(self):
        for task in TASKS:(self.root/task[3]).mkdir(parents=True,exist_ok=True)
        calls=[]
        def runner(module,root,directory):
            calls.append(module)
            if module==TASKS[1][2]:raise AssertionError('故意失败')
            return dict(status='passed',count=1)
        results=full_checks(self.root,runner=runner)
        self.assertEqual(len(calls),10);self.assertEqual(len(results),10)
        self.assertEqual(results[1]['status'],'failed');self.assertEqual(results[-1]['status'],'passed')
        self.assertEqual(results[1]['error_type'],'AssertionError')

    def test_missing_local_sources_are_failures_and_optimized_python_is_rejected(self):
        called=[]
        results=full_checks(self.root,runner=lambda *args:called.append(args))
        self.assertFalse(called);self.assertTrue(all(r['status']=='failed' for r in results))
        self.assertTrue(all(not r['verifier_invoked'] for r in results))
        with patch('fdna.evidence_review.sys.flags',SimpleNamespace(optimize=1)),self.assertRaises(ModelError):full_checks(self.root)
        with self.assertRaises(ModelError):run(self.repo,None,self.root/'output',mode='full',catalog_path=self.catalog_path)

    def test_full_mode_without_sources_does_not_claim_numerical_execution(self):
        report=run(self.repo,self.root,self.root/'missing_sources',mode='full',catalog_path=self.catalog_path)
        self.assertEqual(report['status'],'failed');self.assertEqual(report['full_verifiers_invoked'],0)
        self.assertFalse(report['numerical_rechecks_executed']);self.assertFalse(report['numerical_rechecks_completed'])
        self.assertEqual(len(report['full_checks']),10)

    def test_protected_outputs_and_nonempty_directory_rejected(self):
        for output in (self.repo/'docs/new',self.repo/'fdna/new',self.root/TASKS[0][3]/'new'):
            with self.subTest(output=output),self.assertRaises(ModelError):run(self.repo,self.root,output,catalog_path=self.catalog_path)
        out=self.root/'nonempty';out.mkdir();(out/'keep').write_text('keep')
        with self.assertRaises(ModelError):run(self.repo,None,out,catalog_path=self.catalog_path)
        self.assertEqual((out/'keep').read_text(),'keep')

    def test_catalog_requires_nine_unique_scoped_items_and_known_evidence(self):
        for change in ('count','evidence','state','missing_field'):
            catalog=deepcopy(self.catalog)
            if change=='count':catalog['priorities'].pop()
            elif change=='evidence':catalog['priorities'][0]['evidence']=['unknown']
            elif change=='state':catalog['priorities'][0]['state']='scientifically_valid'
            else:catalog['priorities'][0].pop('title')
            with self.subTest(change=change),self.assertRaises(ModelError):validate_catalog(catalog)

    def test_cli_exit_codes_distinguish_failed_checks_and_invalid_request(self):
        from fdna.__main__ import main
        with patch('fdna.evidence_review.run',return_value=dict(status='failed',mode='published',package_files_checked=0,failed_checks=1,
                numerical_rechecks_executed=False,declared_progress_counts={})),redirect_stdout(io.StringIO()):
            self.assertEqual(main(['review-results','--output-dir',str(self.root/'cli')]),1)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['review-results','--mode','full','--output-dir',str(self.root/'cli')]),2)


if __name__=='__main__':unittest.main()
