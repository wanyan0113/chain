"""Analytical delta scans: preserve offsets, clamps and unknown reference values."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, inspect_model
from fdna.additive import ADDITIVE_MODEL
from fdna.application import analyze_application
from fdna.fitted_application import analyze_fitted_application
from fdna.__main__ import main
import test_fdna_fitted_application


class AdditiveApplicationTests(unittest.TestCase):
    def setUp(self):
        fixture = test_fdna_fitted_application.FittedApplicationTests()
        fixture.setUp()
        self.model, self.fit, self.protocol = deepcopy((fixture.model, fixture.fit, fixture.protocol))
        self.protocol['model'] = ADDITIVE_MODEL
        self.fit['settings_snapshot'].update(model=ADDITIVE_MODEL,
            delta_policy='per_receiver_bounded', delta_bounds={'S5': [-10, 10]},
            output_policy='no_clipping')
        self.fit['receivers'][0]['delta'] = -3
        for sample in self.fit['samples_snapshot']['records']:
            sample['node_values']['S5'] -= 3
        for metric in self.fit['receivers'][0]['metrics'].values():
            for row in metric['rows']:
                row['observed'] -= 3
                row['predicted'] -= 3
        self.rebind()
        self.extension = dict(model=ADDITIVE_MODEL, basis='Analytical test only',
                              delta_values={'S5': -5}, output_policy='no_clipping')

    def rebind(self):
        fixture = test_fdna_fitted_application.FittedApplicationTests()
        fixture.model, fixture.fit = self.model, self.fit
        fixture.rebind()

    def test_nonzero_fitted_delta_without_reference_is_not_replaced_by_zero(self):
        before = deepcopy((self.model, self.fit, self.protocol))
        r = analyze_fitted_application(self.model, self.fit, self.protocol)
        scan = r['fitted_application']['sweep_snapshot']
        self.assertEqual([p['value'] for p in scan['rows']], [17, 57, 97])
        self.assertEqual([p['loss'] for p in scan['rows']], [80, 40, 0])
        self.assertEqual(scan['model'], ADDITIVE_MODEL)
        self.assertEqual(r['fitted_extension']['delta_values'], {'S5': -3})
        self.assertEqual(r['comparison_status'], 'reference_delta_not_supplied')
        self.assertIsNone(r['reference_application'])
        self.assertEqual(r['response_differences'], [])
        self.assertEqual((self.model, self.fit, self.protocol), before)

    def test_explicit_reference_delta_comparison_and_fingerprints(self):
        r = analyze_fitted_application(self.model, self.fit, self.protocol,
                                       reference_extension=self.extension)
        self.assertEqual([p['reference_value'] for p in r['response_differences']], [29, 69, 95])
        self.assertEqual([p['difference'] for p in r['response_differences']], [-12, -12, 2])
        changed = deepcopy(self.extension)
        changed['delta_values']['S5'] = -4
        other = analyze_fitted_application(self.model, self.fit, self.protocol,
                                           reference_extension=changed)
        self.assertNotEqual(r['input_sha256'], other['input_sha256'])
        self.assertNotEqual(r['reference_application']['input_sha256'],
                            other['reference_application']['input_sha256'])
        self.assertNotEqual(r['reference_application']['sweep_snapshot']['input_sha256'],
                            other['reference_application']['sweep_snapshot']['input_sha256'])

    def test_rejects_wrong_model_unknown_reference_and_changed_delta(self):
        wrong = deepcopy(self.protocol)
        wrong['model'] = 'fdna_base_eq1_eq2'
        with self.assertRaises(ModelError):
            analyze_fitted_application(self.model, self.fit, wrong)
        self.extension['delta_values']['S5'] = None
        with self.assertRaises(ModelError):
            analyze_fitted_application(self.model, self.fit, self.protocol,
                                       reference_extension=self.extension)
        self.fit['receivers'][0]['delta'] = -2
        with self.assertRaises(ModelError):
            analyze_fitted_application(self.model, self.fit, self.protocol)

    def test_two_layers_and_intermediate_clamp_suspend_delta(self):
        # O5=min(.4*O1+60,O1+20)-3; O7=min(.5*O5+50,O5+10)-2.
        self.model['edges'][0].update(alpha=.4, beta=20)
        edge = deepcopy(self.model['edges'][0])
        edge.update(feeder='S5', receiver='F7', alpha=.5, beta=10)
        self.model['edges'].append(edge)
        self.model['case_extension']['delta']['F7'] = None
        ext = deepcopy(self.extension)
        ext['delta_values'] = {'S5': -3, 'F7': -2}
        self.protocol.update(baseline={n: 100 for n in inspect_model(self.model)['exogenous_nodes']},
                             observers=['S5', 'F7'])
        r = analyze_application(self.model, self.protocol, extension=ext)
        rows = r['sweep_snapshot']['rows']
        self.assertEqual([p['value'] for p in rows if p['observer']=='F7'], [25, 65, 96.5])
        self.protocol.update(drivers=['S5'], intervention_policy='clamp_driver_recompute_descendants')
        r = analyze_application(self.model, self.protocol, extension=ext)
        rows = r['sweep_snapshot']['rows']
        self.assertEqual([p['value'] for p in rows if p['observer']=='S5'], [0, 40, 100])
        self.assertEqual([p['value'] for p in rows if p['observer']=='F7'], [8, 48, 98])
        clamped = next(n for n in r['sweep_snapshot']['traces'][0]['nodes'] if n['id']=='S5')
        self.assertIsNone(clamped['delta'])
        self.assertEqual(clamped['kind'], 'intervened')

    def test_zero_delta_scan_matches_base_for_root_and_intermediate_inputs(self):
        self.extension['delta_values']['S5'] = 0
        self.protocol.update(drivers=['S1', 'S5'],
                             intervention_policy='clamp_driver_recompute_descendants')
        extended = analyze_application(self.model, self.protocol, extension=self.extension)
        base_protocol = deepcopy(self.protocol)
        base_protocol['model'] = 'fdna_base_eq1_eq2'
        base = analyze_application(self.model, base_protocol)
        self.assertEqual(extended['sweep_snapshot']['rows'], base['sweep_snapshot']['rows'])
        self.assertNotEqual(extended['input_sha256'], base['input_sha256'])

    def test_no_clipping_and_outside_range_is_reported(self):
        self.extension['delta_values']['S5'] = 5
        r = analyze_application(self.model, self.protocol, extension=self.extension)
        self.assertEqual(r['sweep_snapshot']['rows'][-1]['value'], 105)
        self.assertEqual(r['sweep_snapshot']['outside_se_range'][-1]['value'], 105)

    def test_cli_exports_fitted_values_even_without_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in [('case',self.model), ('fit',self.fit),
                                ('protocol',self.protocol), ('extension',self.extension)]:
                (root/(name+'.json')).write_text(json.dumps(value), encoding='utf-8')
            args = ['fitted-application', str(root/'case.json'), '--fit-report', str(root/'fit.json'),
                    '--protocol', str(root/'protocol.json'), '--output-dir', str(root/'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
                self.assertEqual(main(args[:-1]+[str(root/'compared'), '--reference-extension',
                                                str(root/'extension.json')]), 0)
            self.assertIn('17', (root/'out/fitted_responses.csv').read_text())
            self.assertFalse((root/'out/reference_responses.csv').exists())
            self.assertTrue((root/'compared/reference_responses.csv').is_file())


if __name__ == '__main__':
    unittest.main()
