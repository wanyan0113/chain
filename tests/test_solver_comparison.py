from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from fdna.model import ModelError
from fdna.solver_comparison import solver_config,compare_records,run
from tests.test_prediction_baselines import CONFIG


def record(solver,value=2.,status='fitted'):
    return dict(solver=solver,fits=[dict(fit_group='g',model='base',node='B',status=status,flags=[],
        parameters=[dict(feeder='A',alpha=.5,beta=50.)],delta=None,attempts=[dict(initial=[.5,.5],rmse=value,success=status=='fitted')],attempt_selection=dict(index=0))],
        metrics=[dict(fit_group='g',model='base',node='B',split='holdout',prediction='local',rmse=value if status=='fitted' else None,available=30,evaluated=30 if status=='fitted' else 0,outside=0)],
        holdout_points=[dict(fit_group='g',model='base',node='B',sample_id='s',target=1.,local_prediction=value if status=='fitted' else None,recursive_prediction=value if status=='fitted' else None)])


class SolverComparisonTests(unittest.TestCase):
    def test_only_solver_changes_and_source_is_immutable(self):
        config=deepcopy(CONFIG)
        for s in config['models'].values():s.update(starts=6,max_nfev=1500)
        before=deepcopy(config);changed=solver_config(config,'least_squares')
        self.assertEqual(config,before)
        for mode in config['models']:
            expected=deepcopy(config['models'][mode]);expected['solver']='least_squares'
            self.assertEqual(changed['models'][mode],expected)
        changed['models']['free_delta']['delta_bounds']['B'][0]=-999
        self.assertEqual(config,before)

    def test_rejects_unregistered_solver_and_changed_source_settings(self):
        for solver in ('unknown','least_squares'):
            with self.assertRaises(ModelError):solver_config(CONFIG,solver)

    def test_paired_differences_have_explicit_orientation(self):
        summary=compare_records([record('L-BFGS-B',2.),record('least_squares',1.)])
        self.assertEqual(summary['diagnostics'][0]['training_rmse_difference'],-1.)
        self.assertEqual(summary['metrics'][0]['difference'],-1.)
        self.assertTrue(all(p['difference']==-1. for p in summary['holdout_points']))

    def test_failure_not_replaced_and_evaluation_denominator_retained(self):
        summary=compare_records([record('L-BFGS-B'),record('least_squares',status='iteration_limit')])
        self.assertIsNone(summary['metrics'][0]['difference'])
        self.assertEqual(summary['metrics'][0]['planned'],30);self.assertEqual(summary['metrics'][0]['least_squares_evaluated'],0)
        self.assertTrue(all(p['least_squares'] is None for p in summary['holdout_points']))

    def test_mismatched_starts_duplicate_keys_or_target_changes_rejected(self):
        for kind in ('starts','fits','points','metrics','target'):
            a=record('L-BFGS-B');b=record('least_squares')
            if kind=='starts':b['fits'][0]['attempts'][0]['initial'][0]=.1
            elif kind=='fits':b['fits']*=2
            elif kind=='points':b['holdout_points']=[]
            elif kind=='metrics':b['metrics']*=2
            else:b['holdout_points'][0]['target']=99.
            with self.subTest(kind=kind),self.assertRaises(ModelError):compare_records([a,b])

    def test_existing_output_never_overwritten(self):
        import json
        from fdna.solver_comparison import PROTOCOL
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'keep').write_text('keep');(p/'protocol.json').write_text(json.dumps(PROTOCOL))
            with self.assertRaises(ModelError):run('missing',p/'protocol.json',p)
            self.assertEqual((p/'keep').read_text(),'keep')


if __name__=='__main__':unittest.main()
