"""Observed input never consumes hidden clean states or regenerates supplied data."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fdna.bridge_observed import run, main
from fdna.equipment_bridge import bridge
from fdna.model import ModelError
from fdna.network_workflow import read_samples


class BridgeObservedTests(unittest.TestCase):
    def setUp(self):
        self.fixture=Path(__file__).resolve().parents[1]/'data/fdna'
        self.report=self.fixture/'bridge_intake/source_report.json';self.mapping=self.fixture/'bridge_intake/mapping.json'
        self.original_config=json.loads((self.fixture/'bridge_observed/fit_observed.json').read_text(encoding='utf-8'))
        self.rows=json.loads((self.fixture/'bridge_observed/samples_observed.json').read_text(encoding='utf-8'))
        self.rows=[r for r in self.rows if r['fit_group']=='sigma0']
        self.config=deepcopy(self.original_config);self.config['models'].pop('free_delta');self.config['models']['base']['starts']=2
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.samples=self.root/'samples.json';self.settings=self.root/'fit.json';self.out=self.root/'result'

    def write_inputs(self):
        self.samples.write_text(json.dumps(self.rows),encoding='utf-8');self.settings.write_text(json.dumps(self.config),encoding='utf-8')

    def execute(self):
        self.write_inputs()
        with redirect_stdout(io.StringIO()),patch('fdna.bridge_observed.figures'):
            return run(self.report,self.mapping,self.samples,self.settings,self.out)

    def test_direct_observed_input_fits_without_generator_or_clean_state(self):
        with patch('fdna.generation_network.NetworkGenerator') as generator:
            report,analysis,counts=self.execute();generator.assert_not_called()
        self.assertEqual(counts['status'],'complete');self.assertEqual(counts['local_fits'],6)
        self.assertFalse(counts['generated_samples']);self.assertFalse(counts['clean_predictors_used'])
        self.assertEqual(report['policies']['input_role'],'observed');self.assertTrue(analysis['effects'])
        saved=json.loads((self.out/'输入_SE记录.json').read_text(encoding='utf-8'))
        self.assertTrue(all('predictor_values' not in r for r in saved))
        self.assertFalse((self.out/'共同来源情景响应.csv').exists())

    def test_json_and_wide_csv_express_same_external_observations(self):
        network,_=bridge(json.loads(self.report.read_text(encoding='utf-8')),json.loads(self.mapping.read_text(encoding='utf-8')))
        a=read_samples(self.fixture/'bridge_observed/samples_observed.json',network)
        b=read_samples(self.fixture/'bridge_observed/samples_observed.csv',network)
        self.assertEqual(a,b)

    def test_clean_predictors_are_rejected_before_optimization(self):
        self.rows[0]['predictor_values']=dict(self.rows[0]['node_values'])
        with patch('fdna.bridge_observed.fit_network') as fitter:
            with self.assertRaises(ModelError):self.execute()
            fitter.assert_not_called()

    def test_requires_observed_role_and_explicit_data_kind(self):
        for key,value in [('input_role','clean_predictors_observed_targets'),('data_kind','unknown')]:
            self.config=deepcopy(self.original_config);self.config[key]=value
            with self.subTest(key=key),self.assertRaises(ModelError):self.execute()

    def test_source_change_blocks_supplied_data_before_optimization(self):
        source=json.loads(self.report.read_text(encoding='utf-8'));source['scope']+=' changed'
        self.report=self.root/'changed.json';self.report.write_text(json.dumps(source),encoding='utf-8')
        with patch('fdna.bridge_observed.fit_network') as fitter:
            with self.assertRaises(ModelError):self.execute()
            fitter.assert_not_called()

    def test_unknown_node_or_invalid_se_cannot_reach_optimizer(self):
        for bad in ('unknown','range'):
            changed=deepcopy(self.rows)
            if bad=='unknown':self.rows[0]['node_values']['unknown']=10
            else:self.rows[0]['node_values'][next(iter(self.rows[0]['node_values']))]=101
            with self.subTest(bad=bad),patch('fdna.bridge_observed.fit_network') as fitter:
                with self.assertRaises(ModelError):self.execute()
                fitter.assert_not_called()
            self.rows=changed

    def test_missing_se_retained_without_zero_imputation(self):
        network,_=bridge(json.loads(self.report.read_text(encoding='utf-8')),json.loads(self.mapping.read_text(encoding='utf-8')))
        parent=network['edges'][0]['feeder'];self.rows[0]['node_values'][parent]=None
        report,_,counts=self.execute();self.assertEqual(counts['missing_se_cells'],1)
        saved=json.loads((self.out/'输入_SE记录.json').read_text(encoding='utf-8'))
        self.assertIsNone(saved[0]['node_values'][parent])
        omitted=[r for j in report['jobs'] for f in j['local_fits'] for r in f['fit']['receivers'][0]['omitted']]
        self.assertTrue(any(r['id']==self.rows[0]['sample_id'] for r in omitted))

    def test_refuses_existing_output_before_reading_inputs(self):
        self.out.mkdir();(self.out/'keep').write_text('keep')
        with self.assertRaises(ModelError):run('absent','absent','absent','absent',self.out)
        self.assertEqual((self.out/'keep').read_text(),'keep')

    def test_input_change_during_read_blocks_provenance_mismatch(self):
        self.write_inputs()
        def changed(*args):
            self.samples.write_text('[]',encoding='utf-8');return self.rows
        with patch('fdna.bridge_observed.read_samples',side_effect=changed),patch('fdna.bridge_observed.fit_network') as fitter:
            with self.assertRaises(ModelError):run(self.report,self.mapping,self.samples,self.settings,self.out)
            fitter.assert_not_called()

    def test_cli_returns_invalid_for_clean_role_without_output(self):
        self.config['input_role']='clean_predictors_observed_targets';self.write_inputs()
        args=['--report',str(self.report),'--mapping',str(self.mapping),'--samples',str(self.samples),'--config',str(self.settings),'--output-dir',str(self.out)]
        with redirect_stdout(io.StringIO()):self.assertEqual(main(args),2)
        self.assertFalse(self.out.exists())


if __name__=='__main__':unittest.main()
