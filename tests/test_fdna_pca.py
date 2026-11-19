"""Analytical covariance checks and explicit initialization contracts."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import load_model, ModelError
from fdna.fitting import fit_parameters
from fdna.pca import analyze_pca
from fdna.structure import digest
from fdna.__main__ import main

ROOT = Path(__file__).resolve().parents[1]


class PCATests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(ROOT / 'data/fdna/arcane_case.json')
        self.model['edges'] = [deepcopy(e) for e in self.model['edges']
                               if e['receiver'] == 'S5'][:2]
        self.feeders = [e['feeder'] for e in self.model['edges']]
        self.assertEqual(len(self.feeders), 2)
        self.model['case_extension']['delta'] = {'S5': None}
        self.model['reported_fit'] = [r for r in self.model['reported_fit'] if r['receiver'] == 'S5']
        self.config = dict(format_version='1.0', id='analytical', basis='Independent analytical check',
                           scaling='unit_variance', variance_target=.95)
        self.samples = dict(format_version='1.0', id='analytical', data_kind='method_check',
                            basis='Independent analytical check', case_sha256=digest(self.model), records=[])
        for i, x in enumerate([0, 10, 20, 30]):
            self.samples['records'].append(dict(id=str(i), split='train',
                node_values={self.feeders[0]: x, self.feeders[1]: 2*x, 'S5': 70}))

    def analyze(self):
        return analyze_pca(self.model, self.samples, self.config)

    def test_known_rank_one_covariance_and_scaling(self):
        before = deepcopy((self.model, self.samples, self.config))
        r = self.analyze()['receivers'][0]
        self.assertEqual(r['rank'], 1)
        self.assertEqual(r['retained_count'], 1)
        c = r['components'][0]
        self.assertAlmostEqual(c['eigenvalue'], 2)
        self.assertAlmostEqual(c['variance_ratio'], 1)
        for v in c['loadings'].values():
            self.assertAlmostEqual(v, 2**-.5)
        self.config['scaling'] = 'center_only'
        c = self.analyze()['receivers'][0]['components'][0]
        self.assertAlmostEqual(c['eigenvalue'], 2500/3)
        self.assertAlmostEqual(c['loadings'][self.feeders[1]], 2/5**.5)
        self.assertEqual((self.model, self.samples), before[:2])

    def test_holdout_and_reported_parameters_are_not_features(self):
        original = self.analyze()
        self.samples['records'].append(dict(id='hold', split='holdout', node_values={'S5': 0}))
        for edge in self.model['edges']:
            edge.update(alpha=.99, beta=99)
        self.samples['case_sha256'] = digest(self.model)
        self.assertEqual(original, self.analyze())

    def test_constants_missing_rows_and_insufficient_samples(self):
        for row in self.samples['records']:
            row['node_values'][self.feeders[1]] = .1
        r = self.analyze()['receivers'][0]
        self.assertEqual(r['constant_feeders'], [self.feeders[1]])
        self.assertEqual(r['components'][0]['loadings'][self.feeders[1]], 0)
        for row in self.samples['records']:
            row['node_values'][self.feeders[0]] = 1
        self.assertEqual(self.analyze()['receivers'][0]['status'], 'no_variation')
        self.samples['records'][0]['node_values']['S5'] = None
        self.assertEqual(self.analyze()['receivers'][0]['omitted_train_ids'], ['0'])
        self.samples['records'] = self.samples['records'][:2]
        self.assertEqual(self.analyze()['receivers'][0]['status'], 'insufficient_samples')

    def test_invalid_contracts(self):
        original = deepcopy((self.samples, self.config))
        for kind in ('hash', 'duplicate', 'node', 'threshold', 'scaling'):
            self.samples, self.config = deepcopy(original)
            if kind == 'hash': self.samples['case_sha256'] = '0'*64
            if kind == 'duplicate': self.samples['records'].append(deepcopy(self.samples['records'][0]))
            if kind == 'node': self.samples['records'][0]['node_values']['bad'] = 1
            if kind == 'threshold': self.config['variance_target'] = 0
            if kind == 'scaling': self.config['scaling'] = 'unknown'
            with self.subTest(kind=kind), self.assertRaises(ModelError): self.analyze()

    def setup_fit(self):
        self.model['edges'] = self.model['edges'][:1]
        self.samples['case_sha256'] = digest(self.model)
        f = self.feeders[0]
        self.samples['records'] = [dict(id=str(x), split='train',
            node_values={f: x, 'S5': min(60+.4*x, x+20)}) for x in [0, 10, 20, 40, 60, 80, 100]]
        self.settings = json.loads((ROOT / 'data/fdna/fit_settings_lbfgsb_check.json').read_text(encoding='utf-8'))
        self.settings['starts'] = 3
        self.settings['initialization'] = dict(basis='Explicit analytical check, not PCA-derived', pca=self.config,
            receivers={'S5': dict(parameters=[dict(feeder=f, alpha=.35, beta=45)])})

    def test_physical_start_and_parameter_recovery(self):
        self.setup_fit()
        report = fit_parameters(self.model, self.samples, self.settings)
        r = report['receivers'][0]
        start = next(a for a in r['attempts'] if a['initial_source'] == 'explicit_configuration')
        self.assertEqual(start['initial'], [.35, .45])
        self.assertAlmostEqual(r['parameters'][0]['alpha'], .4, places=6)
        self.assertAlmostEqual(r['parameters'][0]['beta'], 20, places=6)
        self.assertEqual(len(r['attempts']), 3)
        self.assertEqual(report['pca_diagnostics']['status'], 'complete')
        self.samples['records'].append(dict(id='hold', split='holdout', node_values={self.feeders[0]: 50, 'S5': 0}))
        changed = fit_parameters(self.model, self.samples, self.settings)
        self.assertEqual(r['attempts'], changed['receivers'][0]['attempts'])

    def test_initialization_validation_and_additive_scaling(self):
        self.setup_fit()
        original = deepcopy(self.settings)
        for kind in ('receiver', 'feeder', 'duplicate', 'delta', 'alpha'):
            self.settings = deepcopy(original)
            r = self.settings['initialization']['receivers']['S5']
            if kind == 'receiver': self.settings['initialization']['receivers']['F7'] = deepcopy(r)
            if kind == 'feeder': r['parameters'][0]['feeder'] = 'absent'
            if kind == 'duplicate': r['parameters'] *= 2
            if kind == 'delta': r['delta'] = 0
            if kind == 'alpha': r['parameters'][0]['alpha'] = 2
            with self.subTest(kind=kind), self.assertRaises(ModelError):
                fit_parameters(self.model, self.samples, self.settings)
        self.settings = deepcopy(original)
        self.settings.update(model='fdna_additive_delta', delta_policy='per_receiver_bounded',
                             delta_bounds={'S5': [-10, 20]}, output_policy='no_clipping')
        for delta in (None, 21):
            if delta is not None: self.settings['initialization']['receivers']['S5']['delta'] = delta
            with self.assertRaises(ModelError): fit_parameters(self.model, self.samples, self.settings)
        self.settings['initialization']['receivers']['S5']['delta'] = 5
        r = fit_parameters(self.model, self.samples, self.settings)['receivers'][0]
        self.assertEqual(next(a['initial'] for a in r['attempts']
                              if a['initial_source'] == 'explicit_configuration'), [.35, .45, .5])

    def test_cli_output_and_overwrite_protection(self):
        self.model['sources'] = [s for s in self.model['sources'] if 'path' not in s]
        self.samples['case_sha256'] = digest(self.model)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in [('case', self.model), ('samples', self.samples), ('config', self.config)]:
                (root / (name+'.json')).write_text(json.dumps(value), encoding='utf-8')
            args = ['pca', str(root/'case.json'), '--samples', str(root/'samples.json'),
                    '--config', str(root/'config.json'), '--output-dir', str(root/'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
            report = json.loads((root/'out/fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['receivers'][0]['rank'], 1)


if __name__ == '__main__':
    unittest.main()
