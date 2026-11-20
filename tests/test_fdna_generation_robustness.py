"""Paired repeat statistics and transparent accounting of incomplete jobs."""
from copy import deepcopy
import json
from pathlib import Path
import statistics
import unittest

from fdna.generation_network_check import run_network_check
from fdna.generation_fitting import fit_joint_network
from fdna.generation_robustness import describe, summarize_repeats, summary_csvs, render_summary
from fdna.model import ModelError

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'


class RobustnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read(name): return json.loads((ROOT/name).read_text(encoding='utf-8'))
        config=read('generation_network_check.json')
        config.update(base_cases=20,train_count=16,repetitions=2,noise_std=[0,2],
                      groups={'unit_conditions':config['groups']['unit_conditions']})
        cls.generation=run_network_check(read('generation_network.json'),read('generation_network_nodes.json'),config)
        fc=read('generation_network_fit.json')
        fc.update(groups=['unit_conditions'],noise_std=[0,2],repetitions=[1,2])
        fc['settings']['starts']=3
        cls.progress=[]
        cls.fit=fit_joint_network(cls.generation,fc,progress=cls.progress.append)
        cls.report=summarize_repeats(cls.fit,cls.generation)

    def test_repeats_are_summary_unit_and_paired_differences_use_matching_ids(self):
        self.assertEqual(self.report['status'],'complete')
        self.assertEqual(len(self.progress),4)
        row=next(r for r in self.report['metrics'] if r['node']=='R' and r['noise_sigma']==2
                 and r['mode']=='recursive' and r['target']=='clean')
        values={}
        for j in self.fit['jobs']:
            values[j['noise_sigma'],j['repetition']]=next(m['rmse'] for m in j['metrics']
                if m['node']=='R' and m['split']=='holdout' and m['mode']=='recursive' and m['target']=='clean')
        self.assertEqual(row['n'],2)
        self.assertAlmostEqual(row['mean'],statistics.mean(values[2,r] for r in [1,2]))
        self.assertAlmostEqual(row['sd'],statistics.stdev(values[2,r] for r in [1,2]))
        paired=next(r for r in self.report['paired_rmse_changes'] if r['node']=='R' and r['noise_sigma']==2
                    and r['mode']=='recursive' and r['target']=='clean')
        self.assertAlmostEqual(paired['mean'],statistics.mean(values[2,r]-values[0,r] for r in [1,2]))
        reordered=deepcopy(self.fit); reordered['jobs'].reverse()
        # Full fit digest records declaration order; numerical aggregation must not depend on it.
        result=summarize_repeats(reordered,self.generation)
        self.assertEqual(result['metrics'],self.report['metrics'])
        self.assertEqual(result['paired_rmse_changes'],self.report['paired_rmse_changes'])

    def test_failed_jobs_remain_in_denominator_and_pair_count(self):
        fit=deepcopy(self.fit); fit['jobs'][0]['status']='blocked'; fit['status']='blocked'
        report=summarize_repeats(fit,self.generation)
        self.assertEqual(report['expected_jobs'],4); self.assertEqual(report['complete_jobs'],3)
        self.assertEqual(report['status'],'blocked')
        self.assertTrue(any(r['n']==1 and r['expected_repeats']==2 for r in report['metrics']))
        self.assertTrue(any(r['n']==1 and r['expected_pairs']==2 for r in report['paired_rmse_changes']))

    def test_duplicate_missing_and_wrong_version_rejected(self):
        for kind in ('duplicate','missing','wrong_version','metric_missing'):
            fit=deepcopy(self.fit)
            if kind=='duplicate': fit['jobs'].append(deepcopy(fit['jobs'][0]))
            if kind=='missing': fit['jobs'].pop()
            if kind=='wrong_version': fit['generation_report_sha256']='incorrect'
            if kind=='metric_missing': fit['jobs'][0]['metrics']=[m for m in fit['jobs'][0]['metrics'] if m['split']!='holdout']
            with self.subTest(kind=kind),self.assertRaises(ModelError): summarize_repeats(fit,self.generation)

    def test_clipping_audit_uses_raw_noisy_value_and_holdout_only(self):
        for row in self.report['observation_clipping']:
            observations=[r for r in self.generation['observations'] if r['group']==row['group']
                          and r['noise_sigma']==row['noise_sigma'] and r['split']=='holdout']
            self.assertEqual(row['count'],8)
            self.assertEqual(row['clipped_count'],sum(not 0<=r['node_values_clean'][row['node']]+
                r['observation_noise'][row['node']]<=100 for r in observations))
        self.assertTrue(all(r['clipped_count']==0 for r in self.report['observation_clipping'] if r['noise_sigma']==0))

    def test_sd_is_sample_sd_and_not_zero_for_single_repeat(self):
        self.assertEqual(describe([1,3])['sd'],2**.5)
        self.assertIsNone(describe([1])['sd'])
        self.assertIsNone(describe([])['mean'])

    def test_reports_include_limits_and_parameter_counts(self):
        json.dumps(self.report,allow_nan=False)
        self.assertEqual(len(summary_csvs(self.report)),6)
        self.assertTrue(all(r['n']==2 for r in self.report['parameter_spread']))
        text=render_summary(self.report)
        self.assertIn('不是置信区间',text)
        self.assertIn('未检验供给端测量误差',text)


if __name__=='__main__': unittest.main()
