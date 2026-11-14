"""Traceable metric extraction, explicit grouping and normalization handoff."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, load_model
from fdna.__main__ import main
from fdna.extraction import extract_measurements
from fdna.structure import digest

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'data/fdna/arcane_case.json'
STRUCTURE = CASE.with_name('arcane_structure.json')
PLAN = CASE.with_name('experiment_plan_check.json')
BATCH = CASE.with_name('indicator_records_check.json')
CONFIG = CASE.with_name('extraction_check.json')
CALIBRATION = CASE.with_name('se_calibration_check.json')


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        self.structure, self.plan, self.batch, self.config, self.calibration = [
            json.loads(p.read_text(encoding='utf-8')) for p in (STRUCTURE, PLAN, BATCH, CONFIG, CALIBRATION)]

    def extract(self, normalize=False):
        return extract_measurements(self.model, self.structure, self.plan, self.batch, self.config,
                                    self.calibration if normalize else None)

    def event(self, node):
        return next(e for c in self.batch['runs'][0]['closures'] for e in c['events'] if e['capability'] == node)

    def rebind(self):
        self.config['batch_sha256'] = digest(self.batch)

    def test_all_table5_indicators_match_independent_expected_measurements(self):
        before = deepcopy((self.model, self.structure, self.plan, self.batch, self.config))
        report = self.extract(True)
        expected = json.loads(CASE.with_name('se_measurements_check.json').read_text(encoding='utf-8'))
        self.assertEqual(report['measurements']['records'][0]['observations'], expected['records'][0]['observations'])
        self.assertEqual(report['counts'], dict(records=1, known_indicators=11, unknown_indicators=0))
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['normalization']['counts']['known_se'], 11)
        values = {n['id']: n['se'] for n in report['normalization']['records'][0]['nodes']}
        self.assertEqual(values['S1'], 75)
        self.assertEqual(values['D2'], 75)
        self.assertFalse(report['case_reproduction_ready'])
        self.assertEqual((self.model, self.structure, self.plan, self.batch, self.config), before)
        report['experiment_check']['batch_snapshot']['runs'].clear()
        self.assertEqual((self.model, self.structure, self.plan, self.batch, self.config), before)

    def test_tracking_union_does_not_double_count_overlaps_and_counts_are_summed(self):
        closure = self.batch['runs'][0]['closures'][0]
        event = self.event('S5')
        event['facts'][1]['value'] = 30
        extra = deepcopy(event)
        extra['id'] = 'tracking_extra'
        extra['facts'][0]['value'] = 20
        extra['facts'][1]['value'] = 50
        closure['events'].append(extra)
        other = self.batch['runs'][0]['closures'][1]
        count = deepcopy(self.event('S3'))
        count['id'] = 'count_extra'
        count['facts'][0]['value'] = 2
        other['events'].append(count)
        self.rebind()
        obs = self.extract()['measurements']['records'][0]['observations']
        self.assertEqual(obs['S5']['value'], 50)
        self.assertEqual(obs['S3']['value'], 5)

    def test_discovery_minimum_and_fusion_kept_as_two_measured_times(self):
        extra = deepcopy(self.event('S1'))
        extra['id'] = 'earlier_discovery'
        extra['facts'][0]['value'] = 10
        self.batch['runs'][0]['closures'][0]['events'].append(extra)
        self.rebind()
        report = self.extract()
        obs = report['measurements']['records'][0]['observations']
        self.assertEqual(obs['S1']['value'], 10)
        self.assertEqual(obs['D4'], dict(start=10, end=20, unit='time_scale'))
        trace = next(t for t in report['traces'] if t['node'] == 'D4')
        self.assertEqual(trace['evidence'][0]['source_locator'], 'artificial:D4')
        self.assertEqual([f['field'] for f in trace['evidence'][0]['selected_facts']], ['fusion_start', 'fusion_end'])

    def test_missing_rules_facts_and_incomplete_closures_propagate_unknown(self):
        self.config['rules']['S1'] = None
        self.event('S5')['facts'].pop()
        self.rebind()
        report = self.extract()
        obs = report['measurements']['records'][0]['observations']
        self.assertIsNone(obs['S1'])
        self.assertIsNone(obs['S5'])
        self.assertEqual(report['status'], 'blocked')
        self.batch['runs'][0]['closures'][1]['status'] = 'incomplete'
        self.rebind()
        self.assertIsNone(self.extract()['measurements']['records'][0]['observations']['F9'])

    def test_different_closure_contexts_and_multiple_fusion_values_are_ambiguous(self):
        run = self.batch['runs'][0]
        other = deepcopy(run['closures'][0])
        other.update(id='another_attempt', attempt='2')
        for event in other['events']: event['id'] += '_attempt2'
        run['closures'].append(other)
        self.config['groups'][0]['closures'].append(other['id'])
        self.rebind()
        report = self.extract()
        trace = next(t for t in report['traces'] if t['node'] == 'S1')
        self.assertIn('AMBIGUOUS_CAPABILITY_CONTEXT', trace['blockers'])
        run['closures'].pop()
        self.config['groups'][0]['closures'].pop()
        extra = deepcopy(self.event('D2'))
        extra['id'] = 'second_fusion'
        run['closures'][0]['events'].append(extra)
        self.rebind()
        trace = next(t for t in self.extract()['traces'] if t['node'] == 'D2')
        self.assertIn('AMBIGUOUS_MULTIPLE_VALUES', trace['blockers'])

    def test_explicit_selection_no_automatic_pooling_or_zero_filling(self):
        self.config['groups'][0]['closures'] = ['closure_check']
        report = self.extract()
        self.assertEqual(report['counts']['known_indicators'], 6)
        self.assertIsNone(report['measurements']['records'][0]['observations']['F9'])
        self.assertEqual(report['unselected_closures'], ['closure_check_KC2'])
        self.config['groups'] = []
        report = self.extract(True)
        self.assertEqual(report['status'], 'no_records')
        self.assertIsNone(report['normalization'])

    def test_invalid_mapping_grouping_units_counts_and_times_rejected(self):
        before = deepcopy((self.config, self.batch))
        for change in ('stale', 'rule_missing', 'indicator', 'fields', 'duplicate_group', 'run', 'closure',
                       'unit', 'fraction_count', 'negative', 'bool', 'text', 'reverse', 'outside', 'nonfinite'):
            self.config, self.batch = deepcopy(before)
            if change == 'rule_missing': self.config['rules'].pop('S1')
            elif change == 'indicator': self.config['rules']['S1']['indicator'] = 'other'
            elif change == 'fields': self.config['rules']['D2']['fields'] = ['fusion_start']
            elif change == 'duplicate_group': self.config['groups'].append(deepcopy(self.config['groups'][0]))
            elif change == 'run': self.config['groups'][0]['run'] = 'missing'
            elif change == 'closure': self.config['groups'][0]['closures'] = ['missing']
            elif change == 'unit': self.event('S1')['facts'][0]['unit'] = 'other'
            elif change == 'fraction_count': self.event('S3')['facts'][0]['value'] = .5
            elif change == 'negative': self.event('S1')['facts'][0]['value'] = -1
            elif change == 'bool': self.event('S1')['facts'][0]['value'] = True
            elif change == 'text': self.event('S1')['facts'][0]['value'] = '25'
            elif change == 'reverse': self.event('D2')['facts'][1]['value'] = 5
            elif change == 'outside': self.event('S1')['facts'][0]['value'] = 101
            elif change == 'nonfinite': self.event('S1')['facts'][0]['value'] = float('inf')
            if change != 'nonfinite': self.rebind()
            if change == 'stale': self.config['batch_sha256'] = '0' * 64
            with self.subTest(change=change), self.assertRaises(ModelError): self.extract()

    def test_pending_normalization_and_plan_do_not_borrow_artificial_bounds_or_units(self):
        self.calibration = json.loads(CASE.with_name('se_calibration_pending.json').read_text(encoding='utf-8'))
        report = self.extract(True)
        self.assertEqual(report['counts']['known_indicators'], 11)
        self.assertEqual(report['normalization']['counts']['unknown_se'], 11)
        self.assertEqual(report['status'], 'blocked')
        self.plan = json.loads(CASE.with_name('experiment_plan_pending.json').read_text(encoding='utf-8'))
        self.batch['plan_sha256'] = digest(self.plan)
        self.rebind()
        self.assertEqual(self.extract()['counts']['unknown_indicators'], 11)

    def test_cli_handoff_export_and_overwrite_refusal(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'out'
            args = ['extract', str(CASE), '--structure', str(STRUCTURE), '--plan', str(PLAN), '--batch', str(BATCH),
                    '--extraction', str(CONFIG), '--calibration', str(CALIBRATION), '--output-dir', str(output)]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
            measurements = json.loads((output / 'measurements.json').read_text(encoding='utf-8'))
            report = json.loads((output / 'fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(measurements, report['measurements'])
            self.assertEqual(report['normalization']['counts']['known_se'], 11)


if __name__ == '__main__':
    unittest.main()
