"""Experiment versioning, leaf-only variables and lossless closure checks."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, load_model
from fdna.__main__ import main
from fdna.experiments import inspect_experiments
from fdna.structure import digest

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'data/fdna/arcane_case.json'
STRUCTURE = CASE.with_name('arcane_structure.json')
PLAN = CASE.with_name('experiment_plan_check.json')
BATCH = CASE.with_name('closure_records_check.json')


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        self.structure = json.loads(STRUCTURE.read_text(encoding='utf-8'))
        self.plan = json.loads(PLAN.read_text(encoding='utf-8'))
        self.batch = json.loads(BATCH.read_text(encoding='utf-8'))

    def inspect(self):
        return inspect_experiments(self.model, self.structure, self.plan, self.batch)

    def test_complete_contract_preserves_inputs_without_claiming_metric_extraction(self):
        before = deepcopy((self.model, self.structure, self.plan, self.batch))
        report = self.inspect()
        self.assertEqual(report['status'], 'validated')
        self.assertEqual(report['counts'], dict(variables=7, runs=1, closures=1, events=6,
                                               structurally_complete_closures=1, needs_review_closures=0))
        self.assertEqual(report['policies']['metric_extraction'], 'not_implemented')
        self.assertFalse(report['case_reproduction_ready'])
        self.assertEqual((self.model, self.structure, self.plan, self.batch), before)
        report['batch_snapshot']['runs'].clear()
        self.assertEqual((self.model, self.structure, self.plan, self.batch), before)

    def test_table6_unknown_units_retained_and_not_replaced_by_check_configuration(self):
        self.plan = json.loads(PLAN.with_name('experiment_plan_pending.json').read_text(encoding='utf-8'))
        self.batch['plan_sha256'] = digest(self.plan)
        report = self.inspect()
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(sum(b.startswith('UNKNOWN_VARIABLE_UNIT:') for b in report['runs'][0]['blockers']), 7)
        self.assertEqual({v['category'] for v in self.plan['variables']}, {'method', 'performance', 'environment'})
        self.assertTrue(all(v['unit'] is None for v in report['plan_snapshot']['variables']))

    def test_incomplete_missing_and_grouped_capability_records_retained(self):
        run = self.batch['runs'][0]
        run['assignments'].pop('mirror_start')
        closure = run['closures'][0]
        closure.update(status='incomplete', end=None)
        # S6 is required separately even though S5 shares the activity.
        closure['events'] = [e for e in closure['events'] if e['capability'] != 'S6']
        closure['events'][0].update(start=None, outcome='unknown', facts=[])
        report = self.inspect()
        self.assertEqual(report['counts']['closures'], 1)
        self.assertEqual(report['counts']['events'], 5)
        self.assertEqual(report['counts']['needs_review_closures'], 1)
        blockers = report['closures'][0]['blockers']
        self.assertIn('MISSING_VARIABLE_VALUE:mirror_start', blockers)
        self.assertIn('MISSING_CLOSURE_TIME', blockers)
        self.assertIn('CLOSURE_NOT_CONFIRMED', blockers)
        self.assertIn('MISSING_ACTIVITY_CAPABILITY:Act_KC1_4:S6', blockers)
        self.assertIn('MISSING_EVENT_TIME:event_S1', blockers)
        self.assertEqual(report['batch_snapshot'], self.batch)

    def test_capability_variables_only_allow_leaf_nodes(self):
        variable = self.plan['variables'][0]
        variable.update(target_kind='capability', targets=['S1', 'S8', 'S10'])
        self.batch['plan_sha256'] = digest(self.plan)
        self.assertEqual(self.inspect()['status'], 'validated')
        variable['targets'] = ['S5']
        self.batch['plan_sha256'] = digest(self.plan)
        with self.assertRaises(ModelError): self.inspect()

    def test_invalid_versions_ownership_duplicates_units_and_times_rejected(self):
        original = deepcopy((self.plan, self.batch))
        changes = ('case_hash', 'structure_hash', 'plan_hash', 'variable_id', 'source', 'target',
                   'assignment', 'unit', 'run_id', 'closure_id', 'attempt', 'event_id', 'task',
                   'closure_target', 'activity', 'capability', 'subject', 'time_unit', 'reverse',
                   'outside', 'negative', 'bool', 'infinite', 'fact_name')
        for change in changes:
            self.plan, self.batch = deepcopy(original)
            run = self.batch['runs'][0]
            closure = run['closures'][0]
            event = closure['events'][0]
            if change == 'case_hash': self.plan['case_sha256'] = '0' * 64
            elif change == 'structure_hash': self.plan['structure_sha256'] = '0' * 64
            elif change == 'variable_id': self.plan['variables'].append(deepcopy(self.plan['variables'][0]))
            elif change == 'source': self.plan['variables'][0]['source_refs'] = ['fantasy:absent']
            elif change == 'target': self.plan['variables'][0]['targets'] = ['absent']
            elif change == 'assignment': run['assignments']['absent'] = {'value': 1, 'unit': 'x'}
            elif change == 'unit': run['assignments']['mirror_start']['unit'] = 'different'
            elif change == 'run_id': self.batch['runs'].append(deepcopy(run))
            elif change in ('closure_id', 'attempt'):
                extra = deepcopy(closure)
                if change == 'attempt': extra['id'] = 'another_closure'
                run['closures'].append(extra)
            elif change == 'event_id': closure['events'].append(deepcopy(event))
            elif change == 'task': closure['task'] = 'absent'
            elif change == 'closure_target': closure['target'] = 'T1'
            elif change == 'activity': event['activity'] = 'Act_KC2_1'
            elif change == 'capability': event['capability'] = 'S5'
            elif change == 'subject': event['subject'] = 'U2'
            elif change == 'time_unit': event['time_unit'] = 'other'
            elif change == 'reverse': event.update(start=10, end=5)
            elif change == 'outside': event['end'] = 51
            elif change == 'negative': event['start'] = -1
            elif change == 'bool': event['facts'][0]['value'] = True
            elif change == 'infinite': event['start'] = float('nan')
            elif change == 'fact_name': event['facts'].append(deepcopy(event['facts'][0]))
            self.batch['plan_sha256'] = digest(self.plan)
            if change == 'plan_hash': self.batch['plan_sha256'] = '0' * 64
            with self.subTest(change=change), self.assertRaises(ModelError): self.inspect()

    def test_no_records_or_empty_run_cannot_claim_complete(self):
        run = self.batch['runs'][0]
        run['closures'] = []
        self.assertEqual(self.inspect()['status'], 'needs_review')
        self.assertIn('MISSING_CLOSURES', self.inspect()['runs'][0]['blockers'])
        self.batch['runs'] = []
        self.assertEqual(self.inspect()['status'], 'no_records')

    def test_missing_fact_value_keeps_unknown_and_does_not_fill_zero(self):
        self.batch['runs'][0]['closures'][0]['events'][0]['facts'][0]['value'] = None
        report = self.inspect()
        self.assertEqual(report['status'], 'needs_review')
        self.assertIn('MISSING_EVENT_FACT:event_S1', report['closures'][0]['blockers'])

    def test_cli_exports_snapshots_refuses_overwrite_and_invalid_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'out'
            args = ['experiments', str(CASE), '--structure', str(STRUCTURE), '--plan', str(PLAN),
                    '--batch', str(BATCH), '--output-dir', str(output)]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
            report = json.loads((output / 'fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['batch_snapshot'], self.batch)
            self.assertEqual(report['plan_snapshot'], self.plan)
            self.batch['plan_sha256'] = '0' * 64
            invalid = Path(folder) / 'invalid.json'
            invalid.write_text(json.dumps(self.batch), encoding='utf-8')
            args[args.index('--batch') + 1] = str(invalid)
            args[-1] = str(Path(folder) / 'bad')
            with redirect_stdout(io.StringIO()): self.assertEqual(main(args), 2)
            self.assertFalse((Path(folder) / 'bad').exists())


if __name__ == '__main__':
    unittest.main()
