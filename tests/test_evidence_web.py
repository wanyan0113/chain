from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote,unquote
from urllib.request import urlopen
from http.server import HTTPServer

from excel_entry.app import Handler,ROOT
from excel_entry.evidence_web import status_data,artifact,reading_page,inline,target_url,EvidenceError


class Images(HTMLParser):
    def __init__(self):super().__init__();self.images=[]
    def handle_starttag(self,tag,attrs):
        if tag=='img':self.images.append(dict(attrs))


class EvidenceWebTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name);(self.repo/'data/fdna').mkdir(parents=True)
        self.package=self.repo/'docs/results';self.package.mkdir(parents=True)
        (self.package/'结果.md').write_text('# 结果\n\n配合说明。\n\n![图](图.png)\n\n<script>alert(1)</script>\n',encoding='utf-8')
        (self.package/'图.png').write_bytes(b'PNG-test')
        self.index={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.package.iterdir()}
        (self.package/'manifest.json').write_text(json.dumps(self.index),encoding='utf-8')
        self.catalog=dict(id='flow06_optimization_evidence_catalog_v1',packages=[dict(id='test',title='实验图文',directory='docs/results',entrypoint='结果.md',required_files=['结果.md','图.png'])],
            documents=[],priorities=[dict(number=i,title=str(i),state='deferred',evidence=[],scope='已记录',remaining='待办') for i in range(1,10)])
        self.catalog_path=self.repo/'data/fdna/evidence_catalog.json';self.catalog_path.write_text(json.dumps(self.catalog),encoding='utf-8')

    def test_status_is_current_material_check_without_numeric_execution(self):
        result=status_data(self.repo)
        self.assertEqual(result['status'],'passed');self.assertEqual(result['package_files_checked'],2)
        self.assertFalse(result['numerical_rechecks_executed']);self.assertFalse(result['performance_remeasured'])
        self.assertEqual(result['historical_full']['status'],'unavailable')
        self.assertEqual(result['packages'][0]['figure_count'],1)

    def test_read_only_web_import_does_not_load_optional_fitting_libraries(self):
        code="import sys; from excel_entry.evidence_web import catalog; assert 'scipy' not in sys.modules; assert 'numpy' not in sys.modules; assert len(catalog('.') ['packages']) == 7"
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_changed_material_is_failed_and_preview_removed(self):
        (self.package/'图.png').write_bytes(b'changed')
        result=status_data(self.repo)
        self.assertEqual(result['status'],'failed');self.assertIsNone(result['packages'][0]['thumbnail_url'])
        with self.assertRaises(EvidenceError) as ctx:artifact(self.repo,'test','图.png')
        self.assertEqual(ctx.exception.status,409)
        with self.assertRaises(EvidenceError):reading_page(self.repo,'/fdna-results/read/test/'+quote('结果.md'))

    def test_reader_renders_images_and_escapes_source_html(self):
        page=reading_page(self.repo,'/fdna-results/read/test/'+quote('结果.md'))
        self.assertIn('&lt;script&gt;',page);self.assertNotIn('<script>alert',page)
        parser=Images();parser.feed(page)
        self.assertEqual(parser.images[0]['src'],'/fdna-results/files/test/'+quote('图.png'))
        _,data,mime=artifact(self.repo,'test','图.png');self.assertEqual(data,b'PNG-test');self.assertEqual(mime,'image/png')

    def test_links_do_not_expose_undeclared_files_or_execute_script_urls(self):
        source=self.package/'结果.md'
        (self.repo/'secret.txt').write_text('private')
        self.assertIsNone(target_url(self.repo,source,'../../secret.txt'))
        self.assertIsNone(target_url(self.repo,source,'javascript:alert(1)'))
        self.assertIsNone(target_url(self.repo,source,'https://example.com/x.png',True))
        self.assertNotIn('href="javascript:',inline('[点](javascript:alert)',self.repo,source))
        with self.assertRaises(ValueError):artifact(self.repo,'test','../../secret.txt')
        with self.assertRaises(EvidenceError):artifact(self.repo,'test','not-listed.json')

    def historical(self):
        directory=self.repo/'docs/evidence-review-report';directory.mkdir()
        current=status_data(self.repo)
        record=dict(checked_at_utc='2026-11-23T00:00:00+00:00',status='passed',packages=current['packages'],
                    full_verifiers_invoked=10,numerical_rechecks_completed=True,full_checks=[dict(title='历史任务',status='passed',verifier_invoked=True)])
        (directory/'本次完整复核.json').write_text(json.dumps(record),encoding='utf-8')
        (directory/'本次完整复核.md').write_text('# 历史记录',encoding='utf-8')
        (directory/'目录版本.json').write_text(json.dumps(self.catalog),encoding='utf-8')
        inventory={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
        (directory/'manifest.json').write_text(json.dumps(inventory),encoding='utf-8')
        return directory

    def test_history_has_its_own_time_and_is_not_a_fresh_numeric_check(self):
        self.historical();result=status_data(self.repo)
        self.assertEqual(result['historical_full']['status'],'available')
        self.assertTrue(result['historical_full']['matches_current_materials'])
        self.assertNotEqual(result['checked_at_utc'],result['historical_full']['checked_at_utc'])
        self.assertFalse(result['numerical_rechecks_executed'])
        (self.package/'图.png').write_bytes(b'changed')
        self.assertFalse(status_data(self.repo)['historical_full']['matches_current_materials'])

    def test_current_document_failure_breaks_history_material_match(self):
        path=self.repo/'docs/说明.md';path.write_bytes(b'original')
        self.catalog['documents']=[dict(id='d',title='说明',path='docs/说明.md',normalized_sha256=hashlib.sha256(b'original').hexdigest())]
        self.catalog_path.write_text(json.dumps(self.catalog),encoding='utf-8');self.historical()
        path.write_bytes(b'changed')
        result=status_data(self.repo);self.assertEqual(result['status'],'failed')
        self.assertFalse(result['historical_full']['matches_current_materials'])

    def test_report_tables_and_code_remain_escaped(self):
        from excel_entry.evidence_web import markdown_body
        text='| 名称 | 值 |\n| --- | --- |\n| x | **通过** |\n\n```html\n<img onerror="bad">\n```'
        page=markdown_body(text,self.repo,self.package/'结果.md')
        self.assertIn('<table>',page);self.assertIn('<strong>通过</strong>',page)
        self.assertIn('&lt;img onerror=',page);self.assertNotIn('<img onerror=',page)


class EvidenceWebHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class QuietHandler(Handler):
            def log_message(self,*args):pass
        cls.server=HTTPServer(('127.0.0.1',0),QuietHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def test_workspace_tab_and_read_only_api(self):
        with urlopen(self.base+'/') as r:self.assertIn('tab-evidence',r.read().decode('utf-8'))
        with urlopen(self.base+'/fdna-results?embedded=1') as r:
            html=r.read().decode('utf-8');self.assertIn('检查当前发布材料',html)
            self.assertIn('Content-Security-Policy',r.headers);self.assertIn('display:none!important',html)
        with urlopen(self.base+'/fdna-results/status?mode=full') as r:status=json.load(r)
        self.assertEqual(status['package_files_checked'],95);self.assertFalse(status['numerical_rechecks_executed'])
        self.assertEqual(status['historical_full']['full_verifiers_invoked'],10)

    def test_report_images_are_served_and_encoded_traversal_blocked(self):
        with urlopen(self.base+'/fdna-results/status') as r:data=json.load(r)
        for package in data['packages']:
            with urlopen(self.base+package['reading_url']) as r:page=r.read().decode('utf-8')
            parser=Images();parser.feed(page);self.assertTrue(parser.images,package['id'])
            for image in parser.images:
                with urlopen(self.base+image['src']) as r:self.assertEqual(r.headers.get_content_type(),'image/png')
        for path in ('/fdna-results/files/overview/%2e%2e/%2e%2e/README.md','/fdna-results/files/unknown/README.md','/fdna-results/files/overview/manifest.json','/fdna-results/files/overview/汇报图文.html'):
            with self.assertRaises(HTTPError) as ctx:urlopen(self.base+quote(path,safe='/%'))
            self.assertIn(ctx.exception.code,(404,409));ctx.exception.close()


if __name__=='__main__':unittest.main()
