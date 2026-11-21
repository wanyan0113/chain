"""Frozen prediction audits and descriptive parameter probes must never refit."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np

from fdna.generation_network_check import run_network_check
from fdna.generation_fitting import fit_joint_network, make_fitting_model
from fdna.generation_delta_comparison import run_comparison
from fdna.generation_delta_repeats import summarize_comparison
from fdna.generation_delta_diagnostics import analyze_diagnostics, training_geometry, csv_outputs, validate_config, scalar_network
from fdna.additive import evaluate_additive, ADDITIVE_MODEL
from fdna.recovery_extended import fit_one
from fdna.model import ModelError
from fdna.structure import digest

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'


class DeltaDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read(name): return json.loads((ROOT/name).read_text(encoding='utf-8'))
        gc=read('generation_network_check.json'); gc.update(base_cases=40,train_count=32,noise_std=[0,2])
        cls.generation=run_network_check(read('generation_network.json'),read('generation_network_nodes.json'),gc)
        fc=read('generation_network_fit.json'); fc.update(noise_std=[0,2]); fc['settings']['starts']=4
        cls.baseline=fit_joint_network(cls.generation,fc)
        cc=read('generation_delta_repeats.json'); cc.update(noise_std=[0,2],repetitions=[1])
        cls.comparison=run_comparison(cls.generation,cls.baseline,cc,allow_repeats=True)
        cls.summary=summarize_comparison(cls.comparison)
        cls.config=read('generation_delta_diagnostics.json'); cls.config['plot_noise_std']=[0,2]
        with patch('fdna.fitting.fit_parameters',side_effect=AssertionError('No fitting during diagnosis')), \
             patch('fdna.generation_delta_comparison.fit_joint_network',side_effect=AssertionError('No refit')):
            cls.report,cls.points=analyze_diagnostics(cls.generation,cls.baseline,cls.comparison,cls.summary,cls.config)

    def test_all_predictions_and_metrics_audited_without_refitting(self):
        self.assertFalse(self.report['policies']['refitted'])
        self.assertEqual(self.report['counts']['audited_local_fits'],36)
        self.assertEqual(len(self.points),2880)
        self.assertEqual(self.report['counts']['verified_metric_rows'],len(self.comparison['metrics']))
        for p in self.points:
            self.assertAlmostEqual(p['predicted'],p['base_before_delta']+p['delta'])
            self.assertAlmostEqual(p['residual_clean'],p['predicted']-p['actual_clean'])
            self.assertAlmostEqual(p['residual_observed'],p['predicted']-p['actual_observed'])

    def test_outside_threshold_and_no_automatic_clipping(self):
        for p in self.points:
            if abs(p['predicted']-100)>1e-8:
                self.assertEqual(p['predicted']>100,p['delta']>p['upper_headroom'])
            if abs(p['predicted'])>1e-8:
                self.assertEqual(p['predicted']<0,p['delta']< -p['base_before_delta'])
        self.assertFalse(self.report['policies']['clipping'])

    def test_independent_scalar_dag_keeps_outside_intermediate_values(self):
        model=make_fitting_model(self.generation['network_snapshot'])
        for edge in model['edges']: edge.update(alpha=.5,beta=100.)
        deltas={'T':10.,'Q':-3.,'R':-2.}
        incoming={n:[e for e in model['edges'] if e['receiver']==n] for n in deltas}
        roots=dict.fromkeys(self.generation['topology']['roots'],100.)
        independent=scalar_network(incoming,roots,deltas,self.generation['topology']['order'])
        core=evaluate_additive(model,dict(id='test',basis='Scalar comparison',node_values=roots),
            dict(model=ADDITIVE_MODEL,basis='Explicit test',delta_values=deltas,output_policy='no_clipping'))
        self.assertEqual(independent['T']['value'],110.)
        for node in core['nodes']: self.assertEqual(independent[node['id']]['value'],node['value'])

    def test_near_starts_are_selected_only_using_training_loss(self):
        for geometry in self.report['geometries']:
            self.assertGreaterEqual(geometry['near_start_count'],1)
            self.assertTrue(all(a['training_rmse']<=geometry['selected_training_rmse']+1e-6 for a in geometry['near_starts']))
        fit=deepcopy(self.comparison['fits']['free_delta']['jobs'][0]['local_fits'][0]['fit'])
        before=training_geometry(fit,self.config)
        for row in fit['samples_snapshot']['records']:
            if row['split']=='holdout': row['node_values'][fit['receivers'][0]['receiver']]=0
        after=training_geometry(fit,self.config)
        self.assertEqual(before,after)

    def test_training_null_direction_does_not_guarantee_holdout_equivalence(self):
        cfg=json.loads((ROOT/'recovery_extended_check.json').read_text(encoding='utf-8')); cfg['starts']=4
        inputs=np.random.default_rng(31).uniform(0,40,(96,2))
        job=fit_one(cfg,cfg['probe'],'free_delta',1,0,inputs,['train']*64+['holdout']*32,np.zeros(96),probe=True)
        geometry=training_geometry(job['fit'],self.config); p=geometry['null_probe']
        self.assertEqual(p['kind'],'delta_compensation_direction')
        self.assertTrue(p['feasible']); self.assertFalse(p['equivalent_on_sampled_inputs'])
        self.assertLess(p['max_train_prediction_change'],1e-6)
        self.assertGreater(p['max_holdout_prediction_change'],1e-6)
        self.assertNotAlmostEqual(p['parameters_before'][-1],p['parameters_after'][-1],places=4)

    def test_analytical_cod_only_fixture_has_exact_beta_delta_compensation(self):
        cfg=json.loads((ROOT/'recovery_extended_check.json').read_text(encoding='utf-8')); cfg['starts']=4
        inputs=np.random.default_rng(31).uniform(0,40,(96,2))
        fit=fit_one(cfg,cfg['probe'],'free_delta',1,0,inputs,['train']*64+['holdout']*32,np.zeros(96),probe=True)['fit']
        # Analytical fixture: alpha=0 makes SoD=100 across the entire input box.
        # The preceding test retains the actual fitted example with branch switching.
        row=fit['receivers'][0]
        row['parameters']=[dict(feeder='A',alpha=0.,beta=20.),dict(feeder='B',alpha=0.,beta=35.)]
        row['delta']=-5.; row['attempts']=[dict(success=True,rmse=0.,parameters=[0.,0.,20.,35.,-5.])]
        row['attempt_selection']['index']=0
        geometry=training_geometry(fit,self.config); probe=geometry['null_probe']
        self.assertEqual(probe['kind'],'delta_compensation_direction')
        self.assertTrue(probe['feasible']); self.assertTrue(probe['equivalent_on_sampled_inputs'])

    def test_tampered_saved_prediction_and_attempt_logs_rejected(self):
        c=deepcopy(self.comparison)
        c['fits']['free_delta']['jobs'][0]['points'][0]['recursive_prediction']+=1
        summary=deepcopy(self.summary); summary['comparison_sha256']=digest(c)
        with self.assertRaisesRegex(ModelError,'保存预测'):
            analyze_diagnostics(self.generation,self.baseline,c,summary,self.config)
        fit=deepcopy(self.comparison['fits']['free_delta']['jobs'][0]['local_fits'][0]['fit'])
        row=fit['receivers'][0]; row['attempts'][row['attempt_selection']['index']]['parameters'][0]+=.2
        with self.assertRaises(ModelError): training_geometry(fit,self.config)

    def test_metrics_decomposition_and_exports_preserve_finite_values(self):
        self.assertEqual(len(csv_outputs(self.report,self.points)),8)
        for r in self.report['error_decomposition']:
            self.assertAlmostEqual(r['recursive_mse'],r['local_mse']+r['input_shift_mse']+r['cross_term'])
        json.dumps(self.report,allow_nan=False)
        for value in (True,0,float('nan')):
            cfg=deepcopy(self.config); cfg['null_step_fraction']=value
            with self.assertRaises(ModelError): validate_config(cfg)


if __name__=='__main__': unittest.main()
