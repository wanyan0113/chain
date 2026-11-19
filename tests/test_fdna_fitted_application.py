"""Independent piecewise responses, provenance and rejection of unsafe fit reuse."""
from contextlib import redirect_stdout
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, inspect_model, load_model
from fdna.__main__ import main
from fdna.fitted_application import analyze_fitted_application, response_difference_csv
from fdna.structure import digest
import test_fdna_residuals


class FittedApplicationTests(unittest.TestCase):
    def setUp(self):
        fixture = test_fdna_residuals.ResidualTests()
        fixture.setUp()
        self.model, self.fit = deepcopy((fixture.model, fixture.fit))
        self.protocol = dict(id='analytical', basis='Explicit single-input analytical check',
            model='fdna_base_eq1_eq2',
            baseline={n: 100 for n in inspect_model(self.model)['exogenous_nodes']},
            drivers=['S1'], observers=['S5'], values=[0, 40, 100])

    def rebind(self):
        self.fit['samples_snapshot']['case_sha256'] = digest(self.model)
        self.fit['input_sha256'] = digest(dict(case=self.model,
            samples=self.fit['samples_snapshot'], settings=self.fit['settings_snapshot']))

    def analyze(self):
        return analyze_fitted_application(self.model, self.fit, self.protocol)

    def test_known_response_differences_and_sources(self):
        before = deepcopy((self.model, self.fit, self.protocol))
        r = self.analyze()
        self.assertEqual([p['fitted_value'] for p in r['response_differences']], [20, 60, 100])
        self.assertEqual([p['reference_value'] for p in r['response_differences']], [34, 74, 100])
        self.assertEqual([p['difference'] for p in r['response_differences']], [-14, -14, 0])
        self.assertEqual(r['max_absolute_response_difference'], 14)
        self.assertEqual(r['parameter_changes'][0]['parameter_source'], 'audited_fit_report')
        self.assertEqual(r['residual_audit']['series'][0]['metrics']['mae'], 4/3)
        self.assertFalse(r['case_reproduction_ready'])
        self.assertFalse(r['policies']['reported_pseudo_r_squared_reused'])
        self.assertEqual((self.model, self.fit, self.protocol), before)
        rows = list(csv.DictReader(io.StringIO(response_difference_csv(r))))
        self.assertEqual(float(rows[0]['difference']), -14)
        self.assertEqual(r['fit_report_sha256'], digest(self.fit))
        self.assertEqual(r['scan_training_ranges'][0]['training_max'], 80)
        self.assertEqual(r['scan_training_ranges'][0]['outside_training_range'], [100])

    def test_missing_reference_parameters_do_not_prevent_fitted_analysis(self):
        self.model['edges'][0].update(alpha=None, beta=None)
        self.rebind()
        r = self.analyze()
        self.assertIsNone(r['reference_application'])
        self.assertIsNone(r['max_absolute_response_difference'])
        self.assertEqual(r['response_differences'], [])
        self.assertEqual(r['comparison_status'], 'reference_parameters_missing')
        self.assertEqual(r['fitted_application']['comparisons'][0]['value_at_input_0'], 20)

    def test_tampered_fit_or_wrong_case_is_rejected(self):
        original = deepcopy(self.fit)
        for kind in ('parameter', 'prediction', 'hash', 'duplicate'):
            self.fit = deepcopy(original)
            if kind == 'parameter': self.fit['receivers'][0]['parameters'][0]['alpha'] = .9
            if kind == 'prediction': self.fit['receivers'][0]['metrics']['train']['rows'][0]['predicted'] = 99
            if kind == 'hash': self.fit['input_sha256'] = '0'*64
            if kind == 'duplicate': self.fit['receivers'] *= 2
            with self.subTest(kind=kind), self.assertRaises(ModelError): self.analyze()

    def test_nonconverged_fit_and_delta_are_not_silently_applied(self):
        self.fit['receivers'][0]['delta'] = 5
        with self.assertRaisesRegex(ModelError, 'δ'): self.analyze()
        self.fit['receivers'][0]['delta'] = None
        self.fit['status'] = 'blocked'
        self.fit['receivers'][0]['status'] = 'iteration_limit'
        with self.assertRaisesRegex(ModelError, '收敛'): self.analyze()
        self.fit['status'] = 'complete'
        self.fit['receivers'][0].update(status='fitted', delta=0)
        self.fit['settings_snapshot'].update(model='fdna_additive_delta',
            delta_policy='per_receiver_bounded', delta_bounds={'S5': [-10, 10]}, output_policy='no_clipping')
        self.rebind()
        with self.assertRaisesRegex(ModelError, 'δ'): self.analyze()

    def test_two_level_recursion_uses_both_fitted_edges(self):
        edge = deepcopy(next(e for e in load_model(
            Path(__file__).resolve().parents[1]/'data/fdna/arcane_case.json')['edges']
            if e['feeder']=='S5' and e['receiver']=='F7'))
        self.model['edges'].append(edge)
        self.model['case_extension']['delta']['F7'] = None
        self.fit['samples_snapshot']['records'] = []
        self.fit['receivers'] = []
        values = [(0, 20, 30), (40, 60, 70), (100, 100, 100)]
        for i, (s1, s5, f7) in enumerate(values):
            self.fit['samples_snapshot']['records'].append(dict(id=str(i), split='train',
                node_values=dict(S1=s1, S5=s5, F7=f7)))
        for receiver, feeder, alpha, beta, pos in [('S5','S1',.4,20,1), ('F7','S5',.6,10,2)]:
            rows = [dict(id=str(i), observed=v[pos], predicted=v[pos], residual=0)
                    for i,v in enumerate(values)]
            self.fit['receivers'].append(dict(receiver=receiver, feeders=[feeder], status='fitted', delta=None,
                parameters=[dict(feeder=feeder, alpha=alpha, beta=beta)],
                metrics=dict(train=dict(count=3, rmse=0, max_absolute_error=0, rows=rows), holdout=None)))
        self.rebind()
        self.protocol['baseline'] = {n: 100 for n in inspect_model(self.model)['exogenous_nodes']}
        self.protocol['observers'] = ['F7']
        r = self.analyze()
        self.assertEqual([p['fitted_value'] for p in r['response_differences']], [30, 70, 100])
        self.assertEqual(r['fitted_application']['comparisons'][0]['shortest_dependency_path'], ['S1','S5','F7'])

    def test_cli_writes_comparison_and_residual_csv_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in [('case',self.model), ('fit',self.fit), ('protocol',self.protocol)]:
                (root/(name+'.json')).write_text(json.dumps(value), encoding='utf-8')
            args = ['fitted-application', str(root/'case.json'), '--fit-report', str(root/'fit.json'),
                    '--protocol', str(root/'protocol.json'), '--output-dir', str(root/'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
            self.assertTrue((root/'out/residuals.csv').is_file())
            self.assertTrue((root/'out/response_differences.csv').is_file())


if __name__ == '__main__':
    unittest.main()
