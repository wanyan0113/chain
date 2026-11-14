"""Supplementary checks must retain clean holdout and explicit noise assumptions."""
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
from fdna.robustness import perturb_training, robustness_check

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'data/fdna/arcane_case.json'


@unittest.skipUnless(importlib.util.find_spec('scipy'), 'Optional requirements-fdna.txt dependency')
class RobustnessTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        self.model['edges'] = [deepcopy(next(e for e in self.model['edges']
                                            if e['feeder'] == 'S1' and e['receiver'] == 'S5'))]
        self.model['edges'][0].update(alpha=.4, beta=20)
        self.model['case_extension']['delta'] = {'S5': None}
        self.model['reported_fit'] = [r for r in self.model['reported_fit'] if r['receiver'] == 'S5']
        self.generation = json.loads(CASE.with_name('fit_generation_delta_check.json').read_text(encoding='utf-8'))
        self.generation.update(include_leaf_corners=False)
        self.generation['extension']['delta_values'] = {'S5': -3.5}
        self.settings = json.loads(CASE.with_name('fit_settings_delta_check.json').read_text(encoding='utf-8'))
        self.settings['delta_bounds'] = {'S5': [-8, 3]}
        self.protocol = json.loads(CASE.with_name('fit_robustness_check.json').read_text(encoding='utf-8'))
        self.protocol.update(seeds=[1, 2], noise_std=[0, .5], random_train=16, random_holdout=16, starts=2)

    def test_noise_only_changes_training_values_and_resamples_instead_of_clipping(self):
        samples = {'basis': 'artificial', 'records': [
            {'id': str(i), 'split': 'train', 'node_values': {'S1': 0, 'S5': 100}} for i in range(20)] +
            [{'id': 'holdout', 'split': 'holdout', 'node_values': {'S1': 0, 'S5': 100}}]}
        before = deepcopy(samples)
        perturbed, info = perturb_training(samples, 1, [1, 2])
        self.assertEqual(perturbed, perturb_training(samples, 1, [1, 2])[0])
        self.assertEqual(perturbed['records'][-1], samples['records'][-1])
        self.assertGreater(info['rejected_draws'], 0)
        self.assertEqual(info['perturbed_values'], 40)
        self.assertTrue(all(0 < v < 100 for r in perturbed['records'][:-1] for v in r['node_values'].values()))
        self.assertEqual(perturb_training(samples, 0, 1)[0]['records'], samples['records'])
        self.assertEqual(samples, before)
        with self.assertRaises(ModelError): perturb_training(samples, -1, 1)

    def test_trials_pair_clean_holdout_and_preserve_inputs(self):
        before = deepcopy((self.model, self.generation, self.settings, self.protocol))
        report = robustness_check(*before)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(len(report['trials']), 4)
        self.assertFalse(report['case_reproduction_ready'])
        self.assertIsNone(report['policies']['acceptance_threshold'])
        for seed in self.protocol['seeds']:
            group = [t for t in report['trials'] if t['seed'] == seed]
            holdout = [[r for r in t['samples_snapshot']['records'] if r['split'] == 'holdout'] for t in group]
            self.assertEqual(holdout[0], holdout[1])
            self.assertEqual(group[0]['perturbation']['perturbed_values'], 0)
            self.assertGreater(group[1]['perturbation']['perturbed_values'], 0)
        self.assertEqual((self.model, self.generation, self.settings, self.protocol), before)
        self.assertEqual(report['aggregate'][1]['trial_count'], 2)
        self.assertEqual(len(report['aggregate'][1]['parameter_ranges']), 3)

    def test_no_control_duplicate_seed_or_model_mismatch_rejected(self):
        for change in ('no_control', 'duplicate_seed', 'mismatch'):
            config = deepcopy(self.protocol)
            generation = deepcopy(self.generation)
            if change == 'no_control': config['noise_std'] = [.5, 1]
            elif change == 'duplicate_seed': config['seeds'] = [1, 1]
            elif change == 'mismatch': generation.pop('extension')
            with self.subTest(change=change), self.assertRaises(ModelError):
                robustness_check(self.model, generation, self.settings, config)

    def test_cli_exports_protocol_and_trial_snapshots(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            paths = []
            for name, value in [('case', self.model), ('generation', self.generation), ('settings', self.settings), ('protocol', self.protocol)]:
                path = folder/(name+'.json')
                path.write_text(json.dumps(value), encoding='utf-8')
                paths.append(path)
            args = ['fit-stability', str(paths[0]), '--generation', str(paths[1]), '--settings', str(paths[2]),
                    '--protocol', str(paths[3]), '--output-dir', str(folder/'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
            report = json.loads((folder/'out/fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['protocol_snapshot'], self.protocol)
            self.assertEqual(len(report['trials']), 4)


if __name__ == '__main__':
    unittest.main()
