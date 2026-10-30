"""Entry contract, source fidelity, live HTTP and project backend integration."""
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
import zipfile

from excel_entry.app import Handler, ROOT, run_import
from excel_entry.importer import EntryError, NS, convert, read_xlsx
from http.server import HTTPServer

EXAMPLE = ROOT / 'excel_entry/templates/案例示例.xlsx'


def change_cell(data, *, formula=False, value='12'):
    """Mutate a disposable test copy, not an authored deliverable."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, 'w') as dst:
        for item in src.infolist():
            content = src.read(item.filename)
            if item.filename == 'xl/worksheets/sheet3.xml':
                tree = ET.fromstring(content)
                cell = next(c for c in tree.findall('s:sheetData/s:row/s:c', NS) if c.attrib['r'] == 'D2')
                cell.attrib.pop('t', None)
                for child in list(cell):
                    cell.remove(child)
                if formula:
                    ET.SubElement(cell, '{' + NS['s'] + '}f').text = '6+6'
                ET.SubElement(cell, '{' + NS['s'] + '}v').text = value
                content = ET.tostring(tree)
            dst.writestr(item, content)
    return out.getvalue()


class ExcelEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = EXAMPLE.read_bytes()
        cls.sheets = read_xlsx(cls.data)

    def test_example_roundtrip_all_source_values_and_metadata(self):
        expected = json.loads((ROOT / 'data/article_source/case_records.json').read_text(encoding='utf-8'))
        self.assertEqual(convert(self.sheets), expected)

    def test_blank_has_no_borrowed_case_records(self):
        sheets = read_xlsx((EXAMPLE.parent / '空白模板.xlsx').read_bytes())
        self.assertEqual(len(sheets['对象清单']), 1)
        self.assertEqual(len(sheets['疑点']), 1)
        with self.assertRaises(EntryError):
            convert(sheets)

    def test_missing_and_extra_header_rejected(self):
        for operation in ('missing', 'extra'):
            s = copy.deepcopy(self.sheets)
            if operation == 'missing':
                del s['作用能力'][1][4]
            else:
                s['作用能力'][1][13] = '其他'
            with self.subTest(operation=operation), self.assertRaisesRegex(EntryError, '第1行列名'):
                convert(s)

    def test_missing_number_is_not_zero(self):
        s = copy.deepcopy(self.sheets)
        del s['作用能力'][2][4]
        with self.assertRaisesRegex(EntryError, '作用能力第2行.*空白'):
            convert(s)
        s['作用能力'][2][4] = 0
        self.assertEqual(convert(s)['table9'][0]['max_range_nm'], 0)

    def test_numeric_text_duplicate_id_probability_and_count(self):
        for sheet, row, col, value, message in [
            ('作用能力', 2, 4, '12nm', '填写数字'),
            ('作用能力', 2, 12, 101, '概率须'),
            ('作用能力', 3, 2, 'R-Sigil-A', '编号重复'),
            ('对象清单', 2, 4, 1.5, '非负整数'),
        ]:
            s = copy.deepcopy(self.sheets)
            s[sheet][row][col] = value
            with self.subTest(value=value), self.assertRaisesRegex(EntryError, message):
                convert(s)

    def test_reorder_preserves_issue_identity(self):
        s = copy.deepcopy(self.sheets)
        s['作用能力'][2], s['作用能力'][5] = s['作用能力'][5], s['作用能力'][2]
        d = convert(s)
        issue = next(i for i in d['issues'] if i['code'] == 'RANGE_ORDER' and i['record'] == 'R-Sigil-D')
        self.assertIn('table9[0].max_range_nm', issue['field_paths'])
        self.assertEqual(d['table9'][0]['manifestation_artifact'], 'R-Sigil-D')

    def test_renamed_id_exposes_dangling_manual_issue(self):
        s = copy.deepcopy(self.sheets)
        s['作用能力'][5][2] = 'NEW-ID'
        with self.assertRaisesRegex(EntryError, '关联字段不存在'):
            convert(s)

    def test_formula_and_non_xlsx_rejected(self):
        with self.assertRaisesRegex(EntryError, 'D2.*不要使用公式'):
            read_xlsx(change_cell(self.data, formula=True))
        with self.assertRaises(EntryError):
            read_xlsx(b'not a workbook')

    def test_undeclared_issue_source_rejected(self):
        s = copy.deepcopy(self.sheets)
        s['疑点'][2][6] = 'missing.png'
        with self.assertRaisesRegex(EntryError, '来源文件未在案例说明中登记'):
            convert(s)

    def test_single_task_limit(self):
        s = copy.deepcopy(self.sheets)
        s['任务'][3] = dict(s['任务'][2])
        s['任务'][3][1] = 'another'
        with self.assertRaisesRegex(EntryError, '只支持一条'):
            convert(s)

    def test_live_import_changed_cell_reaches_rdf_query(self):
        original = (ROOT / 'data/article_source/case_records.json').read_bytes()
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
            result = run_import(change_cell(self.data, value='12'), 'changed.xlsx', output_root=Path(directory))
            self.assertEqual(result['backend_module'], 'source_data.cli')
            self.assertEqual(result['backend_path'], str(ROOT / 'source_data'))
            self.assertEqual(result['report']['status'], 'valid')
            self.assertEqual(result['report']['counts']['records'], 18)
            record = next(r for r in result['records'] if r['original_code'] == 'R-Sigil-A')
            field = next(f for f in record['fields'] if f['field']['key'] == 'max_range_nm')
            self.assertEqual(field['raw_value'], 12)
            self.assertEqual(field['display_label'], '最大作用距离（n mile）')
            self.assertTrue((Path(result['output']) / 'results/provenance.ttl').exists())
            self.assertEqual(result['instance_report']['status'], 'valid')
            from rdflib import Graph, URIRef
            from adapters.case_instances import KW
            instance = next(e for e in record['instances'] if e['code'] == 'R-Sigil-A')
            graph = Graph().parse(Path(result['output']) / 'results/instances.ttl', format='turtle')
            self.assertEqual(float(graph.value(URIRef(instance['iri']), KW.maxRangeNm)), 12)
            self.assertTrue((Path(result['output']) / 'results/instance_report.json').exists())
            combined = Graph().parse(Path(result['output']) / 'results/ontology_with_instances.ttl', format='turtle')
            from rdflib import RDF, OWL
            self.assertIn((KW.InsightArtifact, RDF.type, OWL.Class), combined)
            self.assertIn((URIRef(instance['iri']), RDF.type, KW.ManifestationArtifact), combined)
        self.assertEqual(original, (ROOT / 'data/article_source/case_records.json').read_bytes())

    def test_http_upload_and_validation_failure(self):
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base + '/example.xlsx') as response:
                self.assertEqual(response.read(), self.data)
            with urlopen(Request(base + '/import?sample.xlsx', data=self.data, headers={'Content-Type': 'application/octet-stream', 'Origin': base})) as response:
                result = json.load(response)
                self.assertEqual(len(result['records']), 18)
            with self.assertRaises(HTTPError) as ctx:
                urlopen(Request(base + '/import', data=b'invalid'))
            self.assertEqual(ctx.exception.code, 400)
            ctx.exception.close()
            with self.assertRaises(HTTPError) as ctx:
                urlopen(Request(base + '/import', data=self.data, headers={'Origin': 'https://example.com'}))
            self.assertEqual(ctx.exception.code, 403)
            ctx.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
