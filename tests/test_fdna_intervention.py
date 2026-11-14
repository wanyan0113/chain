"""Explicit intermediate clamps, suspended equations and downstream propagation."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, evaluate, load_model
from fdna.__main__ import main
from fdna.figure_compare import compare_readings
from fdna.sweep import sweep

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT/'data/fdna/arcane_case.json'


class InterventionTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        self.scenario = json.loads(CASE.with_name('intact.json').read_text(encoding='utf-8'))
        self.protocol = json.loads(CASE.with_name('intervention_sweep.json').read_text(encoding='utf-8'))
        self.protocol['values'] = [0, 50, 100]

    def test_s5_clamp_suspends_own_equation_and_only_changes_descendants(self):
        intervention = {'basis': 'Independent analytical intervention', 'node_values': {'S5': 0}}
        before = deepcopy((self.model, self.scenario, intervention))
        report = evaluate(self.model, self.scenario, intervention=intervention)
        nodes = {n['id']: n for n in report['nodes']}
        self.assertEqual(nodes['S5']['kind'], 'intervened')
        self.assertEqual(nodes['S5']['terms'], [])
        self.assertEqual(len(nodes['S5']['intervention']['suspended_incoming_edges']), 3)
        self.assertIsNone(nodes['S5']['cod_min'])
        self.assertEqual(nodes['S5']['value'], 0)
        for node in ('S1','D2','D4','S6','S3','S8','S10'):
            self.assertEqual(nodes[node]['value'], 100)
        self.assertEqual(nodes['F7']['value'], min((67+100)/2, 38, 142))
        self.assertEqual(nodes['F9']['value'], min((100+97+100+100+100)/5, 143, 68, 169, 126, 175))
        self.assertEqual(nodes['F11']['value'], min((100+96+100+100+100)/5, 149, 64, 165, 181, 133))
        self.assertEqual((self.model, self.scenario, intervention), before)
        self.assertFalse(report['case_reproduction_ready'])
        self.assertNotEqual(report['input_sha256'], evaluate(self.model, self.scenario)['input_sha256'])

    def test_unknown_upstream_not_silently_imputed_or_propagated_through_clamp(self):
        self.scenario['node_values']['S1'] = None
        report = evaluate(self.model, self.scenario, intervention={
            'basis': 'Explicit clamps do not impute upstream observations', 'node_values': {'S5': 50, 'S6': 100}})
        nodes = {n['id']: n for n in report['nodes']}
        self.assertIsNone(nodes['S1']['value'])
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(nodes['F7']['value'], min((83.5+100)/2, 88, 142))

    def test_intervention_validation_and_no_implicit_scenario_override(self):
        for invalid in ({}, {'basis':'', 'node_values':{'S5':0}},
                        {'basis':'test','node_values':{}}, {'basis':'test','node_values':{'missing':0}},
                        *({'basis':'test','node_values':{'S5':v}} for v in (True, None, -1, 101, float('nan'), float('inf')))):
            with self.subTest(invalid=invalid), self.assertRaises(ModelError):
                evaluate(self.model, self.scenario, intervention=invalid)
        self.scenario['node_values']['S5'] = 0
        with self.assertRaises(ModelError): evaluate(self.model, self.scenario)
        self.protocol['intervention_policy'] = 'implicit'
        with self.assertRaises(ModelError): sweep(self.model, self.protocol)

    def test_leaf_intervention_matches_existing_sweep_and_s6_has_own_response(self):
        old = json.loads(CASE.with_name('base_sweep.json').read_text(encoding='utf-8'))
        old['values'] = self.protocol['values']
        before = deepcopy(self.protocol)
        report = sweep(self.model, self.protocol)
        old_rows = sweep(self.model, old)['rows']
        self.assertEqual([r for r in report['rows'] if r['driver'] in old['drivers']], old_rows)
        self.assertEqual(self.protocol, before)
        rows = {(r['driver'],r['input'],r['observer']): r for r in report['rows']}
        self.assertEqual(rows['S6',0,'F7']['value'], 42)
        self.assertEqual(rows['S6',0,'F9']['value'], 69)
        self.assertEqual(rows['S6',0,'F11']['value'], 65)
        self.assertTrue(all(r['value']==100 for r in report['rows'] if r['input']==100))
        self.assertTrue(all('intervention_snapshot' in t for t in report['traces']))

    def test_intermediate_raster_comparison_uses_explicit_clamp_and_axis_errors(self):
        readings = {'id':'synthetic_intervention_pixels','data_kind':'uncertain_raster_readings',
                    'source_sha256':self.model['sources'][0]['sha256'], 'method':'Synthetic fixture, not source readings',
                    'axis_mapping':{'kind':'candidate_identity','basis':'Explicit test mapping'},
                    'panels':[{'id':'test','observer':'F7','pdf_page':1,'image_xref':1,
                               'image_sha256':'0'*64,'size':[101,101],'x_axis':[[0,0],[100,100]],
                               'y_axis':[[0,100],[100,0]],'legend':['S5']}],
                    'observations':[{'panel':'test','driver':'S5','pixel':[0,62],'pixel_error':[1,1]}]}
        report = compare_readings(self.model,self.protocol,readings)
        point = report['comparisons'][0]
        self.assertEqual(point['predicted'],38)
        self.assertEqual(point['predicted_interval'],[38,39])
        self.assertEqual(point['status'],'overlap')
        self.assertEqual(report['not_covered'],[])

    def test_cli_records_interventions_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            path=folder/'protocol.json'
            path.write_text(json.dumps(self.protocol),encoding='utf-8')
            args=['sweep',str(CASE),'--protocol',str(path),'--output-dir',str(folder/'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args),0)
                self.assertEqual(main(args),2)
            report=json.loads((folder/'out/fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['evaluations'],24)
            self.assertEqual(report['traces'][12]['intervention_snapshot']['node_values'],{'S5':0})


if __name__ == '__main__':
    unittest.main()
