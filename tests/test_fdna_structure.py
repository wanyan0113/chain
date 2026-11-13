"""Chapter 3 ownership, bijections, grouped activity interpretation and edge layers."""
from contextlib import redirect_stdout
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, evaluate, load_model
from fdna.__main__ import main
from fdna.structure import inspect_structure, structure_csv, validate_structure

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'data/fdna/arcane_case.json'
STRUCTURE = CASE.parent / 'arcane_structure.json'


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model(CASE)
        self.structure = json.loads(STRUCTURE.read_text(encoding='utf-8'))

    def test_six_layers_and_explicit_task_chain_correspondence(self):
        report = inspect_structure(self.model, self.structure)
        self.assertEqual(report['counts'], dict(systems=1, chains=2, tasks=2, activity_slots=7,
                                               capabilities=11, subjects=8, functions=11,
                                               capability_dependencies=18, function_dependency_correspondences=18))
        self.assertEqual(len(report['graph']['nodes']), 40)
        self.assertEqual({n['layer'] for n in report['graph']['nodes']}, {'NKW', 'NKC', 'NAct', 'NC', 'NU', 'NF'})
        self.assertEqual(len(report['rows']), 11)
        self.assertEqual(len(report['activity_order_relations']), 5)
        self.assertEqual(report['type_catalog']['NAct'], {'R': '探知', 'D': '决策', 'E': '施法'})
        self.assertTrue(report['structural_contract_ready'])
        self.assertFalse(report['source_confirmed_activity_partition'])
        self.assertFalse(report['dependency_identification_ready'])

    def test_rule6_multiple_functions_per_subject_and_one_per_capability(self):
        functions = self.structure['functions']
        self.assertEqual({f['capability'] for f in functions if f['subject'] == 'U3'}, {'S3', 'D4'})
        self.assertEqual({f['capability'] for f in functions if f['subject'] == 'U7'}, {'S8', 'F9'})
        self.assertEqual(len({f['capability'] for f in functions}), 11)
        self.assertTrue(all(f['id'] != f['capability'] for f in functions))
        activities = {a['id']: a for a in self.structure['activities']}
        self.assertEqual(activities['Act_KC1_4']['capability_group'], ['S5', 'S6'])
        self.assertEqual(set(activities['Act_KC1_4']['performers']), {'U4', 'U5'})
        self.assertEqual(activities['Act_KC2_1']['capability_group'], [['S8', 'S10'], 'S3'])
        self.assertEqual(activities['Act_KC2_2']['capability_group'], ['F9', 'F11'])
        self.assertTrue(all(a['evidence']['kind'] == 'interpretation' for a in activities.values()))

    def test_dependency_correspondence_keeps_direction_without_transferring_parameters(self):
        report = inspect_structure(self.model, self.structure)
        links = report['graph']['links']
        capability_edges = [e for e in links if e['kind'] == 'capability_dependency']
        function_edges = [e for e in links if e['kind'] == 'function_dependency_correspondence']
        pairs = {(e['feeder'], e['receiver']) for e in self.model['edges']}
        self.assertEqual({(e['source'], e['target']) for e in capability_edges}, pairs)
        self.assertNotIn(('S1', 'D2'), pairs)  # Activity order does not produce this numerical edge.
        self.assertEqual({(e['source'], e['target']) for e in function_edges},
                         {('FN_'+a, 'FN_'+b) for a, b in pairs})
        self.assertTrue(all(e['parameters'] is None and e['interaction_mechanism'] is None for e in function_edges))
        self.assertTrue(all(not e['numeric_dependency'] for e in report['activity_order_relations']))

    def test_broken_ownership_bijections_grouping_types_and_direction_rejected(self):
        changes = ['function_owner', 'function_type', 'duplicate_capability', 'missing_function', 'duplicate_id',
                   'task_target', 'task_chain', 'task_order', 'activity_task', 'activity_type', 'performers',
                   'activity_functions', 'flatten_nested_group', 'orphan_activity', 'missing_dependency',
                   'reverse_dependency', 'duplicate_dependency', 'transfer_parameters', 'assert_parallel', 'claim_confirmed']
        for change in changes:
            data = deepcopy(self.structure)
            if change == 'function_owner': data['functions'][0]['subject'] = 'U2'
            elif change == 'function_type': data['functions'][0]['type'] = 'F'
            elif change == 'duplicate_capability': data['functions'][1]['capability'] = 'S1'
            elif change == 'missing_function': data['functions'].pop()
            elif change == 'duplicate_id': data['functions'][0]['id'] = 'S1'
            elif change == 'task_target': data['tasks'][0]['target'] = 'T1'
            elif change == 'task_chain': data['tasks'][1]['chain'] = 'KC1'
            elif change == 'task_order': data['tasks'][0]['activity_order'].reverse()
            elif change == 'activity_task': data['activities'][0]['task'] = data['tasks'][1]['id']
            elif change == 'activity_type': data['activities'][0]['type'] = 'D'
            elif change == 'performers': data['activities'][0]['performers'] = ['U2']
            elif change == 'activity_functions': data['activities'][0]['functions'] = ['FN_D2']
            elif change == 'flatten_nested_group': data['activities'][5]['capability_group'] = ['S8', 'S10', 'S3']
            elif change == 'orphan_activity':
                extra = deepcopy(data['activities'][0]); extra['id'] = 'Act_extra'; data['activities'].append(extra)
            elif change == 'missing_dependency': data['function_dependencies'].pop()
            elif change == 'reverse_dependency':
                edge = data['function_dependencies'][0]; edge['feeder'], edge['receiver'] = edge['receiver'], edge['feeder']
            elif change == 'duplicate_dependency':
                extra = deepcopy(data['function_dependencies'][0]); extra['id'] = 'FD_extra'; data['function_dependencies'].append(extra)
            elif change == 'transfer_parameters': data['function_dependencies'][0]['parameters'] = {'alpha': .21, 'beta': 34}
            elif change == 'assert_parallel': data['policy']['group_timing'] = 'parallel'
            elif change == 'claim_confirmed': data['policy']['partition_confirmed'] = True
            with self.subTest(change=change), self.assertRaises(ModelError):
                validate_structure(self.model, data)

    def test_case_revision_and_evidence_rejected_when_stale_or_unsupported(self):
        for change in ('case_id', 'case_digest', 'missing_locator', 'duplicate_locator', 'unknown_source', 'reported_activity'):
            data = deepcopy(self.structure)
            if change == 'case_id': data['case_id'] = 'other'
            elif change == 'case_digest': data['case_sha256'] = '0'*64
            elif change == 'missing_locator': data['source_locators'].pop()
            elif change == 'duplicate_locator': data['source_locators'].append(deepcopy(data['source_locators'][0]))
            elif change == 'unknown_source': data['source_locators'][0]['source'] = 'missing'
            elif change == 'reported_activity': data['activities'][0]['evidence']['kind'] = 'reported'
            with self.subTest(change=change), self.assertRaises(ModelError):
                validate_structure(self.model, data)

    def test_no_mutation_and_base_results_unchanged(self):
        scenario = json.loads((CASE.parent / 'hand_check.json').read_text(encoding='utf-8'))
        expected = evaluate(self.model, scenario)
        before = deepcopy((self.model, self.structure))
        report = inspect_structure(self.model, self.structure)
        self.assertEqual((self.model, self.structure), before)
        self.assertEqual(evaluate(self.model, scenario), expected)
        report['structure_snapshot']['functions'][0]['label'] = 'changed'
        self.assertEqual((self.model, self.structure), before)
        rows = list(csv.DictReader(io.StringIO(structure_csv(report))))
        self.assertEqual(len(rows), 11)
        self.assertEqual(rows[0]['capability'], 'S1')

    def test_cli_exports_and_refuses_overwrite_or_bad_structure(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            args = ['structure', str(CASE), '--structure', str(STRUCTURE), '--output-dir', str(folder / 'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
            report = json.loads((folder / 'out/fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['structure_snapshot'], self.structure)
            self.assertTrue((folder / 'out/structure_mapping.csv').is_file())
            self.structure['functions'].pop()
            invalid = folder / 'invalid.json'
            invalid.write_text(json.dumps(self.structure), encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['structure', str(CASE), '--structure', str(invalid),
                                       '--output-dir', str(folder / 'bad')]), 2)
            self.assertFalse((folder / 'bad').exists())


if __name__ == '__main__':
    unittest.main()
