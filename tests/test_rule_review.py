"""Generic ledger review: real XLSX input, boundaries, missing facts and HTTP."""
from copy import deepcopy
import io
import json
from functools import partial
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import HTTPServer
from xml.etree import ElementTree as ET
import zipfile

from excel_entry.app import Handler, run_rule_review
from excel_entry.importer import EntryError, NS, read_xlsx
from rule_review.core import review, read_document

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'rule_review/templates/规则验证示例.xlsx'


def edit_cell(data, sheet_number, cell_ref, value):
    """Disposable test copy only; preserve the workbook outside one cell."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, 'w') as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == f'xl/worksheets/sheet{sheet_number}.xml':
                root = ET.fromstring(content)
                cell = root.find(f'.//s:c[@r="{cell_ref}"]', NS)
                if cell is None:
                    raise AssertionError(f'Missing fixture cell {cell_ref}')
                cell.clear()
                cell.attrib['r'] = cell_ref
                if value is not None:
                    cell.attrib['t'] = 'str' if isinstance(value, str) else 'n'
                    ET.SubElement(cell, '{' + NS['s'] + '}v').text = str(value)
                content = ET.tostring(root)
            target.writestr(item, content)
    return out.getvalue()


class RuleReviewTests(unittest.TestCase):
    def setUp(self):
        self.data = EXAMPLE.read_bytes()

    def test_example_three_states_and_independent_expected_values(self):
        result = review(self.data)
        self.assertEqual([t['status'] for t in result['tasks']], ['matched', 'no_match', 'needs_information'])
        self.assertEqual(result['tasks'][0]['conclusions'], ['归档能力', '快速复核能力'])
        self.assertEqual(result['tasks'][1]['conclusions'], [])
        self.assertEqual(result['tasks'][2]['missing'], ['pages'])
        self.assertEqual(len(result['cases']), 6)
        self.assertTrue(all(c['matches'] for c in result['cases']))
        self.assertEqual(result['case_validation'], {'status': 'passed', 'total': 6, 'matched': 6, 'mismatched': 0})
        self.assertTrue(all(c['status_matches'] and c['conclusion_matches'] for c in result['cases']))

    def test_numeric_boundaries_zero_blank_and_mismatched_unit(self):
        for value, expected in [(20, 'pass'), (21, 'fail'), (0, 'pass'), (None, 'unknown')]:
            with self.subTest(value=value):
                result = review(edit_cell(self.data, 2, 'D3', value))
                self.assertEqual(result['tasks'][0]['rules'][1]['status'], expected)
        result = review(edit_cell(self.data, 2, 'E3', '张'))
        self.assertEqual(result['tasks'][0]['rules'][1]['status'], 'unknown')
        self.assertIn('单位不一致', result['tasks'][0]['rules'][1]['checks'][1]['reason'])

    def test_pending_rule_never_concludes_and_disabled_is_skipped(self):
        pending = edit_cell(self.data, 3, 'F3', '待确认')
        pending = edit_cell(pending, 4, 'E4', None)
        result = review(pending)
        self.assertNotIn('快速复核能力', result['tasks'][0]['conclusions'])
        self.assertEqual(result['tasks'][0]['pending_rules'], ['R2'])
        self.assertIn('规则比较值：pages', result['tasks'][0]['missing'])
        disabled = review(edit_cell(self.data, 3, 'F3', '已停用'))
        self.assertEqual(disabled['tasks'][0]['rules'][1]['status'], 'skipped')

    def test_bad_data_and_dangling_references_rejected(self):
        for sheet, cell, value in [(3, 'A3', 'R1'), (2, 'A3', 'absent'), (5, 'C2', 'absent'),
                                   (4, 'E4', None), (4, 'C4', 'eval'), (3, 'D2', None)]:
            with self.subTest(cell=cell, value=value):
                with self.assertRaises(EntryError):
                    review(edit_cell(self.data, sheet, cell, value))

    def test_false_conjunction_with_missing_fact_is_false(self):
        data = edit_cell(self.data, 2, 'D5', None)
        result = review(data)
        self.assertEqual(result['tasks'][1]['rules'][1]['status'], 'fail')
        self.assertEqual(result['tasks'][1]['status'], 'no_match')

    def test_mismatched_expectation_is_visible_not_used_as_input(self):
        data = edit_cell(edit_cell(self.data, 5, 'D2', 'fail'), 5, 'E2', '无结论')
        result = review(data)
        self.assertFalse(result['cases'][0]['matches'])
        self.assertEqual(result['cases'][0]['actual'], 'pass')
        self.assertIn('归档能力', result['tasks'][0]['conclusions'])

    def test_changed_rule_conclusion_fails_independent_expectations(self):
        result = review(edit_cell(self.data, 3, 'B2', '另一项需求'))
        failed = [case for case in result['cases'] if not case['matches']]
        self.assertEqual([case['id'] for case in failed], ['C1', 'C5'])
        self.assertEqual(result['case_validation'], {'status': 'failed', 'total': 6, 'matched': 4, 'mismatched': 2})
        for case in failed:
            self.assertTrue(case['status_matches'])
            self.assertFalse(case['conclusion_matches'])
            self.assertEqual(case['expected_conclusion'], '归档能力')
            self.assertEqual(case['actual_conclusion'], '另一项需求')

    def test_expected_conclusion_does_not_rewrite_actual_conclusion(self):
        result = review(edit_cell(self.data, 5, 'E2', '独立的错误预期'))
        self.assertFalse(result['cases'][0]['matches'])
        self.assertTrue(result['cases'][0]['status_matches'])
        self.assertEqual(result['cases'][0]['actual_conclusion'], '归档能力')
        self.assertEqual(result['case_validation']['mismatched'], 1)
        for case in result['cases'][2:4] + result['cases'][5:]:
            self.assertIsNone(case['actual_conclusion'])
            self.assertIsNone(case['expected_conclusion'])
            self.assertTrue(case['matches'])

    def test_missing_or_contradictory_expectations_rejected(self):
        for cell, value in [('E2', None), ('E2', '无结论'), ('E4', '错误的非空结论')]:
            with self.subTest(cell=cell, value=value):
                with self.assertRaisesRegex(EntryError, '预期|结论'):
                    review(edit_cell(self.data, 5, cell, value))

    def test_old_template_rejected_with_update_instructions(self):
        sheets = read_xlsx(self.data)
        for cells in sheets['对照用例'].values():
            cells.pop(5, None)
        with patch('rule_review.core.read_xlsx', return_value=sheets):
            with self.assertRaisesRegex(EntryError, '重新下载模板'):
                review(b'old-template')

    def test_generic_boolean_and_list_membership(self):
        sheets = read_xlsx(self.data)
        sheets['任务事实'][2].update({2: 'categories', 3: '列表', 4: ' A ;B'})
        sheets['规则条件'][2].update({2: 'categories', 3: 'contains', 4: '文本', 5: 'A'})
        snapshot = deepcopy(sheets)
        with patch('rule_review.core.read_xlsx', return_value=sheets):
            result = review(b'fixture')
        self.assertEqual(result['tasks'][0]['rules'][0]['status'], 'pass')
        self.assertEqual(sheets, snapshot)
        sheets['任务事实'][2].update({2: 'approved', 3: '布尔', 4: 'false'})
        sheets['规则条件'][2].update({2: 'approved', 3: 'eq', 4: '布尔', 5: 'true'})
        with patch('rule_review.core.read_xlsx', return_value=sheets):
            result = review(b'fixture')
        self.assertEqual(result['tasks'][0]['rules'][0]['status'], 'fail')

    def test_outputs_preserve_input_and_report(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            result = run_rule_review(self.data, '测试.xlsx', output_root=Path(temporary))
            out = Path(result['output'])
            self.assertEqual((out / 'input.xlsx').read_bytes(), self.data)
            self.assertEqual(json.loads((out / 'rule_report.json').read_text(encoding='utf-8')), result)
            self.assertIn('pages', (out / 'rule_summary.md').read_text(encoding='utf-8'))
            self.assertIn('用例总数：6；一致：6；不一致：0', (out / 'rule_summary.md').read_text(encoding='utf-8'))
            self.assertFalse(list(out.glob('*.ttl')))

    def test_markdown_records_conclusion_and_state_mismatches(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            data = edit_cell(self.data, 3, 'B2', '另一项需求')
            result = run_rule_review(data, 'changed.xlsx', output_root=Path(temporary))
            summary = (Path(result['output']) / 'rule_summary.md').read_text(encoding='utf-8')
            for value in ['用例总数：6；一致：4；不一致：2', 'C1（不一致）', 'C5（不一致）',
                          '预期状态：pass；实际状态：pass', '预期需求结论：归档能力',
                          '实际需求结论：另一项需求', '状态核对：一致；结论核对：不一致']:
                self.assertIn(value, summary)
            data = edit_cell(edit_cell(self.data, 5, 'D2', 'fail'), 5, 'E2', '无结论')
            result = run_rule_review(data, 'state.xlsx', output_root=Path(temporary))
            summary = (Path(result['output']) / 'rule_summary.md').read_text(encoding='utf-8')
            self.assertIn('预期状态：fail；实际状态：pass', summary)
            self.assertIn('预期需求结论：无结论', summary)
            self.assertIn('状态核对：不一致；结论核对：不一致', summary)

    def test_condition_limit_rejects_without_truncation(self):
        document = read_document(self.data)
        document['tasks'] *= 12000
        with patch('rule_review.core.read_document', return_value=document):
            with self.assertRaisesRegex(EntryError, '100000'):
                review(b'fixture')

    def test_empty_expectations_are_not_fabricated(self):
        sheets = read_xlsx(self.data)
        sheets['对照用例'] = {1: sheets['对照用例'][1]}
        with patch('rule_review.core.read_xlsx', return_value=sheets):
            result = review(b'fixture')
        self.assertEqual(result['cases'], [])
        self.assertEqual(result['case_validation'], {'status': 'not_run', 'total': 0, 'matched': 0, 'mismatched': 0})
        self.assertEqual(result['tasks'][0]['status'], 'matched')
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            with patch('rule_review.core.read_xlsx', return_value=sheets):
                result = run_rule_review(b'fixture', 'no-cases.xlsx', output_root=Path(temporary))
            summary = (Path(result['output']) / 'rule_summary.md').read_text(encoding='utf-8')
            self.assertIn('未登记对照用例；未验证', summary)

    def test_http_routes_upload_and_failure(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            server = HTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with patch('excel_entry.app.run_rule_review', side_effect=partial(run_rule_review, output_root=Path(temporary))):
                    self.assertIn('任务规则验证', urlopen(base + '/rules').read().decode())
                    self.assertEqual(urlopen(base + '/rules-example.xlsx').read(), self.data)
                    response = urlopen(Request(base + '/review-rules?sample.xlsx', data=self.data,
                                               headers={'Origin': base, 'Content-Type': 'application/octet-stream'}))
                    self.assertEqual(len(json.load(response)['cases']), 6)
                    changed = edit_cell(self.data, 3, 'B2', '另一项需求')
                    response = urlopen(Request(base + '/review-rules?changed.xlsx', data=changed, headers={'Origin': base}))
                    result = json.load(response)
                    self.assertEqual(result['case_validation']['mismatched'], 2)
                    summary = (Path(result['output']) / 'rule_summary.md').read_text(encoding='utf-8')
                    self.assertIn('C1（不一致）', summary)
                    for payload, origin, expected in [(b'bad', base, 400), (self.data, 'http://other.example', 403)]:
                        with self.assertRaises(HTTPError) as raised:
                            urlopen(Request(base + '/review-rules', data=payload, headers={'Origin': origin}))
                        self.assertEqual(raised.exception.code, expected)
                        raised.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
