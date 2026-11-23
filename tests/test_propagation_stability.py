from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from fdna.model import ModelError
from fdna.network_workflow import analyze_dependencies
from fdna.propagation_stability import PROTOCOL,replay,summarize,run
from tests.test_prediction_baselines import frozen_report,sample_rows


class PropagationStabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=sample_rows();cls.fit=frozen_report(cls.rows)
        cls.fit['topology']=dict(order=['A','B','C'],roots=['A'],receivers=['B','C'])
        cls.legacy=analyze_dependencies(cls.fit,cls.rows,dict(retained_fractions=PROTOCOL['retained_fractions'],
            max_paths_per_node=1000,baseline_policy='first_holdout'))
        cls.draws=dict(base_cases=['b7','b8','b9'],indices=[[0,0,2],[2,1,0]])
        with patch('fdna.network_workflow.fit_parameters') as fitter:
            cls.report,cls.resampled,cls.indices=replay(cls.fit,cls.rows,cls.legacy,cls.draws,PROTOCOL)
            fitter.assert_not_called()

    def test_all_holdout_replay_preserves_original_and_non_descendants(self):
        audit=self.report['audit']
        self.assertEqual(audit['baseline_job_pairs'],6)
        self.assertEqual(audit['responses'],90)
        self.assertEqual(audit['original_responses_compared'],30)
        self.assertLess(audit['original_max_difference'],1e-10)
        self.assertTrue(audit['non_descendants_invariant'])
        self.assertFalse(self.report['policies']['refitted'])
        self.assertTrue(all(e['effect']<=1e-8 for e in self.report['analysis']['effects']))
        self.assertTrue(all(abs(e['effect'])<1e-8 for e in self.report['analysis']['effects'] if e['retained_fraction']==1))

    def test_cluster_weights_and_baseline_vs_mean_ranges_against_scalar_oracle(self):
        summaries=self.report['summary'];effects=self.report['analysis']['effects']
        sample_to_base={(r['fit_group'],r['sample_id']):r['base_case_id'] for r in self.rows}
        for i,row in enumerate(summaries):
            own={sample_to_base[e['group'],e['baseline_sample']]:-e['effect'] for e in effects
                 if all(e[k]==row[k] for k in ('group','model','intervened_node','target','retained_fraction'))}
            values=[own[b] for b in self.draws['base_cases']]
            expected=[sum(values[index] for index in draw)/3 for draw in self.draws['indices']]
            np.testing.assert_allclose(self.resampled[:,i],expected,rtol=0,atol=1e-12)
            self.assertAlmostEqual(row['mean_decrease'],sum(values)/3)
            self.assertAlmostEqual(row['p10_decrease'],float(np.quantile(values,.1)))
            self.assertAlmostEqual(row['mean_resample_q025'],float(np.quantile(expected,.025)))

    def test_summary_does_not_modify_inputs_and_ignores_training_target_values(self):
        analysis=deepcopy(self.report['analysis']);fit=deepcopy(self.fit);rows=deepcopy(self.rows);draws=deepcopy(self.draws)
        before=deepcopy((analysis,fit,rows,draws));summarize(analysis,fit,rows,draws)
        self.assertEqual((analysis,fit,rows,draws),before)
        for r in rows:
            if r['split']=='train':r['node_values']['C']=99.
        changed=summarize(analysis,fit,rows,draws)
        self.assertEqual(changed[0],self.report['summary'])
        np.testing.assert_array_equal(changed[1],self.resampled)

    def test_missing_duplicate_blocked_and_nonzero_intact_response_rejected(self):
        for kind in ('missing','duplicate','blocked','intact'):
            analysis=deepcopy(self.report['analysis'])
            if kind=='missing':analysis['effects'].pop()
            elif kind=='duplicate':analysis['effects'].append(deepcopy(analysis['effects'][0]))
            elif kind=='blocked':analysis['effects'][0]['effect']=None
            else:next(e for e in analysis['effects'] if e['retained_fraction']==1)['effect']=-.2
            with self.subTest(kind=kind),self.assertRaises(ModelError):
                summarize(analysis,self.fit,self.rows,self.draws)

    def test_changed_legacy_or_protocol_rejected(self):
        with self.assertRaises(ModelError):replay(self.fit,self.rows,self.legacy,self.draws,dict(PROTOCOL,model_refit=True))
        legacy=deepcopy(self.legacy);legacy['effects'][0]['effect']-=1
        with self.assertRaisesRegex(ModelError,'原首条基准的数值响应发生变化'):
            replay(self.fit,self.rows,legacy,self.draws,PROTOCOL)

    def test_bad_draws_or_base_case_leakage_rejected(self):
        for draws in (dict(base_cases=['wrong'],indices=[[0]]),dict(self.draws,indices=[[0,1,9]]),
                      dict(self.draws,indices=[[0.,1.,2.],[0.,0.,1.]])):
            with self.subTest(draws=draws),self.assertRaises(ModelError):
                summarize(self.report['analysis'],self.fit,self.rows,draws)
        rows=deepcopy(self.rows);rows[0]['base_case_id']='b7'
        with self.assertRaisesRegex(ModelError,'跨训练与留出'):
            summarize(self.report['analysis'],self.fit,rows,self.draws)

    def test_nonempty_output_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'keep').write_text('keep',encoding='utf-8')
            with self.assertRaises(ModelError):run('missing','missing','missing',out)
            self.assertEqual((out/'keep').read_text(encoding='utf-8'),'keep')


if __name__=='__main__':unittest.main()
