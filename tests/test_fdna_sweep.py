"""Check scan propagation, isolation, protocol validation and exported evidence."""
from contextlib import redirect_stdout
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, load_model
from fdna.__main__ import main
from fdna.sweep import csv_report, sweep

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'data/fdna/arcane_case.json'


class FdnaSweepTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        self.protocol = json.loads((CASE.parent / 'base_sweep.json').read_text(encoding='utf-8'))
        self.protocol['values'] = [0, 50, 100]

    def test_indirect_propagation_and_isolated_branches(self):
        before = deepcopy((self.model, self.protocol))
        report = sweep(self.model, self.protocol)
        self.assertEqual((self.model, self.protocol), before)
        self.assertEqual(report['evaluations'], 18)
        self.assertEqual(len(report['rows']), 90)
        by_key = {(r['driver'], r['input'], r['observer']): r for r in report['rows']}
        for node, expected in {'S5': 34, 'S6': 48, 'F7': 72, 'F9': 98.98, 'F11': 98}.items():
            self.assertAlmostEqual(by_key['S1', 0, node]['value'], expected)
        self.assertFalse(any(e['feeder'] == 'S1' and e['receiver'] == 'F7' for e in self.model['edges']))
        for driver in ('S3', 'S8', 'S10'):
            for value in self.protocol['values']:
                self.assertEqual(by_key[driver, value, 'F7']['value'], 100)
        self.assertEqual(by_key['S8', 0, 'F9']['value'], 26)
        self.assertEqual(by_key['S10', 0, 'F11']['value'], 33)
        for row in report['rows']:
            if row['input'] == 100:
                self.assertEqual(row['value'], 100)
                self.assertEqual(row['loss'], 0)
        trace = next(t for t in report['traces'] if t['driver'] == 'S1' and t['input'] == 0)
        f7 = next(n for n in trace['nodes'] if n['id'] == 'F7')
        self.assertEqual([t['operability'] for t in f7['terms']], [34, 48])
        self.assertFalse(report['case_reproduction_ready'])
        changed = deepcopy(self.protocol)
        changed['values'] = [0, 25, 100]
        self.assertNotEqual(report['input_sha256'], sweep(self.model, changed)['input_sha256'])

    def test_s5_piecewise_constraint_switch_against_scalar_equations(self):
        self.protocol.update(drivers=['S1'], observers=['S5'], values=[0, 63, 64, 100])
        rows = sweep(self.model, self.protocol)['rows']
        for row in rows:
            x = row['input']
            self.assertAlmostEqual(row['value'], min((.21*x + 79 + 100 + 100)/3, x+34))
        self.assertEqual(rows[1]['binding_constraints'], ['cod:S1'])
        self.assertEqual(rows[2]['binding_constraints'], ['sod_mean'])

    def test_missing_unknown_and_invalid_protocols_rejected(self):
        invalid = [None, {}, {**self.protocol, 'model': 'implicit'},
                   {**self.protocol, 'drivers': ['S5']}, {**self.protocol, 'drivers': ['S1', 'S1']},
                   {**self.protocol, 'observers': ['missing']}, {**self.protocol, 'observers': []}]
        for values in ([0], [0, 0], [100, 0], [0, True], [0, float('nan')],
                       [0, float('inf')], [0, 10**500], [0, 101], '0,100'):
            invalid.append({**self.protocol, 'values': values})
        for baseline in ({'S1': 100}, {**self.protocol['baseline'], 'S1': None},
                         {**self.protocol['baseline'], 'S5': 100}):
            invalid.append({**self.protocol, 'baseline': baseline})
        for protocol in invalid:
            with self.subTest(protocol=protocol), self.assertRaises(ModelError):
                sweep(self.model, protocol)
        self.model['edges'][0]['alpha'] = None
        with self.assertRaises(ModelError):
            sweep(self.model, self.protocol)

    def test_complete_grid_monotonicity_and_csv_round_trip(self):
        self.protocol['values'] = list(range(101))
        report = sweep(self.model, self.protocol)
        self.assertEqual(report['evaluations'], 606)
        for driver in self.protocol['drivers']:
            for observer in self.protocol['observers']:
                values = [r['value'] for r in report['rows']
                          if r['driver'] == driver and r['observer'] == observer]
                self.assertTrue(all(a <= b for a, b in zip(values, values[1:])))
        rows = list(csv.DictReader(io.StringIO(csv_report(report))))
        self.assertEqual(len(rows), 3030)
        self.assertAlmostEqual(float(rows[2]['value']), 72)

    def test_cli_portable_exports_and_overwrite_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            # The portable case deliberately has no dependency on local Word files.
            self.assertTrue(all('path' not in s for s in self.model['sources']))
            protocol_path = folder / 'protocol.json'
            protocol_path.write_text(json.dumps(self.protocol), encoding='utf-8')
            args = ['sweep', str(CASE), '--protocol', str(protocol_path),
                    '--output-dir', str(folder / 'output')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
            report = json.loads((folder / 'output/fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['protocol'], self.protocol)
            self.assertEqual([s['status'] for s in report['source_verification']], ['not_checked'] * 2)
            self.assertEqual(len(list(csv.DictReader(io.StringIO(
                (folder / 'output/fdna_sweep.csv').read_text(encoding='utf-8'))))), 90)
            self.assertTrue((folder / 'output/protocol_snapshot.json').is_file())
            self.assertTrue((folder / 'output/case_snapshot.json').is_file())


if __name__ == '__main__':
    unittest.main()
