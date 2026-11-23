from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import numpy as np

from fdna.model import ModelError
from fdna.prediction_stability import METHODS, PROTOCOL, analyze, prepare_panel, resampled_losses, run


def fixture():
    points = []
    for gi, group in enumerate(('e0', 'e1')):
        for bi in range(4):
            for ni, node in enumerate(('B', 'F')):
                for mi, method in enumerate(METHODS):
                    target = 30. + bi + ni
                    error = (mi-1.5)*(bi+1) + gi - ni
                    points.append(dict(fit_group=group, sample_id=f'{group}_s{bi}', base_case_id=f'b{bi}',
                        scenario_id=group, split='train' if bi == 0 else 'holdout', node=node, method=method,
                        target=target, local_prediction=target+error, recursive_prediction=target+2*error))
    return dict(points=points)


class PredictionStabilityTests(unittest.TestCase):
    def test_joint_indices_preserve_axes_and_multiplicity_against_scalar_oracle(self):
        errors = np.arange(2*2*2*4*3, dtype=float).reshape(2, 2, 2, 4, 3)-40
        draws = np.array([[0, 0, 1], [2, 2, 2], [2, 1, 0]])
        result = resampled_losses(errors, draws)
        for di, draw in enumerate(draws):
            for condition in np.ndindex(errors.shape[:-1]):
                values = [float(errors[(*condition, bi)]) for bi in draw]
                self.assertAlmostEqual(result[(di, *condition, 0)], (sum(v*v for v in values)/3)**.5)
                self.assertAlmostEqual(result[(di, *condition, 1)], sum(abs(v) for v in values)/3)
        self.assertNotEqual(result[0, 0, 0, 0, 0, 1], result[2, 0, 0, 0, 0, 1])

    def test_repeatable_order_invariant_and_does_not_mutate_inputs(self):
        source = fixture(); before = deepcopy(source)
        first = analyze(source, PROTOCOL)
        source['points'].reverse(); other = analyze(source, PROTOCOL)
        self.assertEqual(first[0], other[0])
        for a, b in zip(first[1:], other[1:]):
            np.testing.assert_array_equal(a, b)
        source['points'].reverse(); self.assertEqual(source, before)
        self.assertFalse(first[0]['policies']['parameter_confidence_interval'])
        self.assertFalse(first[0]['policies']['model_refitted'])

    def test_training_prediction_changes_do_not_enter_resampling(self):
        source = fixture(); original = analyze(source, PROTOCOL)
        for p in source['points']:
            if p['split'] == 'train':
                p['local_prediction'] = 10000.; p['recursive_prediction'] = -10000.
        changed = analyze(source, PROTOCOL)
        self.assertEqual(original[0], changed[0])
        for a, b in zip(original[1:], changed[1:]):
            np.testing.assert_array_equal(a, b)

    def test_paired_differences_use_same_draw_and_sign_counts_are_exhaustive(self):
        report, draws, losses, differences = analyze(fixture(), PROTOCOL)
        for ci, (left, right) in enumerate(PROTOCOL['comparisons']):
            np.testing.assert_array_equal(differences[..., ci, :],
                losses[..., METHODS.index(left), :]-losses[..., METHODS.index(right), :])
        for row in report['contrasts']:
            self.assertEqual(row['negative_count']+row['positive_count']+row['numerical_tie_count'], len(draws))
            self.assertLessEqual(row['q025'], row['median']); self.assertLessEqual(row['median'], row['q975'])
        self.assertEqual(report['panel']['base_cases'], ['b1', 'b2', 'b3'])

    def test_missing_duplicate_blocked_and_inconsistent_targets_rejected(self):
        for kind in ('missing', 'duplicate', 'blocked', 'target'):
            source = fixture(); held = next(p for p in source['points'] if p['split'] == 'holdout')
            if kind == 'missing': source['points'].remove(held)
            elif kind == 'duplicate': source['points'].append(deepcopy(held))
            elif kind == 'blocked': held['recursive_prediction'] = None
            else: held['target'] += 1
            with self.subTest(kind=kind), self.assertRaises(ModelError):
                prepare_panel(source)

    def test_base_leakage_and_extra_observations_rejected(self):
        source = fixture()
        next(p for p in source['points'] if p['split'] == 'holdout')['base_case_id'] = 'b0'
        with self.assertRaisesRegex(ModelError, '跨训练与留出'):
            prepare_panel(source)
        source = fixture(); extra = deepcopy(next(p for p in source['points'] if p['split'] == 'holdout'))
        extra['sample_id'] += '_repeat'; source['points'].append(extra)
        with self.assertRaisesRegex(ModelError, '多个网络观测'):
            prepare_panel(source)

    def test_invalid_indices_protocol_and_nonfinite_predictions_rejected(self):
        errors = prepare_panel(fixture())['errors']
        for indices in (np.array([[0, 1, 3]]), np.array([[0., 1., 2.]]), np.array([[0, 1]])):
            with self.subTest(indices=indices), self.assertRaises(ModelError):
                resampled_losses(errors, indices)
        with self.assertRaises(ModelError):
            analyze(fixture(), dict(PROTOCOL, seed=1))
        source = fixture(); next(p for p in source['points'] if p['split'] == 'holdout')['local_prediction'] = float('nan')
        with self.assertRaises(ModelError): prepare_panel(source)

    def test_nonempty_output_refused_before_reading_source(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory); (out/'keep.txt').write_text('keep', encoding='utf-8')
            with self.assertRaises(ModelError): run('missing', out, 'missing')
            self.assertEqual((out/'keep.txt').read_text(encoding='utf-8'), 'keep')


if __name__ == '__main__':
    unittest.main()
