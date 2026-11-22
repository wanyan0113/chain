"""Input preparation never calls the optimizer or manufactures node SE."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fdna.model import ModelError
from fdna.network_preflight import inspect_data,template_outputs,main
from fdna.workflow_common import settings
from fdna.network_workflow import read_samples


class NetworkPreflightTests(unittest.TestCase):
    def setUp(self):
        self.network=dict(id='input',nodes=['A','B','T'],edges=[dict(feeder='A',receiver='T'),dict(feeder='B',receiver='T')])
        self.config=dict(input_role='observed',pool_scenarios=False,data_kind='user_supplied',models={'base':settings('base',['T'])})
        self.rows=[dict(sample_id=f's{i}',base_case_id=f'b{i}',scenario_id='one',fit_group='g',split='train' if i<8 else 'holdout',
            node_values=dict(A=float(10+i*5),B=float(80-i*3),T=float(30+i))) for i in range(10)]

    def test_preflight_reports_counts_without_optimizer(self):
        with patch('fdna.network_workflow.fit_parameters',side_effect=AssertionError('must not fit')):
            report=inspect_data(self.network,self.rows,self.config)
        self.assertEqual(report['status'],'ready_with_warnings')
        self.assertIn('HOLDOUT_OUTSIDE_TRAIN_RANGE:A',report['local_readiness'][0]['warnings'])
        row=report['local_readiness'][0]
        self.assertEqual((row['train_count'],row['holdout_count'],row['parameter_count']),(8,2,4))
        self.assertFalse(report['policies']['optimizer_called'])
        self.assertFalse(report['policies']['identifiability_established'])

    def test_blank_templates_cannot_be_mistaken_for_measured_data(self):
        outputs=template_outputs(self.network)
        rows=json.loads(outputs['SE填写模板.json']);config=json.loads(outputs['基础式拟合配置.json'])
        self.assertTrue(all(v is None for r in rows for v in r['node_values'].values()))
        report=inspect_data(self.network,rows,config)
        self.assertEqual(report['status'],'needs_data')
        self.assertEqual(report['local_readiness'][0]['train_count'],0)

    def test_missing_values_and_independent_sample_counts_are_preserved(self):
        rows=deepcopy(self.rows);rows[0]['node_values']['A']=None;rows[1]['base_case_id']=rows[2]['base_case_id']
        report=inspect_data(self.network,rows,self.config)
        row=report['local_readiness'][0]
        self.assertEqual(row['train_count'],7)
        self.assertEqual(row['independent_train_base_cases'],6)
        self.assertEqual(report['omitted_local_records'][0]['missing_nodes'],['A'])

    def test_constant_and_repeated_inputs_warn_without_claiming_identification(self):
        rows=deepcopy(self.rows)
        for r in rows:r['node_values']['A']=50.;r['node_values']['B']=60.
        report=inspect_data(self.network,rows,self.config)
        self.assertEqual(report['status'],'ready_with_warnings')
        warnings=report['local_readiness'][0]['warnings']
        self.assertIn('CONSTANT_PREDICTOR:A',warnings)
        self.assertIn('INSUFFICIENT_DISTINCT_INPUTS',warnings)

    def test_split_leakage_remains_invalid(self):
        self.rows[-1]['base_case_id']=self.rows[0]['base_case_id']
        with self.assertRaisesRegex(ModelError,'数据泄漏'):inspect_data(self.network,self.rows,self.config)

    def test_ambiguous_wide_ids_still_have_long_and_json_templates(self):
        network=dict(id='reserved',nodes=['A','sample_id'],edges=[dict(feeder='A',receiver='sample_id')])
        outputs=template_outputs(network)
        self.assertNotIn('SE填写模板_宽表.csv',outputs)
        self.assertIn('SE填写模板_长表.csv',outputs)

    def test_generated_wide_and_long_templates_map_identically(self):
        outputs=template_outputs(self.network)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            for name,value in outputs.items():(p/name).write_text(value,encoding='utf-8')
            self.assertEqual(read_samples(p/'SE填写模板_宽表.csv',self.network),read_samples(p/'SE填写模板_长表.csv',self.network,'long'))

    def test_free_delta_parameter_count_is_explicit(self):
        config=deepcopy(self.config);config['models']={'free_delta':settings('free_delta',['T'])}
        report=inspect_data(self.network,self.rows,config)
        self.assertEqual(report['local_readiness'][0]['parameter_count'],5)

    def test_repeated_base_cases_do_not_imply_independent_evidence(self):
        rows=deepcopy(self.rows)
        for r in rows:
            if r['split']=='train': r['base_case_id']='same_train_base'
        report=inspect_data(self.network,rows,self.config)
        self.assertIn('FEW_INDEPENDENT_TRAIN_BASE_CASES',report['local_readiness'][0]['warnings'])

if __name__=='__main__':unittest.main()
