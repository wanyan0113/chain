from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from fdna.model import ModelError
from fdna.solver_seed_sensitivity import seed_config,summarize,ranges,run
from tests.test_prediction_baselines import CONFIG


def record(seed,failed=False):
    return dict(seed=seed,fits=[dict(fit_group='e',model='base',node='B',status='iteration_limit' if failed else 'fitted',
        flags=['LOCAL_RANK_DEFICIENT'],parameters=[dict(feeder='A',alpha=.4,beta=seed)],delta=None,
        attempts=[dict(rmse=2.)],attempt_selection=dict(index=0))],
        metrics=[dict(fit_group='e',model='base',node='B',split='holdout',prediction='local',
            rmse=None if failed else 2.,evaluated=0 if failed else 1,outside=0)],
        holdout_points=[dict(fit_group='e',model='base',node='B',sample_id='s',target=10.,
            local_prediction=None if failed else 12.,recursive_prediction=None if failed else 12.)])


class SolverSeedTests(unittest.TestCase):
    def test_seed_only_change_keeps_bounds_split_and_source_immutable(self):
        config=deepcopy(CONFIG)
        for m in config['models'].values():m.update(starts=6,max_nfev=1500)
        before=deepcopy(config);changed=seed_config(config,123)
        self.assertEqual(config,before)
        for mode in config['models']:
            expected=deepcopy(config['models'][mode]);expected['seed']=123
            self.assertEqual(changed['models'][mode],expected)
        changed['models']['free_delta']['delta_bounds']['B'][0]=-100
        self.assertEqual(config,before)

    def test_changed_solver_settings_and_invalid_seed_rejected(self):
        for seed in (True,-1,1.2):
            with self.subTest(seed=seed),self.assertRaises(ModelError):seed_config(CONFIG,seed)
        with self.assertRaises(ModelError):seed_config(CONFIG,123)

    def test_inactive_parameter_can_change_while_loss_and_predictions_do_not(self):
        summary=summarize([record(1),record(20)],[1,20])
        beta=next(r for r in summary['parameters'] if r['parameter']=='beta:A')
        self.assertEqual(beta['span'],19)
        self.assertEqual(summary['diagnostics'][0]['selected_training_rmse']['span'],0)
        self.assertTrue(all(r['span']==0 for r in summary['holdout_points']))

    def test_failure_keeps_planned_denominator_and_unavailable_predictions(self):
        summary=summarize([record(1),record(2,True)],[1,2])
        self.assertEqual(summary['diagnostics'][0]['failed'],1)
        self.assertEqual(summary['metrics'][0]['unavailable'],1)
        self.assertTrue(all(r['planned']==2 and r['evaluated']==1 for r in summary['holdout_points']))

    def test_duplicate_missing_keys_and_changed_targets_are_rejected(self):
        for mutation in ('seed','fit','point','metric','target'):
            a,b=record(1),record(2)
            if mutation=='seed':b['seed']=1
            elif mutation=='fit':b['fits']*=2
            elif mutation=='point':b['holdout_points']=[]
            elif mutation=='metric':b['metrics']=[]
            else:b['holdout_points'][0]['target']=20
            with self.subTest(mutation=mutation),self.assertRaises(ModelError):summarize([a,b],[1,2])

    def test_nonfinite_ranges_are_not_silently_reported(self):
        with self.assertRaises(ModelError):ranges([float('nan')])
        self.assertIsNone(ranges([])['span'])

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'keep').write_text('original')
            with self.assertRaises(ModelError):run('missing','missing',out)
            self.assertEqual((out/'keep').read_text(),'original')


if __name__=='__main__':unittest.main()
