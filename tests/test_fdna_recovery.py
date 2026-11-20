"""Recovery with unknown fit parameters, held-out inputs and nonidentifiable controls."""
from contextlib import redirect_stdout
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fdna import ModelError
from fdna.__main__ import main
from fdna.recovery import independent_forward, recovery_csvs, render_recovery, run_recovery, validate_recovery

ROOT=Path(__file__).resolve().parents[1]


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config=json.loads((ROOT/'data/fdna/recovery_check.json').read_text(encoding='utf-8'))
        cls.config.update(random_train=128,random_holdout=64,starts=6)
        cls.config['cases']=cls.config['cases'][:1]
        cls.report=run_recovery(cls.config)

    def test_scalar_formula_against_hand_computed_sod_and_cod(self):
        self.assertEqual(independent_forward(20,80,[.4,.65],[20,35]),(40,77.5,40,115))
        self.assertEqual(independent_forward(80,80,[.4,.65],[20,35]),(89.5,89.5,100,115))

    def test_recovers_four_parameters_with_blinded_model_and_heldout_inputs(self):
        r=self.report
        self.assertEqual(r['status'],'complete')
        case=r['cases'][0]
        self.assertEqual(case['outcome'],'passed')
        self.assertTrue(all(e['alpha'] is None and e['beta'] is None for e in case['fitting_case']['edges']))
        self.assertTrue(all(case['criteria'].values()))
        self.assertLess(max(p['absolute_error'] for p in case['parameter_comparison']),1e-5)
        self.assertLess(case['metrics']['holdout']['rmse'],1e-6)
        samples=case['fit_report']['samples_snapshot']['records']
        self.assertEqual(len(samples),192)
        inputs={split:{(v['node_values']['A'],v['node_values']['B']) for v in samples if v['split']==split}
                for split in ('train','holdout')}
        self.assertFalse(inputs['train'] & inputs['holdout'])
        self.assertEqual(case['true_training_coverage']['local_jacobian_rank'],4)
        self.assertFalse(r['policies']['truth_used_for_initialization'])
        self.assertFalse(r['policies']['reported_table7_used'])

    def test_holdout_size_does_not_change_training_draws_or_optimization(self):
        config=deepcopy(self.config)
        config['random_holdout']=16
        changed=run_recovery(config)['cases'][0]['fit_report']
        original=self.report['cases'][0]['fit_report']
        self.assertEqual(original['receivers'][0]['attempts'],changed['receivers'][0]['attempts'])
        self.assertEqual([r for r in original['samples_snapshot']['records'] if r['split']=='train'],
                         [r for r in changed['samples_snapshot']['records'] if r['split']=='train'])

    def test_unidentifiable_beta_is_reported_without_claiming_recovery(self):
        config=deepcopy(self.config)
        config['cases'][0].update(alpha=[.4,.65],beta=[100,100])
        case=run_recovery(config)['cases'][0]
        self.assertEqual(case['outcome'],'insufficient_identifiability')
        self.assertLess(case['true_training_coverage']['local_jacobian_rank'],4)
        self.assertEqual(case['true_training_coverage']['counts']['cod_A'],0)
        self.assertEqual(case['true_training_coverage']['counts']['cod_B'],0)
        self.assertFalse(case['criteria']['training_supports_local_identification'])

    def test_contract_rejects_invalid_shapes_bounds_counts_and_nonfinite_values(self):
        for kind in ('shape','bounds','boolean','nan','duplicate','holdout'):
            config=deepcopy(self.config)
            if kind=='shape': config['cases'][0]['alpha']=[.4]
            if kind=='bounds': config['cases'][0]['beta']=[-1,35]
            if kind=='boolean': config['seed']=True
            if kind=='nan': config['cases'][0]['alpha'][0]=float('nan')
            if kind=='duplicate': config['cases']*=2
            if kind=='holdout': config['random_holdout']=0
            with self.subTest(kind=kind),self.assertRaises(ModelError):
                validate_recovery(config)

    def test_exports_recomputable_rows_and_cli_does_not_overwrite(self):
        before=deepcopy(self.report)
        files=recovery_csvs(self.report)
        predictions=list(csv.DictReader(io.StringIO(files['predictions.csv'])))
        self.assertEqual(len(predictions),192)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(files['parameter_comparison.csv'])))),4)
        for p in predictions:
            self.assertAlmostEqual(float(p['residual']),float(p['predicted'])-float(p['observed']))
        self.assertIn('独立验证样本64条',render_recovery(self.report))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=root/'config.json'
            config.write_text(json.dumps(self.config),encoding='utf-8')
            args=['recovery','--config',str(config),'--output-dir',str(root/'out')]
            with patch('fdna.__main__.run_recovery',return_value=self.report),redirect_stdout(io.StringIO()):
                self.assertEqual(main(args),0)
                snapshot=(root/'out/fdna_report.json').read_bytes()
                self.assertEqual(main(args),2)
                self.assertEqual((root/'out/fdna_report.json').read_bytes(),snapshot)
            self.assertTrue((root/'out/recovery_summary.md').is_file())
        self.assertEqual(self.report,before)


if __name__=='__main__':
    unittest.main()
