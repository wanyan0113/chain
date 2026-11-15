"""Independent envelope arithmetic, shared-parameter evidence and CLI contracts."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, load_model
from fdna.__main__ import main
from fdna.engine import evaluate
from fdna.precision import check_precision


CASE = Path(__file__).resolve().parents[1]/'data/fdna/arcane_case.json'


class PrecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_model(CASE)
        cls.protocol = json.loads(CASE.with_name('intervention_sweep.json').read_text(encoding='utf-8'))
        cls.protocol['values'] = [0, 53, 80, 100]
        before = deepcopy((cls.model, cls.protocol))
        cls.report = check_precision(cls.model, cls.protocol)
        assert (cls.model, cls.protocol) == before

    def row(self, driver, observer, value):
        return next(r for r in self.report['rows'] if
                    (r['driver'], r['observer'], r['input']) == (driver, observer, value))

    def test_direct_edge_extrema_and_100_baseline(self):
        row = self.row('S5', 'F7', 0)
        self.assertAlmostEqual(row['value_lower'], 37.5)
        self.assertAlmostEqual(row['value_upper'], 38.5)
        row = self.row('S5', 'F7', 80)
        self.assertAlmostEqual(row['value_lower'], 100-.335*20/2)
        self.assertAlmostEqual(row['value_upper'], 100-.325*20/2)
        self.assertTrue(all(r['value_lower'] == r['value_upper'] == 100
                            for r in self.report['rows'] if r['input'] == 100))

    def test_propagated_extrema_by_independent_scalar_equations(self):
        def output(lower):
            edges = {(e['feeder'], e['receiver']): e for e in self.model['edges']}
            def receiver(node, feeders):
                sod, cod = [], []
                for feeder, value in feeders.items():
                    edge = edges[feeder, node]
                    alpha = edge['alpha'] + (.005 if lower else -.005)
                    beta = edge['beta'] + (-.5 if lower else .5)
                    sod.append(100-alpha*(100-value))
                    cod.append(value+beta)
                return min(sum(sod)/len(sod), min(cod))
            inputs = {'S1': 0, 'D2': 100, 'D4': 100}
            return receiver('F7', {n: receiver(n, inputs) for n in ('S5', 'S6')})
        row = self.row('S1', 'F7', 0)
        self.assertAlmostEqual(row['value_lower'], output(True))
        self.assertAlmostEqual(row['value_upper'], output(False))

    def test_boundary_intersection_and_input_immutability(self):
        model, protocol = deepcopy(self.model), deepcopy(self.protocol)
        model['edges'][0].update(alpha=0, beta=100)
        model['edges'][1].update(alpha=1, beta=0)
        protocol.update(drivers=['S1'], observers=['S5'], values=[0, 100])
        before = deepcopy((model, protocol))
        report = check_precision(model, protocol)
        self.assertEqual((model, protocol), before)
        self.assertEqual(report['parameter_bounds'][0]['alpha']['lower'], 0)
        self.assertEqual(report['parameter_bounds'][0]['beta']['upper'], 100)
        self.assertEqual(report['parameter_bounds'][1]['alpha']['upper'], 1)
        self.assertEqual(report['parameter_bounds'][1]['beta']['lower'], 0)

    def test_reversal_witnesses_replay_with_same_parameter_setting(self):
        witnesses = [w for w in self.report['witnesses'] if w['kind'] == 'strict_reversal']
        self.assertTrue(witnesses)
        self.assertTrue(any(w['observer'] == 'F7' and w['input'] == 53 and
                            {w['a'], w['b']} == {'S5', 'S6'} for w in witnesses))
        settings = {s['id']: s for s in self.report['tested_settings']}
        for witness in witnesses:
            candidate = deepcopy(self.model)
            for edge, saved in zip(candidate['edges'], settings[witness['setting_id']]['parameters']):
                edge.update(alpha=saved['alpha'], beta=saved['beta'])
            losses = []
            for driver in (witness['a'], witness['b']):
                result = evaluate(candidate, {'id': 'replay', 'basis': 'test',
                                             'node_values': self.protocol['baseline']},
                                  intervention={'basis': 'test', 'node_values': {driver: witness['input']}})
                losses.append(100-next(n['value'] for n in result['nodes'] if n['id'] == witness['observer']))
            self.assertAlmostEqual(losses[0], witness['loss_a'])
            self.assertAlmostEqual(losses[1], witness['loss_b'])
            self.assertLess((losses[0]-losses[1])*witness['nominal_loss_gap_a_minus_b'], 0)

    def test_overlap_is_not_certified_as_reversal_and_exact_ties_remain(self):
        pairs = [p for g in self.report['rankings'] for p in g['pairs']]
        self.assertTrue(any(p['status'] == 'not_certified' and 'reversal_witness' not in p for p in pairs))
        for group in self.report['rankings']:
            if group['input'] == 100:
                self.assertTrue(all(p['status'] == 'certified_tie_within_tolerance' for p in group['pairs']))
                self.assertTrue(all(e['conservative_rank_min'] == 1 and
                                    e['conservative_rank_max'] == len(group['entries']) for e in group['entries']))
                self.assertFalse(group['unique_top_certified'])
            self.assertNotIn(group['observer'], [e['driver'] for e in group['entries']])
        self.assertFalse(self.report['case_reproduction_ready'])

    def test_incomplete_domain_and_non100_baseline_rejected(self):
        for change in ('values', 'baseline'):
            protocol = deepcopy(self.protocol)
            if change == 'values':
                protocol['values'] = [0, 80]
            else:
                protocol['baseline']['S1'] = 99
            with self.assertRaises(ModelError):
                check_precision(self.model, protocol)

    def test_cli_exports_evidence_and_refuses_overwrite(self):
        protocol = deepcopy(self.protocol)
        protocol.update(drivers=['S5', 'S6'], observers=['F7'], values=[0, 53, 100])
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = folder/'protocol.json'
            path.write_text(json.dumps(protocol), encoding='utf-8')
            args = ['precision', str(CASE), '--protocol', str(path), '--output-dir', str(folder/'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
            saved = json.loads((folder/'out/fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['protocol_snapshot'], protocol)
            self.assertTrue(saved['source_verification'])
            self.assertTrue(saved['witnesses'])
            self.assertEqual(saved['input_sha256'], check_precision(self.model, protocol)['input_sha256'])


if __name__ == '__main__':
    unittest.main()
