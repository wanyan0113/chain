"""Matched-repeat model comparisons and boundary/failure accounting."""
from copy import deepcopy
import json
from pathlib import Path
import statistics
import unittest

from fdna.generation_network_check import run_network_check
from fdna.generation_fitting import fit_joint_network
from fdna.generation_delta_comparison import run_comparison
from fdna.generation_delta_repeats import summarize_comparison, distribution, summary_csvs, render_summary
from fdna.model import ModelError

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'


class DeltaRepeatedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read(name): return json.loads((ROOT/name).read_text(encoding='utf-8'))
        gc=read('generation_network_check.json'); gc.update(base_cases=40,train_count=32,repetitions=2,noise_std=[0,2])
        cls.generation=run_network_check(read('generation_network.json'),read('generation_network_nodes.json'),gc)
        fc=read('generation_network_fit.json'); fc.update(noise_std=[0,2],repetitions=[1,2]); fc['settings']['starts']=4
        cls.baseline=fit_joint_network(cls.generation,fc)
        cls.config=read('generation_delta_repeats.json'); cls.config.update(noise_std=[0,2],repetitions=[1,2])
        cls.progress=[]
        cls.comparison=run_comparison(cls.generation,cls.baseline,cls.config,allow_repeats=True,progress=cls.progress.append)
        cls.report=summarize_comparison(cls.comparison)

    def test_scope_counts_progress_and_pairing(self):
        self.assertEqual(self.report['status'],'complete')
        self.assertEqual(self.report['counts'],dict(baseline_groups=8,new_expected_groups=16,new_complete_groups=16,new_local_fits=48))
        self.assertEqual(len(self.progress),16)
        self.assertEqual(self.comparison['comparison_scope'],'repeated')
        self.assertTrue(all(r['n']==2 for r in self.report['paired_model_changes']))
        self.assertFalse(self.report['policies']['baseline_refitted'])

    def test_paired_difference_sd_is_computed_from_matching_repeats(self):
        rows=[r for r in self.comparison['metrics'] if r['group']=='fixed_conditions' and r['noise_sigma']==2
              and r['node']=='R' and r['split']=='holdout' and r['target']=='clean' and r['prediction']=='recursive']
        lookup={(r['model'],r['repetition']):r['rmse'] for r in rows}
        values=[lookup['free_delta',rep]-lookup['base',rep] for rep in (1,2)]
        row=next(r for r in self.report['paired_model_changes'] if r['model']=='free_delta' and r['group']=='fixed_conditions'
            and r['noise_sigma']==2 and r['node']=='R' and r['split']=='holdout' and r['target']=='clean'
            and r['prediction']=='recursive' and r['quantity']=='rmse')
        self.assertAlmostEqual(row['mean'],statistics.mean(values))
        self.assertAlmostEqual(row['sd'],statistics.stdev(values))
        self.assertEqual(row['decreased_count'],sum(v < -1e-10 for v in values))

    def test_failed_group_is_counted_and_not_used_in_paired_average(self):
        c=deepcopy(self.comparison)
        fit=c['fits']['free_delta']; job=fit['jobs'][0]
        job['status']='blocked'; fit['status']='blocked'; c['status']='blocked'
        job['local_fits'][0]['fit']['status']='blocked'
        job['local_fits'][0]['fit']['receivers'][0]['status']='iteration_limit'
        key=(job['group'],job['noise_sigma'],job['repetition'])
        def keep(row): return row['model']!='free_delta' or (row['group'],row['noise_sigma'],row['repetition'])!=key
        c['metrics']=[r for r in c['metrics'] if keep(r)]
        c['error_decomposition']=[r for r in c['error_decomposition'] if keep(r)]
        result=summarize_comparison(c)
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['counts']['new_complete_groups'],15)
        self.assertEqual(result['counts']['new_expected_groups'],16)
        selected=[r for r in result['paired_model_changes'] if r['model']=='free_delta' and r['group']==key[0] and r['noise_sigma']==key[1]]
        self.assertTrue(all(r['n']==1 and r['expected_pairs']==2 for r in selected))
        self.assertEqual(len(result['failed_fits']),1)

    def test_boundaries_rank_and_prediction_counts_are_per_node_and_not_per_target(self):
        for row in self.report['delta_summary']:
            self.assertEqual(row['n'],2)
            self.assertLessEqual(row['lower_bound_count']+row['upper_bound_count'],2)
            self.assertLessEqual(row['rank_deficient_count'],2)
        for row in self.report['prediction_range']:
            self.assertEqual(row['count'],64 if row['split']=='train' else 16)
            self.assertGreaterEqual(row['outside_count'],0)
            self.assertLessEqual(row['outside_count'],row['count'])
        self.assertEqual(len(self.report['outside_predictions']),sum(r['outside_count'] for r in self.report['prediction_range']))

    def test_missing_duplicate_or_bad_identity_rejected(self):
        for kind in ('job_missing','all_node_metrics_missing','metric_duplicate','identity','state','count','outside'):
            c=deepcopy(self.comparison)
            if kind=='job_missing': c['fits']['fixed_delta']['jobs'].pop()
            if kind=='all_node_metrics_missing': c['metrics']=[r for r in c['metrics'] if r['node']!='T']
            if kind=='metric_duplicate': c['metrics'].append(deepcopy(c['metrics'][0]))
            if kind=='identity': c['error_decomposition'][0]['identity_error']=1
            if kind=='state': c['status']='blocked'
            if kind=='count': c['metrics'][0]['count']+=1
            if kind=='outside': c['metrics'][0]['outside_se_range']=-1
            with self.subTest(kind=kind),self.assertRaises(ModelError): summarize_comparison(c)

    def test_exports_quantiles_and_explanations(self):
        self.assertEqual(distribution([1,3])['p50'],2)
        self.assertIsNone(distribution([1])['sd'])
        self.assertIsNone(distribution([])['p95'])
        self.assertEqual(len(summary_csvs(self.report)),9)
        self.assertEqual(self.report['sample_sizes'],[dict(train=32,holdout=8)])
        text=render_summary(self.report)
        self.assertIn('不是两个SD相减',text)
        self.assertIn('触下界',text)
        json.dumps(self.report,allow_nan=False)


if __name__=='__main__': unittest.main()
