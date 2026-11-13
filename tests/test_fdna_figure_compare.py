"""Uncertain readings are compared conditionally, with both axis errors retained."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, load_model
from fdna.__main__ import main
from fdna.figure_compare import compare_readings

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'data/fdna/arcane_case.json'


class FigureComparisonTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        self.protocol = json.loads((CASE.parent / 'base_sweep.json').read_text(encoding='utf-8'))
        # Synthetic pixels for testing the comparator, not newly read paper values.
        self.readings = {
            'id': 'synthetic_test_pixels', 'data_kind': 'uncertain_raster_readings',
            'source_sha256': self.model['sources'][0]['sha256'], 'method': 'synthetic fixture',
            'axis_mapping': {'kind': 'candidate_identity', 'basis': 'explicit test assumption'},
            'panels': [{'id': 'test', 'observer': 'S5', 'pdf_page': 1, 'image_xref': 1,
                        'image_sha256': '0'*64, 'size': [101, 101], 'x_axis': [[0, 0], [100, 100]],
                        'y_axis': [[0, 100], [100, 0]], 'legend': ['S1', 'S5']}],
            'observations': [{'panel': 'test', 'driver': 'S1', 'pixel': [50, 15.5], 'pixel_error': [2, 1]}],
        }

    def test_scalar_calibration_and_horizontal_error_propagation(self):
        before = deepcopy((self.model, self.protocol, self.readings))
        report = compare_readings(self.model, self.protocol, self.readings)
        self.assertEqual((self.model, self.protocol, self.readings), before)
        result = report['comparisons'][0]
        self.assertEqual(result['axis_t'], 50)
        self.assertEqual(result['read_value'], 84.5)
        self.assertEqual(result['read_error'], 1)
        self.assertEqual(result['predicted'], 84)
        self.assertEqual(result['predicted_interval'], [82, 86])
        self.assertEqual(result['status'], 'overlap')
        self.assertFalse(report['case_reproduction_ready'])
        self.assertEqual(report['not_covered'], [{'panel': 'test', 'driver': 'S5'}])
        self.readings['observations'][0]['pixel'] = [50, 13]
        # The vertical reading interval [86,88] touches the horizontal-error prediction at 86.
        self.assertEqual(compare_readings(self.model, self.protocol, self.readings)['counts']['overlap'], 1)
        self.readings['observations'][0]['pixel'] = [50, 50]
        changed = compare_readings(self.model, self.protocol, self.readings)
        self.assertEqual(changed['counts']['separated'], 1)
        self.assertNotEqual(report['input_sha256'], changed['input_sha256'])

    def test_selected_real_pixels_show_conditional_discrepancy(self):
        readings = json.loads((CASE.parent / 'figure_readings.json').read_text(encoding='utf-8'))
        report = compare_readings(self.model, self.protocol, readings)
        self.assertEqual(sum(report['counts'].values()), 31)
        result = next(r for r in report['comparisons']
                      if r['panel'] == '9a' and r['driver'] == 'D2' and r['axis_t'] < 20)
        # Independent linear calibration and scalar CoD at the selected pixel.
        self.assertAlmostEqual(result['axis_t'], (104-68)*100/355)
        self.assertAlmostEqual(result['read_value'], 100-(59.5-10)/4)
        self.assertAlmostEqual(result['predicted'], 56+(104-68)*100/355)
        self.assertEqual(result['status'], 'separated')
        self.assertTrue(report['not_sampled'])

    def test_invalid_reading_metadata_and_pixels_rejected(self):
        invalid = [None, {}, {**self.readings, 'data_kind': 'original_samples'},
                   {**self.readings, 'source_sha256': 'f'*64},
                   {**self.readings, 'source_sha256': []},
                   {**self.readings, 'axis_mapping': {'kind': 'confirmed'}}]
        for change in ('duplicate_panel', 'anchors', 'size', 'image_hash', 'legend', 'unknown_panel',
                       'unknown_driver', 'intermediate', 'out_of_image', 'zero_error', 'huge_error', 'duplicate'):
            data = deepcopy(self.readings)
            panel, point = data['panels'][0], data['observations'][0]
            if change == 'duplicate_panel': data['panels'].append(deepcopy(panel))
            elif change == 'anchors': panel['x_axis'] = [[0, 0], [0, 100]]
            elif change == 'size': panel['size'] = [0, 101]
            elif change == 'image_hash': panel['image_sha256'] = 'z'*64
            elif change == 'legend': panel['legend'] = ['absent']
            elif change == 'unknown_panel': point['panel'] = 'missing'
            elif change == 'unknown_driver': point['driver'] = 'D2'
            elif change == 'intermediate': point['driver'] = 'S5'
            elif change == 'out_of_image': point['pixel'] = [101, 0]
            elif change == 'zero_error': point['pixel_error'] = [0, 1]
            elif change == 'huge_error': point['pixel_error'] = [10**500, 1]
            elif change == 'duplicate': data['observations'].append(deepcopy(point))
            invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ModelError):
                compare_readings(self.model, self.protocol, data)

    def test_cli_exports_evidence_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = folder / 'readings.json'
            path.write_text(json.dumps(self.readings), encoding='utf-8')
            args = ['compare', str(CASE), '--protocol', str(CASE.parent / 'base_sweep.json'),
                    '--readings', str(path), '--output-dir', str(folder / 'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
            report = json.loads((folder / 'out/fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['readings_snapshot'], self.readings)
            self.assertEqual(len(report['comparisons'][0]['trace']), 3)
            self.assertTrue((folder / 'out/protocol_snapshot.json').is_file())


if __name__ == '__main__':
    unittest.main()
