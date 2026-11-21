from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fdna.generation_process import run_process_network_check
from fdna.generation_fitting import fit_joint_network
from fdna.generation_delta_comparison import run_comparison
from fdna.generation_delta_repeats import summarize_comparison
from fdna.generation_extended_diagnostics import analyze_source,feeder_quality,validate_bundle_config
from fdna.model import ModelError

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'
def read(n):return json.loads((ROOT/n).read_text(encoding='utf-8'))


class ExtendedDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=read('generation_process_conditions.json')
        c['reference_experiment']['base_experiment'].update(base_cases=40,train_count=32,repetitions=1,noise_std=[0,2])
        cls.g=run_process_network_check(read('generation_network.json'),read('generation_network_nodes.json'),c)
        fc=read('generation_process_fit.json');fc.update(noise_std=[0,2],repetitions=[1]);fc['settings']['starts']=4
        dc=read('generation_process_delta.json');dc.update(noise_std=[0,2],repetitions=[1])
        cls.base=fit_joint_network(cls.g,fc);cls.comparison=run_comparison(cls.g,cls.base,dc,allow_repeats=True)
        cls.summary=summarize_comparison(cls.comparison)
        cls.config=read('generation_extended_diagnostics.json');cls.config['policy']['plot_noise_std']=[0,2]
        cls.source=deepcopy(cls.config['sources'][1])
        with patch('fdna.fitting.fit_parameters',side_effect=AssertionError('diagnostics must not fit')):
            cls.report,cls.points,cls.quality=analyze_source(cls.g,cls.base,cls.comparison,cls.summary,
                cls.source,cls.config['policy'],cls.config['low_input_thresholds'])

    def test_frozen_audit_and_scope(self):
        self.assertEqual(self.report['counts']['audited_local_fits'],18)
        self.assertEqual(self.report['quality_bound_checks'],12)
        self.assertEqual(self.report['counts']['recomputed_prediction_rows'],1440)
        self.assertFalse(self.report['policies']['refitted'])
        self.assertEqual({r['group'] for r in self.quality['node_quality']},{'varying_conditions_process_2'})

    def test_quality_counts_unique_states_not_noise_versions(self):
        self.assertEqual(len(self.quality['node_quality']),6)
        self.assertEqual(len(self.quality['feeder_coverage']),12)
        self.assertEqual(len(self.quality['feeder_correlations']),6)
        self.assertEqual({r['count'] for r in self.quality['node_quality']},{8,32})
        grouped={}
        for r in self.quality['feeder_coverage']:
            key=r['group'],r['repetition'],r['split'],r['node']
            grouped.setdefault(key,[]).append(r)
        for rows in grouped.values():
            self.assertAlmostEqual(sum(r['unique_minimum_fraction'] for r in rows),1)
        for r in self.quality['node_quality']:
            self.assertLessEqual(r['min_input_below_20_fraction'],r['min_input_below_40_fraction'])

    def test_quality_invariant_to_noise_grid_and_row_order(self):
        g=deepcopy(self.g);g['config_snapshot']['noise_std'].reverse();g['observations'].reverse();g['clean_runs'].reverse()
        q=feeder_quality(g,self.source['expected_groups'],[20,40])
        # All per-clean-state statistics are independent of observation grid and ordering.
        for key in ('node_quality','feeder_coverage','feeder_correlations'):
            for actual,old in zip(q[key],self.quality[key]):
                for name,value in actual.items():
                    if isinstance(value,float):self.assertAlmostEqual(value,old[name])
                    else:self.assertEqual(value,old[name])

    def test_constant_inputs_keep_undefined_correlation(self):
        g=deepcopy(self.g)
        for row in g['clean_runs']:row['node_values_clean']['A']=0
        q=feeder_quality(g,self.source['expected_groups'],[20,40])
        correlations=[r for r in q['feeder_correlations'] if r['node']=='T']
        self.assertTrue(all(r['pearson_r'] is None and r['undefined_reason']=='constant_input' for r in correlations))

    def test_rejects_duplicate_sources_thresholds_and_wrong_selection(self):
        for kind in ('source','group','threshold','bool'):
            c=deepcopy(self.config)
            if kind=='source':c['sources'].append(c['sources'][0])
            elif kind=='group':c['sources'][1]['expected_groups']=c['sources'][0]['expected_groups']
            elif kind=='threshold':c['low_input_thresholds']=[20,20]
            else:c['low_input_thresholds']=[True]
            with self.assertRaises(ModelError):validate_bundle_config(c)
        source=deepcopy(self.source);source['expected_groups']=['interaction_015']
        with self.assertRaises(ModelError):analyze_source(self.g,self.base,self.comparison,self.summary,source,
            self.config['policy'],[20,40])

    def test_geometry_summary_keeps_training_only_and_delta_cases_separate(self):
        from fdna.generation_extended_diagnostics_report import summarize_geometries
        def row(rep,kind,train,hold):
            return dict(model='free_delta',group='g',noise_sigma=0,repetition=rep,node='R',rank=2,parameter_count=5,
                null_probe=dict(feasible=True,kind=kind,max_train_prediction_change=train,max_holdout_prediction_change=hold,
                    equivalent_on_sampled_inputs=max(train,hold)<=1e-6))
        report={'geometries':[row(1,'inactive_parameter_direction',0,0),row(2,'delta_compensation_direction',0,.1)]}
        summary=summarize_geometries([report],1e-6)[0]
        self.assertEqual(summary['equivalent_train_and_holdout'],1)
        self.assertEqual(summary['train_equivalent_holdout_changed'],1)
        self.assertEqual(summary['confirmed_delta_equivalence'],0)
        self.assertEqual(summary['feasible_delta_directions'],1)
        with self.assertRaises(ModelError):summarize_geometries([report,report],1e-6)
        from fdna.generation_extended_diagnostics_report import confirmed_delta_paths
        g=deepcopy(report['geometries'][1]);g['parameter_names']=['beta:A','delta']
        g['null_probe'].update(parameters_before=[2,-3],parameters_after=[1.9,-2.9],
            equivalent_on_sampled_inputs=True,max_holdout_prediction_change=0)
        self.assertAlmostEqual(confirmed_delta_paths([dict(geometries=[g])],1e-6)[0]['delta_change'],.1)
        g['null_probe']['parameters_after'][-1]=-3
        with self.assertRaises(ModelError):confirmed_delta_paths([dict(geometries=[g])],1e-6)


if __name__=='__main__':unittest.main()
