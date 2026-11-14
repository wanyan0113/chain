"""Evidence is required separately from interaction and reference edges."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, load_model
from fdna.__main__ import main
from fdna.identification import identify_dependencies, MAIN_TYPES
from fdna.structure import digest

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'data/fdna/arcane_case.json'
STRUCTURE = CASE.with_name('arcane_structure.json')
RECORDS = CASE.with_name('dependency_annotations.json')


class IdentificationTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        self.structure = json.loads(STRUCTURE.read_text(encoding='utf-8'))
        self.records = json.loads(RECORDS.read_text(encoding='utf-8'))

    def run_review(self):
        return identify_dependencies(self.model, self.structure, self.records)

    def test_source_annotations_do_not_turn_all_exchanges_or_reference_edges_into_dependencies(self):
        before = deepcopy((self.model, self.structure, self.records))
        report = self.run_review()
        self.assertEqual(report['status'], 'reviewed')
        self.assertEqual(report['counts']['included'], 1)
        self.assertEqual(report['counts']['excluded'], 2)
        self.assertEqual(report['comparison']['matched'], [{'feeder': 'D4', 'receiver': 'S5'}])
        self.assertEqual(len(report['comparison']['reported_without_included_evidence']), 7)
        self.assertFalse(report['case_reproduction_ready'])
        self.assertEqual((self.model, self.structure, self.records), before)
        report['records_snapshot']['id'] = 'changed'
        self.assertEqual((self.model, self.structure, self.records), before)

    def test_missing_and_conflicting_effects_are_not_confirmed(self):
        row = self.records['interactions'][-1]
        row.update(effect='unknown', effect_evidence=[], review='pending')
        report = self.run_review()
        self.assertEqual(report['counts']['pending'], 1)
        self.assertEqual(report['status'], 'needs_review')
        other = deepcopy(row)
        other.update(id='opposing', effect='not_obvious', effect_evidence=['initial_effect'], review='exclude')
        self.records['interactions'].append(other)
        row.update(effect='supported', effect_evidence=['coordination_effect'], review='include')
        self.assertEqual(self.run_review()['counts']['conflicting'], 1)

    def test_invalid_evidence_mapping_versions_and_reviews_rejected(self):
        original = deepcopy(self.records)
        for change in ('case', 'structure', 'duplicate_record', 'duplicate_evidence', 'source', 'activity',
                       'capability', 'self', 'interaction_ref', 'effect_ref', 'missing_effect',
                       'unsupported_include', 'unsupported_exclude', 'focus', 'unknown_field'):
            self.records = deepcopy(original)
            row = self.records['interactions'][-1]
            if change == 'case': self.records['case_sha256'] = '0' * 64
            elif change == 'structure': self.records['structure_sha256'] = '0' * 64
            elif change == 'duplicate_record': self.records['interactions'].append(deepcopy(row))
            elif change == 'duplicate_evidence': self.records['evidence'].append(deepcopy(self.records['evidence'][0]))
            elif change == 'source': self.records['evidence'][0]['source_refs'] = ['fantasy:absent']
            elif change == 'activity': row['feeder_activity'] = 'absent'
            elif change == 'capability': row['feeder'] = 'S1'
            elif change == 'self': row['receiver'] = row['feeder']
            elif change == 'interaction_ref': row['interaction_evidence'] = ['coordination_effect']
            elif change == 'effect_ref': row['effect_evidence'] = ['absent']
            elif change == 'missing_effect': row['effect_evidence'] = []
            elif change == 'unsupported_include': row['effect'] = 'unknown'
            elif change == 'unsupported_exclude': row['review'] = 'exclude'
            elif change == 'focus': self.records['focus_receivers'] = ['absent']
            elif change == 'unknown_field': row['alpha'] = .5
            with self.subTest(change=change), self.assertRaises(ModelError):
                self.run_review()

    def test_all_six_main_types_and_cross_chain_mapping(self):
        endpoints = {'R': ('Act_KC1_1', 'S1'), 'D': ('Act_KC1_2', 'D2'), 'E': ('Act_KC1_5', 'F7')}
        self.records['data_kind'] = 'method_check'
        self.records['focus_receivers'] = [n['id'] for n in self.model['nodes']]
        for first, second in MAIN_TYPES:
            a, b = endpoints[first], endpoints[second]
            if first == second:
                b = ('Act_KC2_1', 'S8') if second == 'R' else ('Act_KC1_3', 'D4')
            row = deepcopy(self.records['interactions'][-1])
            row.update(feeder_activity=a[0], feeder=a[1], receiver_activity=b[0], receiver=b[1])
            self.records['interactions'] = [row]
            with self.subTest(types=(first, second)):
                report = self.run_review()
                self.assertEqual(report['counts']['included'], 1)
                self.assertEqual(report['candidates'][0]['records'][0]['activity_types'], [first, second])
        row.update(feeder_activity='Act_KC1_5', feeder='F7', receiver_activity='Act_KC2_1', receiver='S8')
        with self.assertRaises(ModelError): self.run_review()
        row.update(effect='unknown', effect_evidence=[], review='pending')
        self.assertEqual(self.run_review()['counts']['pending'], 1)

    def test_out_of_scope_empty_and_reference_only_comparison(self):
        self.records['focus_receivers'] = ['F7']
        report = self.run_review()
        self.assertEqual(report['counts']['out_of_scope'], 3)
        self.assertEqual(len(report['comparison']['reported_without_included_evidence']), 2)
        self.records['interactions'] = []
        self.assertEqual(self.run_review()['status'], 'no_records')

    def test_cli_exports_snapshots_refuses_overwrite_and_invalid_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'out'
            args = ['identify', str(CASE), '--structure', str(STRUCTURE), '--records', str(RECORDS),
                    '--output-dir', str(output)]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
            report = json.loads((output / 'fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['records_snapshot'], self.records)
            self.assertEqual(report['structure_snapshot'], self.structure)
            self.assertEqual(report['input_sha256'], digest({'case': self.model, 'structure': self.structure,
                                                           'records': self.records}))
            invalid = Path(folder) / 'invalid.json'
            self.records['case_sha256'] = '0' * 64
            invalid.write_text(json.dumps(self.records), encoding='utf-8')
            args[args.index('--records') + 1] = str(invalid)
            args[-1] = str(Path(folder) / 'bad')
            with redirect_stdout(io.StringIO()): self.assertEqual(main(args), 2)
            self.assertFalse((Path(folder) / 'bad').exists())


if __name__ == '__main__':
    unittest.main()
