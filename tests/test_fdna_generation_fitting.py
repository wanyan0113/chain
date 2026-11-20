"""Independent network fitting: leakage prevention and model mismatch checks."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fdna.model import ModelError
from fdna.generation_network_check import run_network_check
from fdna.generation_fitting import (audit_generation, make_fitting_model,
    project_local_samples, fit_joint_network, _stats, fitting_csvs, render_fit_summary)
from fdna.engine import evaluate
from fdna.fitting import fit_parameters

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'


class JointFittingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read(name): return json.loads((ROOT/name).read_text(encoding='utf-8'))
        cls.network=read('generation_network.json')
        config=read('generation_network_check.json')
        config.update(base_cases=40,train_count=32,noise_std=[0,2])
        cls.generation=run_network_check(cls.network,read('generation_network_nodes.json'),config)
        cls.config=read('generation_network_fit.json')
        cls.config.update(groups=['unit_conditions'],noise_std=[0,2])
        cls.config['settings'].update(starts=4)
        cls.calls=[]
        def guarded(model,scenario):
            cls.calls.append(deepcopy(scenario))
            if set(scenario['node_values'])!=set(cls.generation['topology']['roots']):
                raise AssertionError('Intermediate truth leaked into recursive prediction')
            return evaluate(model,scenario)
        with patch('fdna.generation_fitting.evaluate',side_effect=guarded):
            cls.result=fit_joint_network(cls.generation,cls.config)

    def test_projection_uses_clean_feeders_and_observed_receiver(self):
        full=make_fitting_model(self.network)
        rows=[r for r in self.generation['observations'] if r['noise_sigma']==2]
        model,samples=project_local_samples(full,rows,'Q')
        self.assertTrue(all(e['receiver']=='Q' and e['alpha'] is None and e['beta'] is None for e in model['edges']))
        for row,sample in zip(rows,samples['records']):
            self.assertEqual(sample['split'],row['split'])
            self.assertEqual(sample['node_values']['Q'],row['node_values_observed']['Q'])
            for n in ('T','C_aux'):
                self.assertEqual(sample['node_values'][n],row['node_values_clean'][n])
        self.assertTrue(any(r['node_values_clean']['Q']!=r['node_values_observed']['Q'] for r in rows))

    def test_no_parameter_truth_or_intermediate_truth_used(self):
        self.assertEqual(self.result['status'],'complete')
        self.assertEqual(len(self.calls),80)
        self.assertFalse(self.result['policies']['generator_parameters_used_as_initialization'])
        self.assertFalse(self.result['policies']['holdout_used_for_optimization'])
        self.assertTrue(all(e['alpha'] is None and e['beta'] is None for e in make_fitting_model(self.network)['edges']))

    def test_conditions_and_noise_jobs_keep_frozen_network_split(self):
        self.assertEqual([j['noise_sigma'] for j in self.result['jobs']],[0,2])
        for job in self.result['jobs']:
            self.assertEqual(job['counts'],dict(train=32,holdout=8))
            local_splits=[]
            for local in job['local_fits']:
                local_splits.append({r['id']:r['split'] for r in local['fit']['samples_snapshot']['records']})
                self.assertEqual(len(local['model']['edges']),2)
            self.assertEqual(local_splits[0],local_splits[1])
            self.assertEqual(local_splits[1],local_splits[2])

    def test_replay_rejects_tampered_generation(self):
        bad=deepcopy(self.generation)
        bad['observations'][0]['node_values_clean']['T']+=1
        with self.assertRaisesRegex(ModelError,'重算不一致'): audit_generation(bad)

    def test_metrics_match_independent_calculation_and_bounds(self):
        for job in self.result['jobs']:
            for metric in job['metrics']:
                points=[p for p in job['points'] if p['node']==metric['node'] and p['split']==metric['split']]
                targets=[p['actual_'+metric['target']] for p in points]
                predicted=[p[metric['mode']+'_prediction'] for p in points]
                self.assertEqual(metric['rmse'],_stats(targets,predicted)['rmse'])
                if metric['mode']=='recursive': self.assertIsNone(metric['rmse_lower_bound'])
                else:
                    bound=(sum(max(0,p['actual_min_parent']-p['actual_'+metric['target']])**2 for p in points)/len(points))**.5
                    self.assertAlmostEqual(metric['rmse_lower_bound'],bound)
                    self.assertGreaterEqual(metric['rmse']+1e-8,bound)

    def test_ordinary_r_squared_allows_negative_and_constant_is_undefined(self):
        self.assertLess(_stats([1,2],[10,20])['r_squared'],0)
        self.assertIsNone(_stats([2,2],[2,3])['r_squared'])

    def test_rejects_missing_selection_and_unsupported_model(self):
        for key,value in [('groups',['absent']),('noise_std',[8]),('repetitions',[2])]:
            bad=deepcopy(self.config); bad[key]=value
            with self.assertRaises(ModelError): fit_joint_network(self.generation,bad)
        bad=deepcopy(self.config); bad['settings']['model']='unsupported_delta'
        with self.assertRaises(ModelError): fit_joint_network(self.generation,bad)

    def test_insufficient_training_does_not_propagate(self):
        config=deepcopy(self.generation['config_snapshot']); config.update(base_cases=20,train_count=3)
        small=run_network_check(self.network,self.generation['node_configs_snapshot'],config)
        with patch('fdna.generation_fitting.evaluate',side_effect=AssertionError('Must not recurse')):
            result=fit_joint_network(small,self.config)
        self.assertEqual(result['status'],'blocked')
        for job in result['jobs']:
            self.assertEqual(job['points'],[])
            self.assertIsNone(job['fitted_model'])

    def test_report_exports_are_finite_and_explain_limits(self):
        json.dumps(self.result,allow_nan=False)
        self.assertEqual(len(fitting_csvs(self.result)),3)
        summary=render_fit_summary(self.result)
        self.assertIn('不能将单点参数当作唯一关系强度',summary)
        self.assertIn('不称为原文伪R²',summary)

    def test_holdout_targets_cannot_change_estimated_parameters(self):
        local=self.result['jobs'][0]['local_fits'][0]
        samples=deepcopy(local['fit']['samples_snapshot'])
        for row in samples['records']:
            if row['split']=='holdout': row['node_values'][local['receiver']]=0
        refit=fit_parameters(local['model'],samples,self.config['settings'])
        self.assertEqual(refit['receivers'][0]['parameters'],local['fit']['receivers'][0]['parameters'])

    def test_iteration_limit_does_not_silently_enable_recursion(self):
        def unfinished(model,samples,settings):
            fit=fit_parameters(model,samples,settings)
            fit['status']='blocked'
            fit['receivers'][0]['status']='iteration_limit'
            return fit
        config=deepcopy(self.config); config['noise_std']=[0]
        with patch('fdna.generation_fitting.fit_parameters',side_effect=unfinished), \
             patch('fdna.generation_fitting.evaluate',side_effect=AssertionError('Must not recurse')):
            result=fit_joint_network(self.generation,config)
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['jobs'][0]['metrics'],[])


if __name__=='__main__': unittest.main()
