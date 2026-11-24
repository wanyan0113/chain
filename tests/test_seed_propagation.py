from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import numpy as np

from fdna.model import ModelError
from fdna.refit_propagation import replay,summarize
from fdna.seed_propagation import PROTOCOL,control_comparison,focus_rows,run


class SeedPropagationTests(unittest.TestCase):
    def setUp(self):
        self.net=dict(id='test',nodes=['A','B','F'],edges=[dict(feeder='A',receiver='B'),dict(feeder='B',receiver='F')])
        self.rows=[dict(fit_group='g',base_case_id='one',sample_id='s',split='holdout',node_values=dict(A=80.,B=1.,F=2.))]
        self.jobs=[('g','base')];self.cases=['one'];self.pairs=[('B','F')];self.fractions=[0,1]
        self.ranges={('g','B','A'):(70,90),('g','F','B'):(80,95)}
        def record(seed,beta):
            fits=[dict(node=n,fit_group='g',model='base',status='fitted',parameters=[dict(feeder=p,alpha=a,beta=b)],delta=None,
                sensitivity=dict(inactive_parameters=['beta:'+p])) for n,p,a,b in [('B','A',.5,50),('F','B',.8,beta)]]
            return dict(seed=seed,fits=fits,metrics=[dict(fit_group='g',model='base',node='F',split='holdout',prediction=k,rmse=2.) for k in ('local','recursive')])
        self.records=[record(1,10),record(2,50)]
    def replay(self,record,rows=None):
        return replay(self.net,record,rows or self.rows,self.jobs,self.cases,self.pairs,self.fractions,self.ranges)

    def test_same_ordinary_prediction_different_zero_propagation(self):
        a,b=[self.replay(r) for r in self.records]
        np.testing.assert_array_equal(a['baseline'],b['baseline'])
        self.assertAlmostEqual(a['decrease'][0,0,0,0],82)
        self.assertAlmostEqual(b['decrease'][0,0,0,0],72)
        self.assertTrue(a['extrapolated'][0,0,0,0]);self.assertFalse(a['outside'].any())
        self.assertAlmostEqual(a['decrease'][0,0,1,0],0)

    def test_internal_observations_do_not_enter_propagation_and_inputs_immutable(self):
        before=deepcopy((self.net,self.records,self.rows));rows=deepcopy(self.rows)
        rows[0]['node_values'].update(B=999.,F=-999.)
        np.testing.assert_array_equal(self.replay(self.records[0])['decrease'],self.replay(self.records[0],rows)['decrease'])
        self.assertEqual((self.net,self.records,self.rows),before)

    def test_unavailable_model_keeps_fixed_denominator(self):
        first=self.replay(self.records[0]);failed=deepcopy(self.records[1]);failed['fits'][1]['status']='iteration_limit'
        second=self.replay(failed);arrays={k:np.stack([first[k],second[k]]) for k in first}
        old={'summary':[dict(group='g',model='base',intervened_node='B',target='F',retained_fraction=f,mean_decrease=0.) for f in self.fractions]}
        summary=summarize(arrays,self.jobs,self.pairs,self.fractions,old)
        self.assertEqual(summary[0]['planned_models'],2);self.assertEqual(summary[0]['complete_models'],1)
        self.assertFalse(second['valid'].any())

    def test_control_detects_duplicate_or_changed_frozen_response(self):
        arrays=self.replay(self.records[0]);original=dict(analysis=dict(effects=[]))
        for i,fraction in enumerate(self.fractions):
            original['analysis']['effects'].append(dict(group='g',model='base',intervened_node='B',target='F',
                retained_fraction=fraction,baseline_sample='s',effect=-arrays['decrease'][0,0,i,0],intervened=arrays['changed'][0,0,i,0],
                forced_value=arrays['forced'][0,0,i,0],baseline=92.))
        args=(self.rows,self.jobs,self.cases,self.pairs,self.fractions,['A','B','F'])
        self.assertEqual(control_comparison(arrays,original,*args)['maximum_absolute_difference'],0)
        bad=deepcopy(original);bad['analysis']['effects'][0]['effect']+=1
        with self.assertRaises(ModelError):control_comparison(arrays,bad,*args)
        bad=deepcopy(original);bad['analysis']['effects'].append(deepcopy(bad['analysis']['effects'][0]))
        with self.assertRaises(ModelError):control_comparison(arrays,bad,*args)

    def test_focus_preserves_seed_and_branch_counts(self):
        runs=[self.replay(r) for r in self.records];arrays={k:np.stack([r[k] for r in runs]) for k in runs[0]}
        focus=[dict(group='g',model='base',intervened_node='B',target='F',retained_fraction=0)]
        rows=focus_rows(self.records,arrays,self.jobs,self.pairs,self.fractions,focus)
        self.assertEqual([r['seed'] for r in rows],[1,2]);self.assertEqual([r['unique_cod_responses'] for r in rows],[1,0])
        self.assertTrue(all(r['training_beta_inactive'] for r in rows))

    def test_output_never_overwrites_existing_records(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'keep').write_text('original')
            protocol=out/'protocol.json'
            import json
            protocol.write_text(json.dumps(PROTOCOL))
            with self.assertRaises(ModelError):run('missing','missing',protocol,out)
            self.assertEqual((out/'keep').read_text(),'original')

    def test_focus_failure_without_parameters_stays_unavailable(self):
        record=deepcopy(self.records[0]);record['fits'][1].update(status='numerical_failure',parameters=[])
        arrays={k:np.stack([v]) for k,v in self.replay(record).items()}
        focus=[dict(group='g',model='base',intervened_node='B',target='F',retained_fraction=0)]
        row=focus_rows([record],arrays,self.jobs,self.pairs,self.fractions,focus)[0]
        self.assertFalse(row['complete_baselines']);self.assertIsNone(row['mean_decrease']);self.assertIsNone(row['target_beta'])


if __name__=='__main__':unittest.main()
