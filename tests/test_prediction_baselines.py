from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from fdna.model import ModelError
from fdna.prediction_baselines import PROTOCOL, compare, fit_baselines, run
from fdna.structure import digest
from fdna.workflow_common import settings


NETWORK = dict(id='baseline_test', nodes=['A', 'B', 'C'], edges=[
    dict(feeder='A', receiver='B'), dict(feeder='B', receiver='C')])
CONFIG = dict(input_role='observed', pool_scenarios=False, models={
    'base': settings('base', ['B', 'C']), 'free_delta': settings('free_delta', ['B', 'C'])})


def sample_rows():
    return [dict(sample_id=f's{i}', base_case_id=f'b{i}', scenario_id='e', fit_group='e',
        split='train' if i < 7 else 'holdout', node_values=dict(A=float(i), B=5+.7*i, C=3+.5*(5+.7*i)))
        for i in range(1, 10)]


def frozen_report(rows, unavailable=None):
    # Independent, explicit fixed FDNA oracle. No optimizer is used by these tests.
    def forward(x, delta):
        return min(100-.4*(100-x), x+15) + delta if x is not None else None
    jobs = []
    for mode in CONFIG['models']:
        delta = None if mode == 'base' else -2.
        local = [dict(node=n, fit=dict(receivers=[dict(status='iteration_limit' if n == unavailable else 'fitted',
            parameters=[dict(feeder=p, alpha=.4, beta=15)], delta=delta)])) for n, p in [('B', 'A'), ('C', 'B')]]
        points = []
        for r in rows:
            a, b = r['node_values'].get('A'), r['node_values'].get('B')
            rb = None if unavailable == 'B' else forward(a, delta or 0)
            rc = None if unavailable == 'C' else forward(rb, delta or 0)
            for n, lp, rp in [('B', None if unavailable == 'B' else forward(a, delta or 0), rb),
                              ('C', None if unavailable == 'C' else forward(b, delta or 0), rc)]:
                points.append(dict(sample_id=r['sample_id'], base_case_id=r['base_case_id'], scenario_id=r['scenario_id'],
                    split=r['split'], node=n, target=r['node_values'].get(n), local_prediction=lp, recursive_prediction=rp))
        jobs.append(dict(fit_group='e', model=mode, local_fits=local, points=points))
    return dict(network_snapshot=deepcopy(NETWORK), config_snapshot=deepcopy(CONFIG), data_sha256=digest(rows),
                input_sha256=digest(dict(network=NETWORK, rows=rows, config=CONFIG)), jobs=jobs)


class PredictionBaselineTests(unittest.TestCase):
    def test_known_linear_coefficients_and_recursive_predictions(self):
        rows = sample_rows(); frozen = frozen_report(rows); before = deepcopy((frozen, rows))
        result = compare(frozen, rows, PROTOCOL)
        b = next(f for f in result['baseline_fits'] if f['node'] == 'B' and f['method'] == 'ols_intercept')
        self.assertAlmostEqual(b['coefficients'][0], 5.)
        self.assertAlmostEqual(b['coefficients'][1], .7)
        for p in result['points']:
            if p['method'] == 'ols_intercept':
                self.assertAlmostEqual(p['target'], p['local_prediction'])
                self.assertAlmostEqual(p['target'], p['recursive_prediction'])
        self.assertLess(result['frozen_max_difference'], 1e-12)
        dimensions = {(r['method'], r['node']): r['parameter_count'] for r in result['model_complexity']}
        self.assertEqual(dimensions['training_mean', 'B'], 1)
        self.assertEqual(dimensions['ols_intercept', 'B'], 2)
        self.assertEqual(dimensions['fdna:base', 'B'], 2)
        self.assertEqual(dimensions['fdna:free_delta', 'B'], 3)
        self.assertEqual((frozen, rows), before)

    def test_holdout_targets_never_change_baseline_coefficients(self):
        rows = sample_rows(); changed = deepcopy(rows)
        for r in changed:
            if r['split'] == 'holdout':
                r['node_values'].update(B=90., C=1.)
        self.assertEqual(fit_baselines(NETWORK, rows, CONFIG), fit_baselines(NETWORK, changed, CONFIG))
        original = compare(frozen_report(rows), rows, PROTOCOL)
        other = compare(frozen_report(changed), changed, PROTOCOL)
        for a, b in zip(original['points'], other['points']):
            if a['method'] in PROTOCOL['methods']:
                self.assertEqual(a['recursive_prediction'], b['recursive_prediction'])
        original_c = next(p for p in original['points'] if p['sample_id'] == 's9' and p['method'] == 'ols_intercept' and p['node'] == 'C')
        changed_c = next(p for p in other['points'] if p['sample_id'] == 's9' and p['method'] == 'ols_intercept' and p['node'] == 'C')
        self.assertNotEqual(original_c['local_prediction'], changed_c['local_prediction'])

    def test_missing_input_training_exclusion_and_recursive_blocking(self):
        rows = sample_rows(); rows[0]['node_values']['A'] = None; rows[-1]['node_values']['A'] = None
        result = compare(frozen_report(rows), rows, PROTOCOL)
        for fit in result['baseline_fits']:
            if fit['node'] == 'B':
                self.assertNotIn('s1', fit['train_sample_ids'])
        self.assertTrue(all(p['recursive_prediction'] is None for p in result['points'] if p['sample_id'] == 's9'))

    def test_common_rows_expose_unavailable_frozen_fit(self):
        rows = sample_rows(); result = compare(frozen_report(rows, unavailable='C'), rows, PROTOCOL)
        common = [m for m in result['metrics'] if m['node'] == 'C' and m['evaluation_scope'] == 'common_records']
        self.assertTrue(all(m['evaluated'] == 0 and m['rmse'] is None for m in common))
        own = [m for m in result['metrics'] if m['node'] == 'C' and m['method'] == 'ols_intercept'
               and m['evaluation_scope'] == 'all_available']
        self.assertTrue(all(m['evaluated'] > 0 for m in own))

    def test_rank_deficiency_is_visible(self):
        rows = sample_rows()
        for r in rows:
            if r['split'] == 'train':
                r['node_values']['A'] = 1.
        fit = next(f for f in fit_baselines(NETWORK, rows, CONFIG) if f['node'] == 'B' and f['method'] == 'ols_intercept')
        self.assertEqual(fit['design_rank'], 1)
        self.assertEqual(fit['parameter_count'], 2)
        self.assertIn('RANK_DEFICIENT_MINIMUM_NORM', fit['flags'])

    def test_out_of_range_predictions_are_not_clipped(self):
        rows = sample_rows()
        for r in rows:
            r['node_values']['B'] = 2*r['node_values']['A']+10
        rows[-1]['node_values'].update(A=90., B=95.)
        result = compare(frozen_report(rows), rows, PROTOCOL)
        p = next(p for p in result['points'] if p['sample_id'] == 's9' and p['node'] == 'B' and p['method'] == 'ols_intercept')
        self.assertAlmostEqual(p['recursive_prediction'], 190.)
        self.assertTrue(any(m['outside'] > 0 for m in result['metrics'] if m['method'] == 'ols_intercept'))

    def test_leakage_bad_protocol_and_stale_frozen_predictions_rejected(self):
        rows = sample_rows(); report = frozen_report(rows)
        with self.assertRaises(ModelError):
            compare(report, rows, dict(PROTOCOL, prediction_clipping=True))
        changed = deepcopy(rows); changed[-1]['node_values']['A'] = 0.
        with self.assertRaises(ModelError):
            compare(report, changed, PROTOCOL)
        changed = deepcopy(rows); changed[-1]['base_case_id'] = changed[0]['base_case_id']
        with self.assertRaises(ModelError):
            fit_baselines(NETWORK, changed, CONFIG)
        report['jobs'][0]['points'][0]['local_prediction'] += 1.
        with self.assertRaisesRegex(ModelError, '原参数重算不一致'):
            compare(report, rows, PROTOCOL)

    def test_missing_or_duplicate_frozen_prediction_rejected(self):
        rows = sample_rows()
        for action in ('duplicate', 'missing'):
            report = frozen_report(rows); points = report['jobs'][0]['points']
            points.append(deepcopy(points[0])) if action == 'duplicate' else points.pop()
            with self.subTest(action=action), self.assertRaises(ModelError):
                compare(report, rows, PROTOCOL)

    def test_nonempty_output_refused_before_reading_source(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory); (output/'keep.txt').write_text('keep', encoding='utf-8')
            with self.assertRaises(ModelError):
                run('missing-source', output, 'missing-protocol')
            self.assertEqual((output/'keep.txt').read_text(encoding='utf-8'), 'keep')


if __name__ == '__main__':
    unittest.main()
