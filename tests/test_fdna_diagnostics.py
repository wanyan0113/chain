"""Scalar interval oracles for bounds and a fixed receiver offset hypothesis."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from fdna import load_model
from fdna.diagnostics import diagnose_comparisons
from fdna.figure_compare import compare_readings

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'data/fdna/arcane_case.json'


def point(driver, observer, x, read, predicted):
    return {'panel': 'fixture', 'driver': driver, 'observer': observer, 'axis_t': x,
            't_error': 1, 'read_interval': read, 'predicted_interval': predicted}


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)

    def test_cod_bound_independent_of_other_feeders_and_strict_exceedance(self):
        points = [point('D2', 'S5', 10, [85, 89], [65, 67]),
                  point('D4', 'S6', 10, [68, 70], [66, 68]),
                  point('S1', 'F7', 10, [60, 65], [70, 75])]
        before = deepcopy((self.model, points))
        result = diagnose_comparisons(self.model, points)
        self.assertEqual((self.model, points), before)
        bounds = result['edge_bounds']
        self.assertEqual(len(bounds), 2)  # No invented direct S1->F7 bound.
        self.assertEqual(bounds[0]['cod_upper'], 11+56)
        self.assertEqual(bounds[0]['minimum_positive_offset_to_avoid_this_bound'], 18)
        self.assertEqual(bounds[0]['status'], 'exceeds_base_bound')
        self.assertEqual(bounds[1]['cod_upper'], 68)
        self.assertEqual(bounds[1]['status'], 'not_excluded')  # Touching is allowed.

    def test_fixed_receiver_delta_requires_intersection_and_skips_downstream(self):
        points = [point('S1', 'S5', 10, [43, 47], [43, 45]),
                  point('D2', 'S5', 10, [85, 89], [65, 67]),
                  point('S1', 'S6', 10, [59, 62], [57, 59]),
                  point('D2', 'S6', 10, [69, 72], [68, 70]),
                  point('S1', 'F7', 10, [60, 65], [70, 75]),
                  point('D2', 'F7', 10, [85, 90], [80, 85])]
        tests = diagnose_comparisons(self.model, points)['constant_delta_tests']
        self.assertEqual([t['receiver'] for t in tests], ['S5', 'S6'])
        self.assertEqual(tests[0]['required_lower'], 18)
        self.assertEqual(tests[0]['required_upper'], 4)
        self.assertIsNone(tests[0]['intersection'])
        self.assertEqual(tests[0]['status'], 'incompatible')
        self.assertEqual(tests[1]['intersection'], [0, 4])
        self.assertEqual(tests[1]['status'], 'not_excluded')

    def test_actual_readings_reject_constant_offset_for_first_layer_only(self):
        protocol = json.loads((CASE.parent / 'base_sweep.json').read_text(encoding='utf-8'))
        readings = json.loads((CASE.parent / 'figure_readings.json').read_text(encoding='utf-8'))
        report = compare_readings(self.model, protocol, readings)
        diagnosis = report['diagnostics']
        self.assertEqual(diagnosis['counts']['incompatible_fixed_delta_receivers'], 2)
        self.assertEqual([t['receiver'] for t in diagnosis['constant_delta_tests']], ['S5', 'S6'])
        self.assertTrue(all(t['intersection'] is None for t in diagnosis['constant_delta_tests']))
        s5_d2 = next(p for p in diagnosis['edge_bounds']
                     if p['observer'] == 'S5' and p['driver'] == 'D2' and p['axis_t'] < 20)
        self.assertEqual(s5_d2['status'], 'exceeds_base_bound')
        self.assertGreater(s5_d2['minimum_positive_offset_to_avoid_this_bound'], 19)


if __name__ == '__main__':
    unittest.main()
