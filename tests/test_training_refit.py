from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from fdna.model import ModelError
from fdna.training_refit import training_panel,sample_training,compare_control,compact,summarize,run
from tests.test_prediction_baselines import sample_rows,frozen_report


def fit_fixture():
    result=frozen_report(sample_rows());result['status']='complete'
    for job in result['jobs']:
        job['metrics']=[]
        for local in job['local_fits']:
            local['status']=local['fit']['receivers'][0]['status'];local['flags']=[]
            local['fit']['settings_snapshot']=deepcopy(result['config_snapshot']['models'][job['model']])
    return result


class TrainingRefitTests(unittest.TestCase):
    def test_joint_base_case_sampling_preserves_environments_and_holdout(self):
        rows=sample_rows();other=deepcopy(rows)
        for r in other:r['fit_group']='other';r['scenario_id']='other'
        rows+=other;before=deepcopy(rows)
        draw=[0,0,1,3,5,5];result=sample_training(rows,draw,1)
        self.assertEqual(rows,before)
        for group in ('e','other'):
            train=[r for r in result if r['fit_group']==group and r['split']=='train']
            self.assertEqual([r['base_case_id'] for r in train],['b1','b1','b2','b4','b6','b6'])
            self.assertEqual(len({r['sample_id'] for r in train}),6)
            self.assertEqual([r for r in result if r['fit_group']==group and r['split']=='holdout'],
                             [r for r in rows if r['fit_group']==group and r['split']=='holdout'])
        train=next(r for r in result if r['split']=='train');train['node_values']['A']=99.
        self.assertEqual(rows,before)

    def test_leakage_repeated_panel_and_invalid_draw_rejected(self):
        rows=sample_rows();bad=deepcopy(rows);bad[-1]['base_case_id']=bad[0]['base_case_id']
        with self.assertRaises(ModelError):training_panel(bad)
        with self.assertRaises(ModelError):training_panel(rows+[deepcopy(rows[0])])
        for draw in ([0]*5,[0,1,2,3,4,6],[0.,1.,2.,3.,4.,5.],[False]*6):
            with self.subTest(draw=draw),self.assertRaises(ModelError):sample_training(rows,draw,1)

    def test_control_detects_parameter_prediction_or_status_drift(self):
        result=fit_fixture();audit=compare_control(result,deepcopy(result))
        self.assertEqual(audit['max_difference'],0)
        for kind in ('parameter','prediction','status'):
            changed=deepcopy(result)
            if kind=='parameter':changed['jobs'][0]['local_fits'][0]['fit']['receivers'][0]['parameters'][0]['beta']+=1
            elif kind=='prediction':changed['jobs'][0]['points'][0]['recursive_prediction']+=1
            else:changed['jobs'][0]['local_fits'][0]['status']='iteration_limit'
            with self.subTest(kind=kind),self.assertRaises(ModelError):compare_control(result,changed)

    def test_compact_records_keep_settings_attempts_and_only_holdout_points(self):
        source=fit_fixture();before=deepcopy(source);record=compact(source,1,'protocol','source')
        self.assertEqual(source,before)
        self.assertTrue(all(p['split']=='holdout' for p in record['holdout_points']))
        self.assertTrue(all(f['settings_sha256'] for f in record['fits']))
        self.assertEqual(record,compact(source,1,'protocol','source'))
        self.assertNotEqual(record['fits'][0]['refit_id'],compact(source,2,'protocol','source')['fits'][0]['refit_id'])

    def test_ranges_keep_failure_denominator_and_undefined_predictions(self):
        records=[]
        for i,status in [(1,'fitted'),(2,'iteration_limit')]:
            records.append(dict(repetition=i,fit_group='e',model='base',node='B',status=status,
                flags=['LOCAL_RANK_DEFICIENT'],parameters=[dict(feeder='A',alpha=.4,beta=20)],delta=None,settings_sha256='fixed'))
        metrics=[dict(repetition=i,fit_group='e',model='base',node='B',prediction='local',split='holdout',
            rmse=2. if i==1 else None,evaluated=3 if i==1 else 0,outside=0) for i in (1,2)]
        summary=summarize(records,metrics,2)
        self.assertEqual(summary['parameters'][0]['failed'],1)
        self.assertEqual(summary['parameters'][0]['evaluated'],1)
        self.assertEqual(summary['diagnostics'][0]['failed'],1)
        self.assertEqual(summary['prediction'][0]['unavailable'],1)
        self.assertEqual(summary['prediction'][0]['median'],2.)

    def test_duplicate_repetitions_and_changed_solver_settings_rejected(self):
        record=dict(repetition=1,fit_group='e',model='base',node='B',status='fitted',flags=[],parameters=[],delta=None,settings_sha256='a')
        with self.assertRaises(ModelError):summarize([record,deepcopy(record)],[],2)
        with self.assertRaises(ModelError):summarize([record,dict(record,repetition=2,settings_sha256='b')],[],2)

    def test_nonempty_output_refused_before_reading_source(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'keep').write_text('keep',encoding='utf-8')
            with self.assertRaises(ModelError):run('missing','missing',out)
            self.assertEqual((out/'keep').read_text(encoding='utf-8'),'keep')


if __name__=='__main__':unittest.main()
