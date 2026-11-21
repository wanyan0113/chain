"""Same-data delta contrast, frozen baseline auditing and MSE cross terms."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fdna.additive import evaluate_additive
from fdna.generation_network_check import run_network_check
from fdna.generation_fitting import fit_joint_network
from fdna.generation_delta_comparison import run_comparison, audit_frozen_baseline
from fdna.model import ModelError

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'


class DeltaComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read(name): return json.loads((ROOT/name).read_text(encoding='utf-8'))
        gc=read('generation_network_check.json'); gc.update(base_cases=40,train_count=32,noise_std=[0])
        cls.generation=run_network_check(read('generation_network.json'),read('generation_network_nodes.json'),gc)
        fc=read('generation_network_fit.json'); fc['settings']['starts']=4
        cls.baseline=fit_joint_network(cls.generation,fc)
        cls.config=read('generation_delta_comparison.json')
        cls.calls=[]
        def roots_only(model,scenario,extension):
            if set(scenario['node_values'])!=set(cls.generation['topology']['roots']):
                raise AssertionError('Actual intermediate values leaked')
            cls.calls.append(deepcopy(extension))
            return evaluate_additive(model,scenario,extension)
        with patch('fdna.generation_fitting.evaluate_additive',side_effect=roots_only):
            cls.result=run_comparison(cls.generation,cls.baseline,cls.config)

    def test_same_data_for_three_models_and_baseline_not_refitted(self):
        self.assertEqual(self.result['status'],'complete')
        self.assertFalse(self.result['policies']['baseline_refitted'])
        self.assertEqual(self.result['baseline_jobs_audited'],2)
        for mode,fit in self.result['fits'].items():
            for job,base in zip(fit['jobs'],self.baseline['jobs']):
                for local,old in zip(job['local_fits'],base['local_fits']):
                    self.assertEqual(local['fit']['samples_snapshot'],old['fit']['samples_snapshot'])
                    settings=local['fit']['settings_snapshot']
                    self.assertEqual(settings['model'],'fdna_additive_delta')
                    if mode=='fixed_delta':
                        self.assertEqual(set(settings['delta_values']),{local['receiver']})
                        self.assertEqual(local['fit']['receivers'][0]['delta'],-5)
                    else: self.assertEqual(set(settings['delta_bounds']),{local['receiver']})

    def test_additive_recursion_uses_only_roots_and_no_old_base_bound(self):
        self.assertEqual(len(self.calls),160)
        for extension in self.calls: self.assertEqual(extension['output_policy'],'no_clipping')
        for fit in self.result['fits'].values():
            for job in fit['jobs']:
                self.assertTrue(all(m['rmse_lower_bound'] is None for m in job['metrics']))

    def test_error_decomposition_keeps_cross_term_and_exact_identity(self):
        self.assertTrue(any(abs(r['cross_term'])>1e-5 for r in self.result['error_decomposition']))
        self.assertTrue(all(r['identity_error']<1e-8 for r in self.result['error_decomposition']))
        for row in self.result['error_decomposition']:
            self.assertAlmostEqual(row['recursive_mse'],row['local_mse']+row['input_replacement_shift_mse']+row['cross_term'])

    def test_comparison_changes_use_same_split_target_and_prediction(self):
        for row in self.result['changes']:
            self.assertAlmostEqual(row['rmse_change'],row['rmse']-row['baseline_rmse'])
            self.assertAlmostEqual(row['mae_change'],row['mae']-row['baseline_mae'])
        self.assertEqual(len(self.result['parameter_changes']),24)
        json.dumps(self.result,allow_nan=False)

    def test_tampered_frozen_baseline_or_target_rejected(self):
        for kind in ('version','target','missing','state'):
            baseline=deepcopy(self.baseline)
            if kind=='version': baseline['generation_report_sha256']='wrong'
            if kind=='target': baseline['jobs'][0]['local_fits'][0]['fit']['samples_snapshot']['records'][0]['node_values']['Q']+=1
            if kind=='missing': baseline['jobs'].pop()
            if kind=='state': baseline['jobs'][0]['status']='blocked'
            with self.subTest(kind=kind),self.assertRaises(ModelError): audit_frozen_baseline(self.generation,baseline,self.config)

    def test_invalid_fixed_delta_or_multi_repeat_first_round_rejected(self):
        for kind in ('missing','bool','bounds','multiple'):
            config=deepcopy(self.config)
            if kind=='missing': config['fixed_delta'].pop('T')
            if kind=='bool': config['fixed_delta']['T']=True
            if kind=='bounds': config['free_delta_bounds']['T']=[1,-1]
            if kind=='multiple': config['repetitions']=[1,2]
            with self.subTest(kind=kind),self.assertRaises(ModelError): run_comparison(self.generation,self.baseline,config)


if __name__=='__main__': unittest.main()
