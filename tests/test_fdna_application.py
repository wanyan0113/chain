"""Analytical influence comparisons, ties, changing rankings and source scope."""
from contextlib import redirect_stdout
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, load_model
from fdna.__main__ import main
from fdna.application import analyze_application, application_csv

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT/'data/fdna/arcane_case.json'


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        self.protocol = json.loads(CASE.with_name('intervention_sweep.json').read_text(encoding='utf-8'))
        self.protocol['values'] = [0,50,80,100]

    def test_independent_endpoint_losses_paths_and_target_attribution(self):
        before = deepcopy((self.model,self.protocol))
        report = analyze_application(self.model,self.protocol)
        rows = {(r['driver'],r['observer']):r for r in report['comparisons']}
        self.assertEqual(rows['S5','F7']['loss_at_input_0'],62)
        self.assertEqual(rows['S6','F7']['loss_at_input_0'],58)
        self.assertEqual(rows['S1','F7']['loss_at_input_0'],28)
        self.assertEqual(rows['S1','F7']['relation'],'indirect')
        self.assertEqual(rows['S1','F7']['shortest_dependency_path'],['S1','S5','F7'])
        self.assertEqual(len(rows['S1','F7']['shortest_path_edges']),2)
        self.assertEqual(rows['S3','F7']['relation'],'no_path')
        self.assertIsNone(rows['S3','F7']['shortest_dependency_path'])
        self.assertEqual(rows['S3','F7']['loss_at_input_0'],0)
        self.assertEqual(rows['S5','F7']['target_links'][0]['target'],'T2')
        self.assertEqual(rows['S8','F9']['target_links'][0]['target'],'T1')
        self.assertEqual(rows['S10','F11']['target_links'][0]['target'],'T1')
        self.assertEqual((self.model,self.protocol),before)
        self.assertFalse(report['case_reproduction_ready'])
        self.assertFalse(report['policies']['chain_closure_inferred_from_se'])

    def test_rankings_change_with_input_and_preserve_all_ties_at_100(self):
        report=analyze_application(self.model,self.protocol)
        groups={(r['observer'],r['input']):r['entries'] for r in report['rankings']}
        self.assertEqual(groups['F7',0][0]['driver'],'S5')
        self.assertEqual(groups['F7',80][0]['driver'],'S6')
        self.assertAlmostEqual(groups['F7',80][0]['loss'],3.7)
        self.assertTrue(all(r['rank_min']==1 and r['rank_max']==8 for r in groups['F7',100]))
        self.assertNotIn('S5',[r['driver'] for r in groups['S5',0]])
        self.assertTrue(all(r['rank_max']<=7 for r in groups['S5',0]))

    def test_parameter_comparison_keeps_alpha_and_beta_order_separate(self):
        report=analyze_application(self.model,self.protocol)
        params={r['receiver']:r for r in report['parameter_comparisons']}
        self.assertEqual(params['S5']['sod_sensitivity_descending'][0]['feeder'],'D4')
        self.assertEqual(params['S5']['cod_offset_ascending'][0]['feeder'],'S1')
        # Table7 alpha ordering must not be rewritten to match prose descriptions.
        self.assertEqual([r['feeder'] for r in params['F9']['sod_sensitivity_descending']],
                         ['S8','S10','S3','S6','S5'])
        self.assertAlmostEqual(params['F9']['sod_sensitivity_descending'][0]['sod_partial_derivative'],.43/5)
        csv_rows=list(csv.DictReader(io.StringIO(application_csv(report))))
        self.assertEqual(len(csv_rows),152)

    def test_equal_endpoint_losses_are_reported_as_ties(self):
        for edge in self.model['edges']:
            if edge['receiver']=='F7': edge['beta']=38
        report=analyze_application(self.model,self.protocol)
        entries=next(r['entries'] for r in report['rankings'] if r['observer']=='F7' and r['input']==0)
        self.assertEqual({r['driver'] for r in entries[:2]},{'S5','S6'})
        self.assertTrue(all(r['rank_min']==1 and r['rank_max']==2 for r in entries[:2]))

    def test_nonreference_baseline_or_partial_input_domain_rejected(self):
        for change in ('baseline','domain'):
            protocol=deepcopy(self.protocol)
            if change=='baseline': protocol['baseline']['S1']=99
            else: protocol['values']=[50,100]
            with self.subTest(change=change),self.assertRaises(ModelError):
                analyze_application(self.model,protocol)

    def test_cli_exports_traceable_rankings_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            path=folder/'protocol.json'
            path.write_text(json.dumps(self.protocol),encoding='utf-8')
            args=['application',str(CASE),'--protocol',str(path),'--output-dir',str(folder/'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args),0)
                self.assertEqual(main(args),2)
            report=json.loads((folder/'out/fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['sweep_snapshot']['protocol'],self.protocol)
            self.assertTrue((folder/'out/application_ranking.csv').is_file())
            self.assertFalse((folder/'out/fdna_summary.md').exists())


if __name__=='__main__': unittest.main()
