"""Real workbook conversion, changed constraints, blocked exports and local HTTP."""
from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import threading
import subprocess
import sys
import unittest
from unittest.mock import patch
from http.server import HTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from design.excel import convert, read_design
from design.engine import design
from excel_entry.app import Handler, run_design, main
from excel_entry.importer import EntryError, read_xlsx
from tests.test_rule_review import edit_cell

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'excel_entry/templates/链网设计示例.xlsx'


class DesignExcelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = EXAMPLE.read_bytes()
        cls.sheets = read_xlsx(cls.data)

    def test_roundtrip_preserves_all_engine_inputs(self):
        expected = json.loads((ROOT / 'design/examples/canonical_network_demo.json').read_text(encoding='utf-8'))
        self.assertEqual(read_design(self.data), expected)
        report = design(read_design(self.data))
        self.assertEqual(report['status'], 'complete')
        self.assertEqual([len(t['chains']) for t in report['tasks']], [2, 2])
        self.assertEqual(report['network']['score'], .5)
        self.assertEqual(report['network_search']['feasible'], 3)

    def test_cli_exit_status_includes_export_failure(self):
        for status, export, expected in [('complete','exported',0), ('blocked','not_ready',1),
                ('no_feasible_design','not_ready',1), ('complete','not_ready',1),
                ('complete','validation_failed',2)]:
            with self.subTest(status=status,export=export):
                result={'report': {'status': status}, 'rdf_export': {'status': export}}
                output=io.StringIO()
                with patch('excel_entry.app.run_design',return_value=result), redirect_stdout(output):
                    self.assertEqual(main(['--design-file',str(EXAMPLE)]),expected)
                self.assertEqual(json.loads(output.getvalue()),result)

    def test_cli_invalid_inputs_and_search_limit_return_two(self):
        from design.engine import SearchLimitError
        for error in (EntryError('无效表格'), OSError('写入失败'), SearchLimitError('搜索上限')):
            with self.subTest(error=error):
                output=io.StringIO()
                with patch('excel_entry.app.run_design',side_effect=error), redirect_stdout(output), redirect_stderr(io.StringIO()):
                    self.assertEqual(main(['--design-file',str(EXAMPLE)]),2)
                self.assertEqual(json.loads(output.getvalue())['status'],
                                 'search_limit' if isinstance(error,SearchLimitError) else 'invalid')

    def test_module_process_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            success=EXAMPLE
            blocked=Path(directory)/'blocked.xlsx'
            data=self.data
            for cell in ('D2','E2','F2','G2'):
                data=edit_cell(data,3,cell,None)
            blocked.write_bytes(data)
            for path, expected in ((success,0),(blocked,1),(Path(directory)/'missing.xlsx',2)):
                with self.subTest(expected=expected):
                    process=subprocess.run([sys.executable,'-B','-m','excel_entry.app','--design-file',str(path)],
                        cwd=ROOT,capture_output=True,encoding='utf-8',
                        env=__import__('os').environ | {'PYTHONIOENCODING':'utf-8'},timeout=30)
                    self.assertEqual(process.returncode,expected,process.stderr)
                    self.assertIsInstance(json.loads(process.stdout),dict)

    def test_resource_change_reselects_network(self):
        data = edit_cell(edit_cell(self.data, 3, 'H4', 2), 3, 'J4', 2)
        report = design(read_design(data))
        self.assertEqual(report['network']['score'], 1)
        self.assertEqual(report['network_search']['feasible'], 4)
        self.assertTrue(all(c['artifacts']['manifestation'] == 'demo-fast'
            for t in report['tasks'] for c in t['chains'] if c['id'] in report['network']['chains']))

    def test_missing_metrics_and_context_block_instead_of_defaulting(self):
        sheets = deepcopy(self.sheets)
        for col in (4, 5, 6, 7):
            sheets['法器'][2].pop(col)
        report = design(convert(sheets))
        self.assertEqual(report['status'], 'blocked')
        self.assertIsNone(report['network'])
        with tempfile.TemporaryDirectory() as directory:
            data = self.data
            for cell in ('D2', 'E2', 'F2', 'G2'):
                data = edit_cell(data, 3, cell, None)
            response = run_design(data, 'missing.xlsx', output_root=Path(directory))
            self.assertEqual(response['rdf_export']['status'], 'not_ready')
            self.assertFalse((Path(response['output']) / 'design.ttl').exists())
        sheets = deepcopy(self.sheets)
        sheets['情境'].pop(2)
        sheets['属性'] = {r: cells for r, cells in sheets['属性'].items()
                         if not (cells.get(1) == '情境' and cells.get(2) == 'demo-task-1' and cells.get(3) == 'demo-eye')}
        self.assertEqual(design(convert(sheets))['status'], 'blocked')

    def test_invalid_inputs_rejected(self):
        for sheet, cell, value in [(3,'D2',None),(3,'H2',None),(3,'H2',1.5),
            (2,'C2',None),(7,'B2','absent'),(6,'E4','true'),(6,'E2','false'),
            (8,'A2','absent'),(5,'D3','target_type'),(1,'A3','范围说明')]:
            with self.subTest(sheet=sheet,cell=cell):
                with self.assertRaises(ValueError):
                    read_design(edit_cell(self.data,sheet,cell,value))
        sheets=deepcopy(self.sheets)
        sheets['属性'][2][3]='unexpected'
        with self.assertRaises(EntryError):
            convert(sheets)

    def test_blank_has_no_fabricated_settings_or_records(self):
        sheets=read_xlsx((EXAMPLE.parent/'链网设计空白.xlsx').read_bytes())
        self.assertEqual(len(sheets['任务']),1)
        self.assertTrue(all(2 not in row for n,row in sheets['设计设置'].items() if n>1))
        with self.assertRaises(ValueError):
            convert(sheets)

    def test_optional_constraints_and_evidence_are_not_discarded(self):
        sheets=deepcopy(self.sheets)
        sheets['资源组'][2]={1:'shared-budget',2:1,3:'demo-fast;demo-slow'}
        report=design(convert(sheets))
        self.assertEqual(report['status'],'no_feasible_design')
        self.assertTrue(any(r['code']=='GROUP_CAPACITY' for r in report['network_search']['rejections']))
        sheets=deepcopy(self.sheets)
        sheets['任务依赖'][2]={1:'demo-task-1',2:'demo-task-2',3:1}
        self.assertEqual(design(convert(sheets))['status'],'no_feasible_design')
        sheets['任务'][3][3]=6
        self.assertEqual(design(convert(sheets))['status'],'complete')
        sheets['任务'][2][5]='unconfirmed-task'
        self.assertEqual(design(convert(sheets))['status'],'blocked')

    def test_numeric_literal_conditions_preserve_units_and_unknowns(self):
        sheets=deepcopy(self.sheets)
        # Replace the first resource's field comparison with a numeric literal.
        sheets['规则条件'][3]={1:'法器',2:'insight-range',3:'artifact.max_range',
                              4:'ge',5:'常量',6:'数值',7:100,8:'nmi'}
        self.assertEqual(design(convert(sheets))['status'],'complete')
        sheets['规则条件'][3][8]='m'
        self.assertEqual(design(convert(sheets))['status'],'blocked')
        sheets['规则条件'][3].pop(7)
        self.assertEqual(design(convert(sheets))['status'],'blocked')

    def test_unmapped_keeps_report_without_rdf(self):
        # Change actual XLSX too, so the entire saved-output path is exercised.
        data=edit_cell(edit_cell(self.data,6,'D3','尚未登记的协调能力'),6,'D6','尚未登记的协调能力')
        with tempfile.TemporaryDirectory() as directory:
            result=run_design(data,'unmapped.xlsx',output_root=Path(directory))
            self.assertEqual(result['report']['status'],'complete')
            self.assertFalse(result['report']['canonical_export_ready'])
            self.assertEqual(result['rdf_export']['status'],'not_ready')
            self.assertFalse((Path(result['output'])/'design.ttl').exists())
            self.assertTrue((Path(result['output'])/'design_report.json').exists())

    def test_outputs_and_http_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            def run(data, filename):
                return run_design(data,filename,output_root=Path(directory))
            server=HTTPServer(('127.0.0.1',0),Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                for route in ('/design','/design-example.xlsx','/design-blank.xlsx'):
                    with urlopen(base+route) as response:
                        self.assertEqual(response.status,200)
                with patch('excel_entry.app.run_design',side_effect=run):
                    with urlopen(Request(base+'/design-run?example.xlsx',data=self.data,method='POST')) as response:
                        result=json.load(response)
                    out=Path(result['output'])
                    self.assertEqual(result['rdf_export']['status'],'exported')
                    self.assertTrue((out/'design.ttl').exists())
                    self.assertEqual((out/'input.xlsx').read_bytes(),self.data)
                    self.assertEqual(json.loads((out/'design_report.json').read_text(encoding='utf-8')),result['report'])
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(base+'/design-run',data=b'bad',method='POST'))
                    self.assertEqual(error.exception.code,400)
                    error.exception.close()
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(base+'/design-run',data=self.data,headers={'Origin':'https://example.com'},method='POST'))
                    self.assertEqual(error.exception.code,403)
                    error.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
