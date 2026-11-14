"""Additive-delta propagation, explicit assumptions and regression checks."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, evaluate, load_model
from fdna.__main__ import main
from fdna.additive import ADDITIVE_MODEL, evaluate_additive
from fdna.fitting import fit_check, fit_parameters, generate_samples
from fdna.structure import digest

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'data/fdna/arcane_case.json'


class AdditiveEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        self.generation = json.loads(CASE.with_name('fit_generation_delta_check.json').read_text(encoding='utf-8'))
        self.extension = deepcopy(self.generation['extension'])
        self.scenario = json.loads(CASE.with_name('intact.json').read_text(encoding='utf-8'))

    def test_nonzero_delta_is_added_after_minimum_and_propagated_upstream(self):
        before = deepcopy((self.model, self.scenario, self.extension))
        report = evaluate_additive(self.model, self.scenario, self.extension)
        values = {n['id']: n['value'] for n in report['nodes']}
        self.assertEqual(values['S5'], 97.5)
        self.assertEqual(values['S6'], 98.5)
        self.assertAlmostEqual(values['F7'], 100-(.33*2.5+.37*1.5)/2-3)
        self.assertAlmostEqual(values['F9'], 100-(.03*2.5+.06*1.5)/5-2)
        self.assertEqual(report['model'], ADDITIVE_MODEL)
        self.assertEqual(report['outside_se_range'], [])
        self.assertEqual((self.model, self.scenario, self.extension), before)
        self.assertTrue(all(n['value'] == 100 for n in evaluate(self.model, self.scenario)['nodes']))

    def test_explicit_zero_delta_matches_base_but_unknown_delta_propagates(self):
        self.extension['delta_values'] = dict.fromkeys(self.extension['delta_values'], 0)
        self.assertEqual([n['value'] for n in evaluate_additive(self.model, self.scenario, self.extension)['nodes']],
                         [n['value'] for n in evaluate(self.model, self.scenario)['nodes']])
        self.extension['delta_values']['S5'] = None
        report = evaluate_additive(self.model, self.scenario, self.extension)
        values = {n['id']: n['value'] for n in report['nodes']}
        self.assertIsNone(values['S5'])
        self.assertIsNone(values['F7'])
        self.assertIsNone(values['F9'])
        self.assertIsNone(values['F11'])
        self.assertEqual(values['S6'], 100)
        self.assertEqual(report['status'], 'blocked')

    def test_no_silent_clipping_or_missing_delta_policy(self):
        self.extension['delta_values']['S5'] = 5
        report = evaluate_additive(self.model, self.scenario, self.extension)
        self.assertEqual(next(n['value'] for n in report['nodes'] if n['id'] == 'S5'), 105)
        self.assertIn('S5', report['outside_se_range'])
        for change in ('missing_receiver', 'unknown_receiver', 'policy', 'bool', 'infinity'):
            config = deepcopy(self.extension)
            if change == 'missing_receiver': config['delta_values'].pop('S5')
            elif change == 'unknown_receiver': config['delta_values']['S1'] = 1
            elif change == 'policy': config['output_policy'] = 'clip'
            elif change == 'bool': config['delta_values']['S5'] = True
            elif change == 'infinity': config['delta_values']['S5'] = float('inf')
            with self.subTest(change=change), self.assertRaises(ModelError):
                evaluate_additive(self.model, self.scenario, config)

    def test_cli_requires_explicit_extension_and_does_not_render_base_only_claims(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            config = folder/'extension.json'
            config.write_text(json.dumps(self.extension), encoding='utf-8')
            args = ['evaluate', str(CASE), '--scenario', str(CASE.with_name('intact.json')),
                    '--model', ADDITIVE_MODEL, '--output-dir', str(folder/'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 2)
                self.assertEqual(main(args+['--extension', str(config)]), 0)
            report = json.loads((folder/'out/fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['extension_snapshot'], self.extension)
            self.assertFalse((folder/'out/fdna_summary.md').exists())


@unittest.skipUnless(importlib.util.find_spec('scipy'), 'Optional requirements-fdna.txt dependency')
class AdditiveFittingTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        self.settings = json.loads(CASE.with_name('fit_settings_delta_check.json').read_text(encoding='utf-8'))
        self.generation = json.loads(CASE.with_name('fit_generation_delta_check.json').read_text(encoding='utf-8'))

    def test_recovers_nonzero_delta_from_independent_analytical_samples(self):
        self.model['edges'] = [deepcopy(next(e for e in self.model['edges']
                                            if e['feeder'] == 'S1' and e['receiver'] == 'S5'))]
        self.model['edges'][0].update(alpha=None, beta=None)
        self.model['case_extension']['delta'] = {'S5': None}
        self.model['reported_fit'] = [r for r in self.model['reported_fit'] if r['receiver'] == 'S5']
        self.settings.update(starts=4, delta_bounds={'S5': [-8, 3]})
        samples = {'format_version': '1.0', 'id': 'analytical_delta', 'data_kind': 'method_check',
                   'basis': 'Independent analytical delta check', 'case_sha256': digest(self.model), 'records': []}
        for split, grid in [('train', [0, 10, 20, 40, 60, 80, 100]), ('holdout', [5, 30, 70, 90])]:
            for x in grid:
                samples['records'].append({'id': f'{split}:{x}', 'split': split,
                                           'node_values': {'S1': x, 'S5': min(60+.4*x, x+20)-3.5}})
        before = deepcopy((self.model, samples, self.settings))
        report = fit_parameters(self.model, samples, self.settings)
        receiver = report['receivers'][0]
        self.assertAlmostEqual(receiver['parameters'][0]['alpha'], .4, places=7)
        self.assertAlmostEqual(receiver['parameters'][0]['beta'], 20, places=7)
        self.assertAlmostEqual(receiver['delta'], -3.5, places=7)
        self.assertEqual(receiver['sensitivity']['parameter_count'], 3)
        self.assertEqual(receiver['sensitivity']['local_jacobian_rank'], 3)
        self.assertLess(receiver['metrics']['holdout']['rmse'], 1e-7)
        self.assertEqual((self.model, samples, self.settings), before)
        for row in samples['records']:
            if row['split'] == 'holdout': row['node_values']['S5'] = 0
        self.assertEqual(receiver['attempts'], fit_parameters(self.model, samples, self.settings)['receivers'][0]['attempts'])

    def test_generation_propagates_nonzero_delta_and_rejects_outside_se_range(self):
        self.generation.update(random_train=16, random_holdout=16, include_leaf_corners=False)
        samples = generate_samples(self.model, self.generation)
        first = samples['records'][0]['node_values']
        expected = min((.21*first['S1']+79+.18*first['D2']+82+.43*first['D4']+57)/3,
                       first['S1']+34, first['D2']+56, first['D4']+62)-2.5
        self.assertAlmostEqual(first['S5'], expected)
        self.generation['include_leaf_corners'] = True
        self.generation['extension']['delta_values']['S5'] = 5
        with self.assertRaises(ModelError): generate_samples(self.model, self.generation)

    def test_missing_invalid_delta_bounds_or_generation_model_mismatch_rejected(self):
        original = deepcopy((self.settings, self.generation))
        for change in ('bounds_missing', 'receiver_missing', 'reverse', 'equal', 'policy', 'base_model', 'unknown_delta', 'outside_bounds'):
            self.settings, self.generation = deepcopy(original)
            if change == 'bounds_missing': self.settings.pop('delta_bounds')
            elif change == 'receiver_missing': self.settings['delta_bounds'].pop('S5')
            elif change == 'reverse': self.settings['delta_bounds']['S5'] = [10, -10]
            elif change == 'equal': self.settings['delta_bounds']['S5'] = [0, 0]
            elif change == 'policy': self.settings.pop('output_policy')
            elif change == 'base_model': self.generation.pop('extension')
            elif change == 'unknown_delta': self.generation['extension']['delta_values']['S5'] = None
            elif change == 'outside_bounds': self.generation['extension']['delta_values']['S5'] = -20
            with self.subTest(change=change), self.assertRaises(ModelError): fit_check(self.model, self.generation, self.settings)


if __name__ == '__main__':
    unittest.main()
