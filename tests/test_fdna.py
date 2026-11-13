"""Independent formula, source-transcription, unknown and CLI checks for FDNA."""
from contextlib import redirect_stdout
from copy import deepcopy
from decimal import Decimal, localcontext
import hashlib
import io
import json
from pathlib import Path
import random
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from fdna import ModelError, evaluate, inspect_model, load_model, validate_model
from fdna.__main__ import main
from fdna.engine import MODEL
from fdna.model import verify_sources

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'data/fdna/arcane_case.json'


class FdnaTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        self.intact = json.loads((CASE.parent / 'intact.json').read_text(encoding='utf-8'))

    def test_case_matches_revised_document_tables_and_stage1_audit(self):
        structure = inspect_model(self.model)
        self.assertEqual(structure['counts'], dict(subjects=8, targets=3, nodes=11, edges=18, chains=2))
        self.assertEqual(structure['exogenous_nodes'], ['S1', 'D2', 'S3', 'D4', 'S8', 'S10'])
        audit_path = ROOT / 'docs/fdna-reproduction/source_audit.json'
        word = ROOT / '新建 DOCX 文档 (2).docx'
        if not audit_path.is_file() or not word.is_file():
            self.skipTest('Local source documents are not distributed with the code')
        audit = json.loads(audit_path.read_text(encoding='utf-8'))
        self.assertEqual([(e['feeder'], e['receiver'], e['alpha'], e['beta']) for e in self.model['edges']],
                         [(e['feeder'], e['receiver'], e['alpha'], e['beta'])
                          for e in audit['reported_parameters_verified_against_table7']])
        source = next(s for s in self.model['sources'] if s['id'] == 'fantasy')
        self.assertEqual(hashlib.sha256(word.read_bytes()).hexdigest(), source['sha256'])
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
              'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math'}
        with zipfile.ZipFile(word) as archive:
            root = ET.fromstring(archive.read('word/document.xml'))
        tags = {f'{{{uri}}}t' for uri in ns.values()}
        def text(node):
            return ''.join(n.text or '' for n in node.iter() if n.tag in tags).strip()
        tables = [[[text(c) for c in row.findall('w:tc', ns)] for row in table.findall('w:tr', ns)]
                  for table in root.findall('.//w:tbl', ns)]
        for node, row in zip(self.model['nodes'], tables[4][1:]):
            self.assertEqual(node['indicator']['label'], row[1])
        revised_pairs = {}
        for row, feeder in zip(tables[6][1:10], ['S1', 'D2', 'S3', 'D4', 'S5', 'S6', 'F7', 'S8', 'S10']):
            for receiver, value in zip(['S5', 'S6', 'F7', 'F9', 'F11'], row[1:]):
                if value != '—':
                    alpha, beta = re.fullmatch(r'\((\d+\.\d+),\s*(\d+)\)', value).groups()
                    revised_pairs[feeder, receiver] = float(alpha), int(beta)
        self.assertEqual(revised_pairs, {(e['feeder'], e['receiver']): (e['alpha'], e['beta']) for e in self.model['edges']})
        self.assertEqual([float(value) for value in tables[6][-1][1:]],
                         [item['pseudo_r_squared'] for item in self.model['reported_fit']])
        self.assertEqual(self.model['chains'][0]['members'], ['S1', 'D2', 'D4', ['S5', 'S6'], 'F7'])
        self.assertEqual(self.model['chains'][1]['members'], [[['S8', 'S10'], 'S3'], ['F9', 'F11']])
        owners = {n['id']: n['subject'] for n in self.model['nodes']}
        self.assertEqual(owners['S3'], owners['D4'])
        self.assertEqual(owners['S8'], owners['F9'])

    def test_intact_reference_no_delta_fallback_no_mutation(self):
        before = deepcopy((self.model, self.intact))
        report = evaluate(self.model, self.intact)
        self.assertEqual((self.model, self.intact), before)
        self.assertEqual(report['status'], 'complete')
        self.assertTrue(all(n['value'] == 100 for n in report['nodes']))
        self.assertFalse(report['case_reproduction_ready'])
        self.assertTrue(all(value is None for value in report['case_extension']['delta'].values()))
        self.assertEqual(report['policies']['delta'], 'not_in_base_equations')
        report['case_extension']['delta']['S5'] = 50
        report['source_snapshot'][0]['reference'] = 'changed'
        self.assertEqual((self.model, self.intact), before)

    def test_two_layer_propagation_against_hand_calculation(self):
        scenario = json.loads((CASE.parent / 'hand_check.json').read_text(encoding='utf-8'))
        report = evaluate(self.model, scenario)
        nodes = {n['id']: n for n in report['nodes']}
        expected = {'S5': 34, 'S6': 48, 'F7': 72, 'F9': 98.98, 'F11': 98}
        for node, value in expected.items():
            self.assertAlmostEqual(nodes[node]['value'], value, places=12)
        self.assertAlmostEqual(nodes['S5']['sod_mean'], 93)
        self.assertAlmostEqual(nodes['F7']['sod_mean'], 79.49)
        self.assertEqual(nodes['F7']['binding_constraints'], ['cod:S5'])
        self.assertEqual(nodes['F9']['binding_constraints'], ['sod_mean'])
        self.assertEqual(nodes['F11']['binding_constraints'], ['cod:S5'])
        self.assertEqual(nodes['F7']['terms'][0]['operability'], 34)
        self.assertEqual(nodes['F7']['terms'][0]['source_refs'], ['paper:table7', 'fantasy:table7'])

    def test_randomized_cases_against_independent_high_precision_equations(self):
        rng = random.Random(8613)
        # Decimal oracle uses the published expanded expression, not the engine's stable form.
        with localcontext() as context:
            context.prec = 50
            for _ in range(40):
                model, scenario = deepcopy(self.model), deepcopy(self.intact)
                for edge in model['edges']:
                    edge['alpha'] = rng.randrange(101) / 100
                    edge['beta'] = rng.randrange(101)
                scenario['node_values'] = {n: rng.randrange(10001) / 100 for n in scenario['node_values']}
                report = evaluate(model, scenario)
                oracle = {n: Decimal(str(v)) for n, v in scenario['node_values'].items()}
                pending = set(n['id'] for n in model['nodes']) - oracle.keys()
                while pending:
                    ready = [n for n in pending if all(e['feeder'] in oracle for e in model['edges'] if e['receiver'] == n)]
                    self.assertTrue(ready)
                    for n in ready:
                        edges = [e for e in model['edges'] if e['receiver'] == n]
                        sod = sum(Decimal(str(e['alpha'])) * oracle[e['feeder']] +
                                  Decimal(100) * (1 - Decimal(str(e['alpha']))) for e in edges) / len(edges)
                        cod = min(oracle[e['feeder']] + Decimal(str(e['beta'])) for e in edges)
                        oracle[n] = min(sod, cod)
                        pending.remove(n)
                for node in report['nodes']:
                    self.assertAlmostEqual(node['value'], float(oracle[node['id']]), places=11)
                    self.assertTrue(0 <= node['value'] <= 100)

    def test_zero_one_alpha_and_zero_hundred_beta(self):
        self.intact['node_values']['S1'] = 0
        first = self.model['edges'][0]
        for alpha, beta, expected in ((0, 100, 100), (1, 100, 200/3), (0, 0, 0), (1, 0, 0)):
            with self.subTest(alpha=alpha, beta=beta):
                first.update(alpha=alpha, beta=beta)
                result = {n['id']: n for n in evaluate(self.model, self.intact)['nodes']}['S5']
                self.assertAlmostEqual(result['value'], expected)
        # alpha=0 does not erase a beta=0 restriction; beta=100 does not erase SoD.

    def test_missing_and_null_values_propagate_without_zero_fill(self):
        for missing in (False, True):
            scenario = deepcopy(self.intact)
            if missing:
                del scenario['node_values']['S1']
            else:
                scenario['node_values']['S1'] = None
            report = evaluate(self.model, scenario)
            nodes = {n['id']: n for n in report['nodes']}
            self.assertEqual(report['status'], 'blocked')
            for node in ('S1', 'S5', 'S6', 'F7', 'F9', 'F11'):
                self.assertIsNone(nodes[node]['value'])
            self.assertEqual(nodes['S3']['value'], 100)
            self.assertEqual(nodes['S5']['blockers'][0]['feeder'], 'S1')

    def test_unknown_edge_parameter_blocks_only_its_dependents(self):
        self.model['edges'][0]['alpha'] = None
        self.assertFalse(inspect_model(self.model)['base_formula_ready'])
        nodes = {n['id']: n for n in evaluate(self.model, self.intact)['nodes']}
        self.assertIsNone(nodes['S5']['value'])
        self.assertEqual(nodes['S6']['value'], 100)
        self.assertIsNone(nodes['F7']['value'])
        self.assertEqual(nodes['S5']['blockers'][0]['code'], 'UNKNOWN_PARAMETER')

    def test_invalid_numeric_values_and_intermediate_overrides(self):
        for value in (True, -1, 101, 10**500, float('nan'), float('inf'), '50', []):
            with self.subTest(value=value):
                scenario = deepcopy(self.intact)
                scenario['node_values']['S1'] = value
                with self.assertRaises(ModelError):
                    evaluate(self.model, scenario)
        for node in ('S5', 'unknown'):
            scenario = deepcopy(self.intact)
            scenario['node_values'][node] = 10
            with self.assertRaises(ModelError):
                evaluate(self.model, scenario)

    def test_invalid_structure_references_parameters_and_cycles(self):
        for change in ('duplicate', 'self', 'dangling', 'source', 'owner', 'chain', 'alpha', 'beta', 'delta', 'cycle', 'nan'):
            with self.subTest(change=change):
                model = deepcopy(self.model)
                if change == 'duplicate': model['edges'].append(deepcopy(model['edges'][0]))
                elif change == 'self': model['edges'][0]['receiver'] = 'S1'
                elif change == 'dangling': model['edges'][0]['feeder'] = 'absent'
                elif change == 'source': model['edges'][0]['source_refs'] = ['paper:absent']
                elif change == 'owner': model['nodes'][0]['subject'] = 'absent'
                elif change == 'chain': model['chains'][1]['members'][0][0][1] = 'absent'
                elif change == 'alpha': model['edges'][0]['alpha'] = 1.01
                elif change == 'beta': model['edges'][0]['beta'] = -1
                elif change == 'delta': model['case_extension']['delta']['S5'] = 0
                elif change == 'nan': model['edges'][0]['alpha'] = float('nan')
                elif change == 'cycle': model['edges'][0]['feeder'] = 'F7'
                with self.assertRaises(ModelError):
                    validate_model(model)

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'duplicate.json'
            path.write_text('{"id":"first","id":"second"}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '重复'):
                load_model(path)

    def test_source_revision_check(self):
        checks = verify_sources(self.model, CASE)
        self.assertEqual([item['status'] for item in checks], ['not_checked', 'not_checked'])
        with tempfile.TemporaryDirectory() as directory:
            source_file = Path(directory) / 'source.txt'
            source_file.write_bytes(b'source revision fixture')
            source = self.model['sources'][1]
            source['path'] = str(source_file)
            source['sha256'] = hashlib.sha256(source_file.read_bytes()).hexdigest()
            self.assertEqual(verify_sources(self.model, CASE)[1]['status'], 'matched')
            source_file.write_bytes(b'changed revision fixture')
            self.assertEqual(verify_sources(self.model, CASE)[1]['status'], 'changed')
            source_file.unlink()
            self.assertEqual(verify_sources(self.model, CASE)[1]['status'], 'unavailable')

    def test_cli_exports_snapshots_and_distinguishes_blocked_from_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name, code in (('intact', 0), ('unknown', 1)):
                captured = io.StringIO()
                args = ['evaluate', str(CASE), '--scenario', str(CASE.parent / f'{name}.json'),
                        '--model', MODEL, '--output-dir', str(folder / name)]
                with redirect_stdout(captured):
                    self.assertEqual(main(args), code)
                result = json.loads(captured.getvalue())
                report = json.loads((folder / name / 'fdna_report.json').read_text(encoding='utf-8'))
                self.assertEqual(result, report)
                self.assertFalse(report['case_reproduction_ready'])
                self.assertTrue((folder / name / 'case_snapshot.json').is_file())
                captured = io.StringIO()
                with redirect_stdout(captured):
                    self.assertEqual(main(args), 2)  # Refuse to replace an existing result.
            changed = deepcopy(self.model)
            changed['sources'][1]['sha256'] = '0' * 64
            changed['sources'][1]['path'] = str(ROOT / '新建 DOCX 文档 (2).docx')
            path = folder / 'changed.json'
            path.write_text(json.dumps(changed, ensure_ascii=False), encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['inspect', str(path)]), 2)


if __name__ == '__main__':
    unittest.main()
