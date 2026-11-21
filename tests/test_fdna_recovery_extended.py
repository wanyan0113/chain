"""Noise pairing, parameter truth isolation and deliberately unidentifiable data."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from fdna.model import ModelError
from fdna.recovery_extended import run_extended_recovery, fit_one, validate_config, main, csv_outputs

ROOT=Path(__file__).resolve().parents[1]


class ExtendedRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config=json.loads((ROOT/'data/fdna/recovery_extended_check.json').read_text(encoding='utf-8'))
        cls.config.update(repetitions=2,random_train=128,random_holdout=64,noise_std=[0,2],starts=4)
        cls.config['cases']=cls.config['cases'][:1]
        cls.result=run_extended_recovery(cls.config)

    def test_noiseless_base_fixed_and_joint_recovery(self):
        self.assertEqual(self.result['status'],'complete')
        self.assertEqual(self.result['counts'],dict(expected_jobs=12,converged_jobs=12,noiseless_jobs=6,noiseless_recovery_passed=6))
        for job in self.result['jobs']:
            if job['noise_sigma']==0: self.assertTrue(all(job['noiseless_criteria'].values()))
            else: self.assertIsNone(job['noiseless_criteria'])

    def test_fixed_and_free_share_exact_data_and_truth_not_used_as_initialization(self):
        for rep in (1,2):
            for sigma in (0,2):
                jobs={j['mode']:j for j in self.result['jobs'] if j['repetition']==rep and j['noise_sigma']==sigma}
                self.assertEqual(jobs['fixed_delta']['fit']['samples_snapshot']['records'],jobs['free_delta']['fit']['samples_snapshot']['records'])
                self.assertEqual(jobs['base']['generation_delta'],0)
                for mode,job in jobs.items():
                    self.assertTrue(all(e['alpha'] is None and e['beta'] is None for e in job['model']['edges']))
                    self.assertNotIn('initialization',job['fit']['settings_snapshot'])
                    count=5 if mode=='free_delta' else 4
                    self.assertTrue(all(len(a['initial'])==count for a in job['fit']['receivers'][0]['attempts']))
                delta=next(p for p in jobs['fixed_delta']['parameter_comparison'] if p['parameter']=='delta')
                self.assertFalse(delta['estimated'])

    def test_noise_versions_preserve_inputs_and_repeats_resample(self):
        jobs={(j['mode'],j['repetition'],j['noise_sigma']):j for j in self.result['jobs']}
        a=jobs['base',1,0]['points']; b=jobs['base',1,2]['points']; c=jobs['base',2,0]['points']
        self.assertEqual([(r['id'],r['A'],r['B'],r['split'],r['actual_clean']) for r in a],
                         [(r['id'],r['A'],r['B'],r['split'],r['actual_clean']) for r in b])
        self.assertNotEqual([r['A'] for r in a],[r['A'] for r in c])
        self.assertTrue(any(r['actual_clean']!=r['actual_observed'] for r in b))

    def test_probe_has_small_prediction_error_without_unique_parameters(self):
        probe=self.result['identifiability_probe']
        self.assertTrue(self.result['probe_expected_behavior_passed'])
        self.assertEqual(probe['truth_coverage']['rank'],2)
        self.assertEqual(probe['interpretation'],'rank_deficient_parameter_recovery_not_required')
        for row in probe['identifiable_combinations']:
            self.assertAlmostEqual(row['known_beta_plus_delta'],row['fitted_beta_plus_delta'],places=6)

    def test_holdout_noise_does_not_change_any_estimated_parameters(self):
        inputs=np.random.default_rng(91).uniform(0,100,(192,2)); splits=['train']*128+['holdout']*64
        z=np.random.default_rng(92).standard_normal(192)
        first=fit_one(self.config,self.config['cases'][0],'free_delta',1,2,inputs,splits,z)
        z[128:]+=20
        changed=fit_one(self.config,self.config['cases'][0],'free_delta',1,2,inputs,splits,z)
        self.assertEqual(first['fit']['receivers'][0]['parameters'],changed['fit']['receivers'][0]['parameters'])
        self.assertEqual(first['fit']['receivers'][0]['delta'],changed['fit']['receivers'][0]['delta'])

    def test_invalid_config_and_out_of_range_clean_truth_rejected(self):
        for key,value in [('noise_std',[2]),('delta_bounds',[10,-10]),('unknown',1)]:
            cfg=deepcopy(self.config); cfg[key]=value
            with self.subTest(key=key),self.assertRaises(ModelError): validate_config(cfg)
        truth=deepcopy(self.config['cases'][0]); truth['delta']=5
        with self.assertRaisesRegex(ModelError,'生成真值超出'):
            fit_one(self.config,truth,'free_delta',1,0,np.array([[100,100]]),['train'],np.zeros(1))

    def test_quantiles_and_single_repeat_uncertainty_are_not_confidence_intervals(self):
        row=next(r for r in self.result['summary'] if r['mode']=='base' and r['noise_sigma']==2 and r['quantity']=='clean_rmse')
        values=[next(m['rmse'] for m in j['metrics'] if m['split']=='holdout' and m['target']=='clean')
            for j in self.result['jobs'] if j['mode']=='base' and j['noise_sigma']==2]
        self.assertAlmostEqual(row['p50'],float(np.percentile(values,50)))
        self.assertEqual(row['n'],2)
        self.assertEqual(len(csv_outputs(self.result)),4)
        json.dumps(self.result,allow_nan=False)

    def test_existing_output_is_rejected_before_computation(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp); (path/'existing.txt').write_text('keep',encoding='utf-8')
            with patch('fdna.recovery_extended.run_extended_recovery',side_effect=AssertionError('Do not refit')),redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--config',str(ROOT/'data/fdna/recovery_extended_check.json'),'--output-dir',temp]),2)
            self.assertEqual((path/'existing.txt').read_text(encoding='utf-8'),'keep')


if __name__=='__main__': unittest.main()
