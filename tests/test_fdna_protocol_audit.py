"""Explicit assignments, uncertainty envelopes and reproducible figure comparisons."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, load_model
from fdna.__main__ import main
from fdna.protocol_audit import audit_protocols


CASE = Path(__file__).resolve().parents[1]/'data/fdna/arcane_case.json'


class ProtocolAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_model(CASE)
        cls.protocol = json.loads(CASE.with_name('intervention_sweep.json').read_text(encoding='utf-8'))
        cls.readings = json.loads(CASE.with_name('figure_readings.json').read_text(encoding='utf-8'))
        before = deepcopy((cls.model, cls.protocol, cls.readings))
        cls.report = audit_protocols(cls.model, cls.protocol, cls.readings)
        assert (cls.model, cls.protocol, cls.readings) == before

    def synthetic(self, driver='S1', observer='F7'):
        return {'id': 'synthetic_protocol_test', 'data_kind': 'uncertain_raster_readings',
                'source_sha256': self.model['sources'][0]['sha256'], 'method': 'synthetic test pixels',
                'axis_mapping': {'kind': 'candidate_identity', 'basis': 'test assumption'},
                'panels': [{'id': 'test', 'observer': observer, 'pdf_page': 1, 'image_xref': 1,
                            'image_sha256': '0'*64, 'size': [101, 101],
                            'x_axis': [[0, 0], [100, 100]], 'y_axis': [[0, 100], [100, 0]],
                            'legend': [driver]}],
                'observations': [{'panel': 'test', 'driver': driver, 'pixel': [50, 3.195], 'pixel_error': [.01, .01]}]}

    def test_nominal_counts_match_previous_independent_audit(self):
        candidates = self.report['candidate_results']
        self.assertEqual([c['nominal_counts']['overlap'] for c in candidates], [18, 17, 8, 7])
        self.assertEqual(self.report['summary']['points'], 31)
        self.assertFalse(self.report['case_reproduction_ready'])
        for c in candidates:
            self.assertGreaterEqual(c['rounding_counts']['overlap'], c['nominal_counts']['overlap'])
            self.assertEqual(sum(c['nominal_counts'].values()), 31)
            self.assertEqual(len(c['rounding_separated_point_indices']), c['rounding_counts']['separated'])

    def test_distinct_protocols_by_independent_scalar_calculation(self):
        report = audit_protocols(self.model, self.protocol, self.synthetic())
        actual = [c['comparisons'][0]['nominal_prediction'] for c in report['candidate_results']]
        # At S1=50: S5=84, S6=97, so F7=(94.72+98.89)/2.
        self.assertAlmostEqual(actual[0], (94.72+98.89)/2)
        self.assertEqual(actual[1], 100)  # Clamped S5/S6 block the indirect path.
        # All leaves=50: S5=84, S6=100-(.18+.23+.51)*50/3.
        s6 = 100-(.18+.23+.51)*50/3
        self.assertAlmostEqual(actual[2], (100-.33*16+100-.37*(100-s6))/2)
        self.assertAlmostEqual(actual[3], 100-(.33+.37)*50/2)

    def test_direct_intermediate_scan_suspends_its_incoming_equation(self):
        readings = self.synthetic('S5', 'F7')
        for c in audit_protocols(self.model, self.protocol, readings)['candidate_results'][:2]:
            self.assertAlmostEqual(c['comparisons'][0]['nominal_prediction'], min(100-.33*50/2, 50+38))

    def test_rounding_envelope_and_read_errors_are_retained(self):
        for c in self.report['candidate_results']:
            for row in c['comparisons']:
                lo, hi = row['rounding_interval']
                a, b = row['nominal_interval']
                self.assertLessEqual(lo, a+1e-9)
                self.assertLessEqual(b, hi+1e-9)
                self.assertGreater(row['read_interval'][1], row['read_interval'][0])
                self.assertGreater(row['t_interval'][1], row['t_interval'][0])
                if row['rounding_status'] == 'separated':
                    self.assertGreater(row['rounding_interval_gap'], 0)
        self.assertTrue(any('同一套参数' in text for text in self.report['limits']))

    def test_synchronous_candidates_ignore_driver_identity(self):
        readings = self.synthetic()
        readings['panels'][0]['legend'].append('D2')
        readings['observations'].append({**deepcopy(readings['observations'][0]), 'driver': 'D2'})
        candidates = audit_protocols(self.model, self.protocol, readings)['candidate_results']
        for c in candidates[2:]:
            self.assertFalse(c['driver_distinguished'])
            a, b = c['comparisons']
            self.assertEqual(a['nominal_interval'], b['nominal_interval'])
            self.assertEqual(a['rounding_interval'], b['rounding_interval'])
        self.assertNotEqual(candidates[0]['comparisons'][0]['nominal_prediction'],
                            candidates[0]['comparisons'][1]['nominal_prediction'])

    def test_invalid_inputs_and_self_observation_rejected(self):
        protocol = deepcopy(self.protocol)
        protocol['baseline']['S1'] = 99
        with self.assertRaises(ModelError):
            audit_protocols(self.model, protocol, self.readings)
        for readings in [self.synthetic('S5', 'S5'), self.synthetic('D2', 'S1'),
                         {**self.readings, 'source_sha256': 'f'*64}]:
            with self.assertRaises(ModelError):
                audit_protocols(self.model, self.protocol, readings)

    def test_cli_exports_and_protects_readings(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = folder/'readings.json'
            text = json.dumps(self.synthetic())
            path.write_text(text, encoding='utf-8')
            args = ['protocol-audit', str(CASE), '--protocol', str(CASE.with_name('intervention_sweep.json')),
                    '--readings', str(path), '--output-dir', str(folder/'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
            saved = json.loads((folder/'out/fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['readings_snapshot'], self.synthetic())
            self.assertEqual(saved['input_sha256'], audit_protocols(self.model, self.protocol, self.synthetic())['input_sha256'])
            self.assertEqual(path.read_text(encoding='utf-8'), text)


if __name__ == '__main__':
    unittest.main()
