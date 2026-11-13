"""Verify measured SE conversion without inventing the article's preprocessing."""
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
from fdna.self_effectiveness import normalize_measurements, se_csv

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'data/fdna/arcane_case.json'


class SelfEffectivenessTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        self.calibration = json.loads((CASE.parent / 'se_calibration_check.json').read_text(encoding='utf-8'))
        self.measurements = json.loads((CASE.parent / 'se_measurements_check.json').read_text(encoding='utf-8'))

    def test_cost_benefit_elapsed_endpoints_and_no_mutation(self):
        before = deepcopy((self.model, self.calibration, self.measurements))
        result = normalize_measurements(self.model, self.calibration, self.measurements)
        self.assertEqual((self.model, self.calibration, self.measurements), before)
        self.assertEqual(result['counts'], {'records': 3, 'known_se': 33, 'unknown_se': 0})
        values = {n['id']: n['se'] for n in result['records'][0]['nodes']}
        self.assertEqual(values, dict(S1=75, D2=75, S3=30, D4=50, S5=80, S6=60,
                                      F7=50, S8=40, F9=25, S10=80, F11=75))
        d2 = next(n for n in result['records'][0]['nodes'] if n['id'] == 'D2')
        self.assertEqual(d2['indicator_value'], 5)
        self.assertEqual(d2['raw']['start'], 10)
        for record, endpoint in zip(result['records'][1:], (0, 100)):
            for node in record['nodes']:
                self.assertEqual(node['se'], 100-endpoint if node['rule']['direction'] == 'cost' else endpoint)
        self.assertFalse(result['parameter_fit_ready'])
        self.assertFalse(result['case_reproduction_ready'])
        self.assertEqual(result['data_kind'], 'method_check')
        self.assertEqual(set(result['complete_local_rows'].values()), {3})
        self.assertEqual(result['fit_inputs']['F7'][0]['feeder_values'], {'S5': 80, 'S6': 60})
        self.assertEqual(result['fit_inputs']['F7'][0]['receiver_value'], 50)

    def test_missing_measurements_block_only_incomplete_local_rows(self):
        self.measurements['records'] = self.measurements['records'][:1]
        observations = self.measurements['records'][0]['observations']
        del observations['S1']
        observations['D2']['end'] = None
        observations['F7'] = None
        result = normalize_measurements(self.model, self.calibration, self.measurements)
        self.assertEqual(result['status'], 'blocked')
        values = {n['id']: n for n in result['records'][0]['nodes']}
        self.assertIsNone(values['S1']['se'])
        self.assertIn('MISSING_MEASUREMENT', values['D2']['blockers'])
        self.assertEqual(values['S5']['se'], 80)  # Measured SE is not propagated operability.
        self.assertEqual(result['complete_local_rows'], {'S5': 0, 'S6': 0, 'F7': 0, 'F9': 1, 'F11': 1})

    def test_pending_calibration_never_falls_back_to_sample_extrema(self):
        pending = json.loads((CASE.parent / 'se_calibration_pending.json').read_text(encoding='utf-8'))
        result = normalize_measurements(self.model, pending, self.measurements)
        self.assertEqual(result['counts']['unknown_se'], 33)
        self.assertEqual(set(result['complete_local_rows'].values()), {0})
        self.assertTrue(all(n['blockers'] == ['UNKNOWN_CALIBRATION'] for r in result['records'] for n in r['nodes']))

    def test_invalid_calibration_and_provenance_shapes_rejected(self):
        for change in ('lower_equal', 'lower_greater', 'direction', 'missing_node', 'extra_node',
                       'nan', 'huge_integer', 'missing_basis', 'implicit_method'):
            calibration = deepcopy(self.calibration)
            rule = calibration['rules']['S1']
            if change == 'lower_equal': rule['upper'] = rule['lower']
            elif change == 'lower_greater': rule['lower'] = rule['upper']+1
            elif change == 'direction': rule['direction'] = 'benefit'
            elif change == 'missing_node': del calibration['rules']['S1']
            elif change == 'extra_node': calibration['rules']['missing'] = None
            elif change == 'nan': rule['upper'] = float('nan')
            elif change == 'huge_integer': rule['upper'] = 10**500
            elif change == 'missing_basis': del rule['basis']
            elif change == 'implicit_method': rule['method'] = 'infer_from_data'
            with self.subTest(change=change), self.assertRaises(ModelError):
                normalize_measurements(self.model, calibration, self.measurements)
        self.measurements['data_kind'] = 'article_verified_samples'
        with self.assertRaises(ModelError):
            normalize_measurements(self.model, self.calibration, self.measurements)

    def test_invalid_raw_values_units_forms_and_duplicate_ids_rejected(self):
        for change in ('out_of_bounds', 'reversed_time', 'unit', 'form', 'bool', 'infinite',
                       'negative', 'duplicate', 'unknown_node'):
            data = deepcopy(self.measurements)
            raw = data['records'][0]['observations']
            if change == 'out_of_bounds': raw['S1']['value'] = 101
            elif change == 'reversed_time': raw['D2']['end'] = 9
            elif change == 'unit': raw['S1']['unit'] = 'other_scale'
            elif change == 'form': raw['D2'] = {'value': 5, 'unit': 'time_scale'}
            elif change == 'bool': raw['S1']['value'] = True
            elif change == 'infinite': raw['S1']['value'] = float('inf')
            elif change == 'negative': raw['S1']['value'] = -1
            elif change == 'duplicate': data['records'][1]['id'] = data['records'][0]['id']
            elif change == 'unknown_node': raw['absent'] = None
            with self.subTest(change=change), self.assertRaises(ModelError):
                normalize_measurements(self.model, self.calibration, data)

    def test_wide_finite_bounds_and_input_digest(self):
        report = normalize_measurements(self.model, self.calibration, self.measurements)
        self.calibration['rules']['S1']['upper'] = 1e308
        self.measurements['records'][0]['observations']['S1']['value'] = 5e307
        changed = normalize_measurements(self.model, self.calibration, self.measurements)
        self.assertEqual(changed['records'][0]['nodes'][0]['se'], 50)
        self.assertNotEqual(changed['input_sha256'], report['input_sha256'])

    def test_csv_cli_unknown_status_and_refusal_to_overwrite(self):
        result = normalize_measurements(self.model, self.calibration, self.measurements)
        rows = list(csv.DictReader(io.StringIO(se_csv(result))))
        self.assertEqual(len(rows), 33)
        self.assertEqual(float(rows[0]['se']), 75)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for suffix, expected in (('check', 0), ('pending', 1)):
                args = ['normalize', str(CASE), '--calibration', str(CASE.parent / f'se_calibration_{suffix}.json'),
                        '--measurements', str(CASE.parent / 'se_measurements_check.json'),
                        '--output-dir', str(folder / suffix)]
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(args), expected)
                    self.assertEqual(main(args), 2)
                exported = json.loads((folder / suffix / 'fdna_report.json').read_text(encoding='utf-8'))
                self.assertEqual(exported['data_kind'], 'method_check')
                self.assertEqual(exported['measurements_snapshot'], self.measurements)
                self.assertTrue((folder / suffix / 'se_values.csv').is_file())


if __name__ == '__main__':
    unittest.main()
