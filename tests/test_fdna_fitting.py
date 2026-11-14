"""Independent analytical samples, holdout isolation and inactive parameters."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, load_model
from fdna.__main__ import main
from fdna.fitting import fit_parameters, generate_samples
from fdna.structure import digest

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'data/fdna/arcane_case.json'
SETTINGS = CASE.with_name('fit_settings_check.json')
GENERATION = CASE.with_name('fit_generation_check.json')


@unittest.skipUnless(importlib.util.find_spec('scipy'), 'Optional requirements-fdna.txt dependency')
class FittingTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        # A one-edge analytical fixture retains valid case entities, without using reported parameters.
        self.model['edges'] = [deepcopy(next(e for e in self.model['edges']
                                            if e['feeder'] == 'S1' and e['receiver'] == 'S5'))]
        self.model['edges'][0].update(alpha=None, beta=None)
        self.model['case_extension']['delta'] = {'S5': None}
        self.model['reported_fit'] = [r for r in self.model['reported_fit'] if r['receiver'] == 'S5']
        self.settings = json.loads(SETTINGS.read_text(encoding='utf-8'))
        self.settings['starts'] = 4
        self.samples = {'format_version': '1.0', 'id': 'analytical', 'data_kind': 'method_check',
                        'basis': 'Independent piecewise analytical check, not original measurements',
                        'case_sha256': digest(self.model), 'records': []}
        for split, grid in [('train', [0, 10, 20, 40, 60, 80, 100]), ('holdout', [5, 30, 70, 90])]:
            for x in grid:
                self.samples['records'].append({'id': f'{split}:{x}', 'split': split,
                    'node_values': {'S1': x, 'S5': min(60+.4*x, x+20)}})

    def fit(self):
        return fit_parameters(self.model, self.samples, self.settings)

    def test_recovers_active_parameters_against_independent_piecewise_formula(self):
        before = deepcopy((self.model, self.samples, self.settings))
        report = self.fit()
        receiver = report['receivers'][0]
        p = receiver['parameters'][0]
        self.assertAlmostEqual(p['alpha'], .4, places=7)
        self.assertAlmostEqual(p['beta'], 20, places=7)
        self.assertLess(receiver['metrics']['holdout']['rmse'], 1e-7)
        self.assertEqual(receiver['sensitivity']['local_jacobian_rank'], 2)
        self.assertFalse(receiver['sensitivity']['global_uniqueness_established'])
        self.assertFalse(report['case_reproduction_ready'])
        self.assertIsNone(report['policies']['pseudo_r_squared'])
        self.assertEqual((self.model, self.samples, self.settings), before)
        self.assertTrue(all(0 <= attempt['parameters'][0] <= 1 and 0 <= attempt['parameters'][1] <= 100
                            for attempt in receiver['attempts']))

    def test_lbfgsb_recovers_analytical_parameters_and_isolates_holdout(self):
        self.settings['solver'] = 'L-BFGS-B'
        self.test_recovers_active_parameters_against_independent_piecewise_formula()
        self.test_holdout_and_reported_parameters_do_not_influence_optimization()
        report = self.fit()
        self.assertEqual(report['solver']['name'], 'scipy.optimize.minimize')
        self.assertEqual(report['solver']['method'], 'L-BFGS-B')

    def test_lbfgsb_evaluation_limit_is_reported_without_claiming_convergence(self):
        self.settings.update(solver='L-BFGS-B', max_nfev=1)
        report = self.fit()
        self.assertEqual(report['status'], 'blocked')
        self.assertTrue(all(not a['success'] and a['message']
                            for a in report['receivers'][0]['attempts']))

    def test_holdout_and_reported_parameters_do_not_influence_optimization(self):
        original = self.fit()['receivers'][0]
        for row in self.samples['records']:
            if row['split'] == 'holdout': row['node_values']['S5'] = 0
        self.model['edges'][0].update(alpha=.99, beta=99)
        self.samples['case_sha256'] = digest(self.model)
        changed = self.fit()['receivers'][0]
        self.assertEqual(original['parameters'], changed['parameters'])
        self.assertEqual(original['attempts'], changed['attempts'])
        self.assertGreater(changed['metrics']['holdout']['rmse'], 10)

    def test_inactive_cod_diagnosed_instead_of_claiming_unique_recovery(self):
        self.samples['records'] = []
        for split, grid in [('train', [60, 65, 70, 80, 90, 100]), ('holdout', [75, 85])]:
            for x in grid:
                self.samples['records'].append({'id': f'{split}:{x}', 'split': split,
                                                'node_values': {'S1': x, 'S5': 60+.4*x}})
        report = self.fit()['receivers'][0]
        self.assertLess(report['metrics']['holdout']['rmse'], 1e-6)
        self.assertIn('beta:S1', report['sensitivity']['inactive_parameters'])
        self.assertEqual(report['sensitivity']['local_jacobian_rank'], 1)
        self.assertGreater(report['near_best_parameter_spread']['beta:S1'], 1)

    def test_missing_rows_insufficient_data_and_absent_holdout_are_explicit(self):
        self.samples['records'][0]['node_values']['S5'] = None
        report = self.fit()['receivers'][0]
        self.assertEqual(report['omitted'], [{'id': 'train:0', 'reason': 'MISSING_LOCAL_SE'}])
        self.samples['records'] = [r for r in self.samples['records'] if r['split'] == 'train']
        self.assertIsNone(self.fit()['receivers'][0]['metrics']['holdout'])
        self.samples['records'] = self.samples['records'][:1]
        report = self.fit()
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['receivers'][0]['status'], 'insufficient_samples')

    def test_contract_rejects_invalid_samples_and_unstated_delta_policy(self):
        original = deepcopy((self.samples, self.settings))
        for change in ('hash', 'duplicate', 'unknown_node', 'bounds', 'bool', 'nan', 'split', 'delta', 'starts', 'solver'):
            self.samples, self.settings = deepcopy(original)
            if change == 'hash': self.samples['case_sha256'] = '0'*64
            elif change == 'duplicate': self.samples['records'].append(deepcopy(self.samples['records'][0]))
            elif change == 'unknown_node': self.samples['records'][0]['node_values']['absent'] = 1
            elif change == 'bounds': self.samples['records'][0]['node_values']['S1'] = 101
            elif change == 'bool': self.samples['records'][0]['node_values']['S1'] = True
            elif change == 'nan': self.samples['records'][0]['node_values']['S1'] = float('nan')
            elif change == 'split': self.samples['records'][0]['split'] = 'unknown'
            elif change == 'delta': self.settings['delta_policy'] = 'assume_zero_in_article'
            elif change == 'starts': self.settings['starts'] = 0
            elif change == 'solver': self.settings['solver'] = 'unknown'
            with self.subTest(change=change), self.assertRaises(ModelError): self.fit()

    def test_generator_changes_only_leaf_values_and_is_reproducible(self):
        model = load_model(CASE)
        generation = json.loads(GENERATION.read_text(encoding='utf-8'))
        generation.update(random_train=16, random_holdout=16, include_leaf_corners=False)
        samples = generate_samples(model, generation)
        self.assertEqual(samples, generate_samples(model, generation))
        self.assertEqual(len(samples['records']), 32)
        first = samples['records'][0]['node_values']
        expected_s5 = min((.21*first['S1']+79+.18*first['D2']+82+.43*first['D4']+57)/3,
                          first['S1']+34, first['D2']+56, first['D4']+62)
        self.assertAlmostEqual(first['S5'], expected_s5)
        with self.assertRaises(ModelError): generate_samples(self.model, generation)

    def test_cli_fit_exports_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            case, samples = folder/'case.json', folder/'samples.json'
            case.write_text(json.dumps(self.model), encoding='utf-8')
            samples.write_text(json.dumps(self.samples), encoding='utf-8')
            args = ['fit', str(case), '--samples', str(samples), '--settings', str(SETTINGS),
                    '--output-dir', str(folder/'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
            report = json.loads((folder/'out/fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['samples_snapshot'], self.samples)
            self.assertLess(report['receivers'][0]['metrics']['holdout']['rmse'], 1e-7)


if __name__ == '__main__':
    unittest.main()
