"""Real combined workbook: source fidelity, binding effects and strict export."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from design.engine import design
from design.excel import read_design
from excel_entry.app import ROOT, run_design, run_import
from excel_entry.importer import EntryError, convert, read_xlsx
from excel_entry.unified import convert_design, SOURCE_SHEETS


class UnifiedExcelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = (ROOT / 'excel_entry/templates/案例设计演示.xlsx').read_bytes()
        cls.sheets = read_xlsx(cls.data)

    def test_original_article_values_preserved_and_design_unconfigured(self):
        data = (ROOT / 'excel_entry/templates/案例示例.xlsx').read_bytes()
        sheets = read_xlsx(data)
        expected = json.loads((ROOT / 'data/article_source/case_records.json').read_text(encoding='utf-8'))
        self.assertEqual(convert(sheets), expected)
        with self.assertRaisesRegex(EntryError, '设计配置尚未填写'):
            read_design(data)
        # Old workbooks keep source-query compatibility, with an explicit error
        # when someone attempts to use them directly as a design input.
        old = {name: rows for name, rows in sheets.items() if name in SOURCE_SHEETS}
        self.assertEqual(convert(old), expected)
        with self.assertRaisesRegex(EntryError, '旧版来源Excel'):
            convert_design(old)

    def test_same_real_workbook_queries_and_designs_with_saved_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            source = run_import(self.data, 'combined.xlsx', output_root=Path(directory)/'source')
            result = run_design(self.data, 'combined.xlsx', output_root=Path(directory)/'design')
            self.assertTrue(source['records'])
            self.assertEqual(result['report']['status'], 'complete')
            self.assertEqual(result['rdf_export']['status'], 'exported')
            self.assertEqual(len(result['report']['network']['chains']), 2)
            self.assertEqual(result['report']['network']['score'], .5)
            self.assertEqual(len(result['source_bridge']['bindings']), 10)
            snapshot = json.loads((Path(result['output'])/'source_bindings.json').read_text(encoding='utf-8'))
            self.assertEqual(snapshot['source_case'], convert(self.sheets))
            self.assertEqual((Path(result['output'])/'input.xlsx').read_bytes(), self.data)
            self.assertTrue((Path(result['output'])/'design.ttl').exists())

    def test_source_edit_changes_bound_value_and_actual_chain_feasibility(self):
        sheets = deepcopy(self.sheets)
        before, _ = convert_design(sheets)
        self.assertEqual(design(before)['status'], 'complete')
        sheets['探测能力'][2][7] = 0
        after, report = convert_design(sheets)
        eye = next(a for a in after['artifacts'] if a['id']=='demo-eye')
        self.assertEqual(eye['attributes']['max_range'], {'value':0,'unit':'nmi'})
        self.assertEqual(design(after)['status'], 'no_feasible_design')
        self.assertEqual(report['source_case']['table8'][0]['detection_range_nm'], 0)
        self.assertEqual(before['artifacts'][0]['attributes']['max_range']['value'], 100)

    def test_binding_uses_identity_across_row_reordering_and_keeps_units(self):
        before, _ = convert_design(self.sheets)
        sheets = deepcopy(self.sheets)
        sheets['作用能力'][2], sheets['作用能力'][3] = sheets['作用能力'][3], sheets['作用能力'][2]
        after, _ = convert_design(sheets)
        self.assertEqual(before, after)
        sheets['来源取值'][2][2] = ' demo-task-1 '
        spaced, _ = convert_design(sheets)
        self.assertEqual(spaced, after)
        for row in sheets['案例说明'].values():
            if row.get(1)=='单位' and row.get(2)=='距离':
                row[3]='km'
        doc, _ = convert_design(sheets)
        eye = next(a for a in doc['artifacts'] if a['id']=='demo-eye')
        self.assertEqual(eye['attributes']['max_range'], {'value':100,'unit':'km'})
        self.assertEqual(design(doc)['status'], 'blocked')

    def test_consumed_manual_issue_and_automatic_bounds_block_design(self):
        for manual in (True, False):
            sheets = deepcopy(self.sheets)
            if manual:
                sheets['疑点'][2] = dict(enumerate(['SOURCE_TEST','demo-eye','距离待确认','待核对',None,
                    'technical-fixture','探测能力::demo-platform::探测距离（n mile）'],1))
            else:
                sheets['作用能力'][2][5] = 1000
            doc, bridge = convert_design(sheets)
            self.assertEqual(design(doc)['status'], 'blocked')
            self.assertTrue(any(binding['blocking_issue_ids'] for binding in bridge['bindings']))

    def test_duplicate_mapping_missing_source_and_unknown_extension_fail(self):
        for action in ('duplicate','dangling','extra','missing'):
            sheets = deepcopy(self.sheets)
            if action=='duplicate':
                rows=sheets['来源取值']; rows[max(rows)+1]=deepcopy(rows[2])
            elif action=='dangling':
                sheets['来源取值'][2][6]='does-not-exist'
            elif action=='extra':
                sheets['未知表']={}
            else:
                del sheets['来源取值']
            with self.subTest(action=action), self.assertRaises(EntryError):
                convert_design(sheets)

    def test_binding_cannot_overwrite_manual_attribute(self):
        sheets=deepcopy(self.sheets)
        rows=sheets['属性']
        rows[max(rows)+1]=dict(enumerate(['法器','demo-eye',None,'max_range','数值',999,'nmi'],1))
        with self.assertRaisesRegex(EntryError, '属性重复'):
            convert_design(sheets)

    def test_unknown_source_unit_is_carried_as_a_blocker(self):
        sheets=deepcopy(self.sheets)
        # Consume an explicitly unconfirmed source quantity without inventing
        # a unit or treating an unknown physical quantity as dimensionless.
        for row in sheets['案例说明'].values():
            if row.get(1)=='单位' and row.get(2)=='作用值':
                row[3]='原表未给物理单位'
        rows=sheets['来源取值']
        rows[max(rows)+1]=dict(enumerate(['法器','demo-fast',None,'power','作用能力','demo-fast','作用值（单位待确认）'],1))
        doc, bridge=convert_design(sheets)
        self.assertEqual(design(doc)['status'],'blocked')
        self.assertTrue(any('source-unit-unconfirmed' in issue for binding in bridge['bindings'] for issue in binding['blocking_issue_ids']))


if __name__=='__main__':
    unittest.main()
