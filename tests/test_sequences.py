"""Sequential trajectories: real OWL requirements, pruning and stage resources."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from rdflib import RDF, URIRef

from check_ontology import analyze_graph
from adapters.owl_check import check_owl
from design.engine import DesignError, SearchLimitError, design
from design.knowledge import knowledge_outputs, ontology_for_document
from design.rdf import KW, design_to_rdf
from design.sequences import connection, step_id
from design.owl_scene import scene_uri

ROOT = Path(__file__).resolve().parents[1]
D = 'urn:arcane-knowledge:demo:'


def make_fixture(root, activities=('observe', 'decide', 'act', 'assess')):
    """Synthetic technical fixture, created locally; not paper input data."""
    doc = json.loads((ROOT / 'design/examples/knowledge_driven_demo.json').read_text(encoding='utf-8'))
    for name in ('demo-task-capabilities.ttl', 'demo-selection.rules.json'):
        shutil.copyfile(ROOT / 'knowledge' / name, root / name)
    ttl = root / 'demo-task-capabilities.ttl'
    ttl.write_text(ttl.read_text(encoding='utf-8') + '''
demo:AssessCapability a owl:NamedIndividual, kw:InsightCapability ; rdfs:label "Assess"@en .
demo:AssessStep a owl:Class ; rdfs:label "Assessment step"@en ; rdfs:subClassOf kw:SequentialStepMission,
  [ a owl:Restriction ; owl:onProperty kw:requiresCapability ; owl:hasValue demo:AssessCapability ] .
demo:InsightUnit rdfs:subClassOf
  [ a owl:Restriction ; owl:onProperty kw:hasCapability ; owl:hasValue demo:AssessCapability ] .
''', encoding='utf-8')
    rules_path = root / 'demo-selection.rules.json'
    rules = json.loads(rules_path.read_text(encoding='utf-8'))
    rules['artifact_rules'].append({'id': 'assessment', 'role': 'insight', 'capability': D + 'AssessCapability',
        'when': [{'field': 'artifact.ontology_capabilities', 'op': 'contains', 'value': D + 'AssessCapability'}],
        'source_refs': ['synthetic-test'], 'blocking_issue_ids': []})
    rules_path.write_text(json.dumps(rules), encoding='utf-8')
    roles = {'observe': 'insight', 'decide': 'coordination', 'act': 'manifestation', 'assess': 'insight'}
    caps = {'observe': str(KW.AirInsightCapability), 'decide': str(KW.RapidResponseCoordinationCapability),
            'act': str(KW.ShortRangeManifestationCapability), 'assess': str(KW.AirInsightCapability)}
    config = {'templates': [{'id': 'trajectory', 'when': [], 'steps': [],
        'allow_nonadjacent_reuse': True, 'source_refs': ['synthetic-test']}],
        'communication_model': 'directed', 'evaluation': {'mode': 'sequential_product', 'source_refs': ['explicit-test-assumption']},
        'source_refs': ['synthetic-test']}
    for index, activity in enumerate(activities):
        step = {'id': f's{index+1}', 'activity': activity, 'role': roles[activity],
                'capabilities': [caps[activity]], 'min_level': 0.8, 'source_refs': ['synthetic-test']}
        if index:
            step['incoming_relation'] = 'control' if activities[index-1] == 'decide' and activity == 'act' else 'communication'
        if activity == 'assess':
            step['ontology_types'] = [D + 'AssessStep']
        config['templates'][0]['steps'].append(step)
    for artifact in doc['artifacts']:
        role = ('insight' if artifact['id'] == 'demo-eye' else 'coordination' if artifact['id'] == 'demo-coordinator' else 'manifestation')
        artifact['activity_metrics'] = {activity: deepcopy(artifact['metrics']) for activity in roles if roles[activity] == role}
        for activity in artifact['activity_metrics']:
            artifact['attributes']['level_' + activity] = {'value': 0.9, 'unit': '1'}
    # Explicit complete directed fixture connectivity, retaining control directions.
    base_link = doc['links'][0]
    doc['links'] = [{**deepcopy(base_link), 'from': a['id'], 'to': b['id'],
                     'controls': a['id'] == 'demo-coordinator' and b['id'] in ('demo-fast', 'demo-slow')}
                    for a in doc['artifacts'] for b in doc['artifacts'] if a['id'] != b['id']]
    doc['knowledge']['sequences_file'] = 'sequences.json'
    (root / 'sequences.json').write_text(json.dumps(config), encoding='utf-8')
    return doc, config


@unittest.skipUnless(importlib.util.find_spec('owlready2') and shutil.which('java'), 'HermiT and Java required')
class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.doc, self.config = make_fixture(self.root)
        context = patch('design.knowledge.KNOWLEDGE_ROOT', self.root)
        context.start()
        self.addCleanup(context.stop)

    def save(self):
        (self.root / 'sequences.json').write_text(json.dumps(self.config), encoding='utf-8')

    def test_three_step_chain_without_assessment_reuses_original_scoring(self):
        self.doc, self.config = make_fixture(self.root, ('observe', 'decide', 'act'))
        # No assessment inputs are required when assessment is absent.
        eye = self.doc['artifacts'][0]
        del eye['activity_metrics']['assess']
        del eye['attributes']['level_assess']
        report = design(self.doc)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual([len(t['chains']) for t in report['tasks']], [2, 2])
        legacy_doc = deepcopy(self.doc)
        del legacy_doc['knowledge']['sequences_file']
        legacy = design(legacy_doc)
        self.assertEqual(legacy['status'], 'complete')
        for current, old in zip(report['tasks'], legacy['tasks']):
            expected = {c['artifacts']['manifestation']: c for c in old['chains']}
            for chain in current['chains']:
                self.assertEqual([s['activity'] for s in chain['plan']['steps']], ['observe', 'decide', 'act'])
                previous = expected[chain['artifacts']['s3']]
                self.assertEqual(chain['metrics'], previous['metrics'])
                self.assertAlmostEqual(chain['score'], previous['score'])
        self.assertTrue(report['canonical_export_ready'])
        design_to_rdf(self.doc, report, require_canonical=True)

    def test_two_consecutive_command_steps_are_not_split_into_subroles(self):
        self.doc, self.config = make_fixture(self.root, ('observe', 'decide', 'decide', 'act'))
        coordinator = next(a for a in self.doc['artifacts'] if a['id'] == 'demo-coordinator')
        self.doc['artifacts'].append({**deepcopy(coordinator), 'id': 'coordinator-2'})
        edge = self.doc['links'][0]
        self.doc['links'] = [{**deepcopy(edge), 'from': a['id'], 'to': b['id'], 'controls': True}
                            for a in self.doc['artifacts'] for b in self.doc['artifacts'] if a['id'] != b['id']]
        report = design(self.doc)
        self.assertEqual(report['status'], 'complete')
        for task in report['tasks']:
            self.assertTrue(task['chains'])
            for chain in task['chains']:
                steps = chain['plan']['steps']
                self.assertEqual([s['activity'] for s in steps], ['observe', 'decide', 'decide', 'act'])
                self.assertEqual([s['role'] for s in steps], ['insight', 'coordination', 'coordination', 'manifestation'])
                self.assertNotEqual(steps[1]['artifact'], steps[2]['artifact'])
                self.assertEqual(len(chain['plan']['members']['coordination']), 2)
                self.assertEqual(chain['plan']['connections'][1]['relation'], 'communication')
                strike = next(a for a in self.doc['artifacts'] if a['id'] == steps[-1]['artifact'])['metrics']
                self.assertEqual(chain['metrics']['time'], 3 + strike['duration_s'])
                self.assertAlmostEqual(chain['metrics']['accuracy'], .99 * .95 * .95 *
                                       strike['accuracy'])
        design_to_rdf(self.doc, report, require_canonical=True)

    def test_more_than_six_steps_without_assessment(self):
        activities = ('observe', 'decide', 'observe', 'decide', 'observe', 'decide', 'act')
        self.doc, self.config = make_fixture(self.root, activities)
        report = design(self.doc)
        self.assertEqual(report['status'], 'complete')
        for task in report['tasks']:
            self.assertTrue(task['chains'])
            for chain in task['chains']:
                self.assertEqual([s['activity'] for s in chain['plan']['steps']], list(activities))
                strike = next(a for a in self.doc['artifacts'] if a['id'] == chain['artifacts']['s7'])['metrics']
                self.assertEqual(chain['metrics']['time'], 6 + strike['duration_s'])
                self.assertEqual(len(chain['plan']['resource_usage']), 7)

    def test_sequence_backbone_is_still_required(self):
        for activities in (('observe', 'decide'), ('observe', 'observe', 'act'),
                           ('decide', 'observe', 'act'), ('observe', 'act', 'decide')):
            with self.subTest(activities=activities):
                self.doc, self.config = make_fixture(self.root, activities)
                with self.assertRaises(DesignError):
                    design(self.doc)

    def test_real_step_entailment_export_and_stage_resources(self):
        report = design(self.doc)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual([len(t['chains']) for t in report['tasks']], [2, 2])
        chain = report['tasks'][0]['chains'][0]
        self.assertEqual(len(chain['plan']['steps']), 4)
        self.assertEqual(chain['metrics']['time'], 4)
        self.assertAlmostEqual(chain['metrics']['accuracy'], .99 * .95 * .9 * .99)
        assessment = chain['plan']['steps'][-1]
        self.assertIn(D + 'AssessCapability', assessment['required_capabilities'])
        self.assertNotIn(D + 'AssessCapability', self.config['templates'][0]['steps'][-1]['capabilities'])
        self.assertNotIn(D + 'AssessCapability', chain['plan']['steps'][0]['required_capabilities'])
        self.assertEqual([(u['start_s'], u['end_s']) for u in chain['plan']['resource_usage']], [(0,1),(1,2),(2,3),(3,4)])
        graph = design_to_rdf(self.doc, report, require_canonical=True)
        self.assertEqual(len(list(graph.subjects(RDF.type, KW.SpellChain))), 2)
        self.assertEqual(len(list(graph.subjects(KW.nodeOrder, None))), 8)
        counts = analyze_graph(ontology_for_document(self.doc) + graph).counts()
        self.assertEqual((counts['ERROR'], counts['WARN']), (0, 0))
        self.assertEqual(check_owl(ontology_for_document(self.doc) + graph)['status'], 'passed')
        outputs = knowledge_outputs(self.doc, report)
        self.assertIn('AssessCapability', outputs['reasoning_result.ttl'])
        self.assertTrue(any(f['path'] == 'knowledge/sequences.json' for f in report['knowledge']['files']))

    def test_six_steps_multiple_distinct_observers_and_coordinators(self):
        self.doc, self.config = make_fixture(self.root, ('observe', 'decide', 'act', 'decide', 'act', 'assess'))
        # Different executors per repeated step via per-activity level restrictions
        # are not required: choose from both distinct instances and inspect output.
        for old, new in (('demo-eye', 'eye-2'), ('demo-coordinator', 'coordinator-2')):
            source = next(a for a in self.doc['artifacts'] if a['id'] == old)
            self.doc['artifacts'].append({**deepcopy(source), 'id': new})
            for context in list(self.doc['contexts']):
                if context['artifact'] == old:
                    self.doc['contexts'].append({**deepcopy(context), 'artifact': new})
        edge = self.doc['links'][0]
        self.doc['links'] = [{**deepcopy(edge), 'from': a['id'], 'to': b['id'], 'controls': True}
                            for a in self.doc['artifacts'] for b in self.doc['artifacts'] if a['id'] != b['id']]
        report = design(self.doc)
        self.assertEqual(report['status'], 'complete')
        self.assertTrue(any(all(len(c['plan']['members'][r]) == 2 for r in ('insight', 'coordination', 'manifestation'))
                            for c in report['tasks'][0]['chains']))
        self.assertTrue(all(len(c['plan']['steps']) == 6 for c in report['tasks'][0]['chains']))
        # 2^6 assignments minus 16 that consume demo-fast twice (inventory 1).
        self.assertEqual(len(report['tasks'][0]['chains']), 48)

    def test_external_threshold_and_unknown_levels(self):
        self.config['templates'][0]['steps'][-1]['min_level'] = .95
        self.save()
        self.assertEqual(design(self.doc)['status'], 'no_feasible_design')
        self.doc['artifacts'][0]['attributes']['level_assess']['unit'] = 'invalid'
        self.assertEqual(design(self.doc)['status'], 'blocked')

    def test_feasibility_mode_does_not_claim_network_optimum(self):
        self.config['evaluation']['mode'] = 'feasibility_only'
        self.save()
        for a in self.doc['artifacts']:
            del a['activity_metrics']
        report = design(self.doc)
        self.assertEqual(report['status'], 'feasible_trajectories')
        self.assertIsNone(report['network'])
        self.assertFalse(report['canonical_export_ready'])
        self.assertTrue(all(c['score'] is None and c['metrics'] is None for t in report['tasks'] for c in t['chains']))
        with self.assertRaises(DesignError):
            design_to_rdf(self.doc, report)

    def test_no_fallback_to_legacy_metrics(self):
        del self.doc['artifacts'][0]['activity_metrics']['assess']
        report = design(self.doc)
        self.assertEqual(report['status'], 'blocked')
        self.assertIn('MISSING_ACTIVITY_METRICS', [b['code'] for b in report['tasks'][0]['blockers']])

    def test_reuse_cooldown_inventory_and_limits(self):
        self.config['templates'][0]['allow_nonadjacent_reuse'] = False
        self.save()
        self.assertEqual(design(self.doc)['status'], 'no_feasible_design')
        self.config['templates'][0]['allow_nonadjacent_reuse'] = True
        self.save()
        eye = self.doc['artifacts'][0]
        eye['capacity'], eye['cooldown_s'] = 1, 10
        report = design(self.doc)
        self.assertEqual(report['status'], 'no_feasible_design')
        self.assertGreater(report['tasks'][0]['sequence_generation']['pruned']['INTERNAL_RESOURCE_CONFLICT'], 0)
        eye['cooldown_s'], eye['consumable'], eye['inventory'] = 0, True, 1
        self.assertEqual(design(self.doc)['status'], 'no_feasible_design')
        eye['consumable'] = False
        self.doc['policies']['max_sequence_search_nodes'] = 1
        with self.assertRaises(SearchLimitError):
            design(self.doc)
        del self.doc['policies']['max_sequence_search_nodes']
        self.doc['policies']['max_chain_combinations'] = 1
        with self.assertRaises(SearchLimitError):
            design(self.doc)

    def test_exact_network_search_agreement(self):
        reports = []
        for method in ('exhaustive', 'branch_and_bound'):
            self.doc['policies']['network_search_method'] = method
            reports.append(design(self.doc))
        self.assertEqual(reports[0]['network'], reports[1]['network'])

    def test_stale_template_and_semantic_validation(self):
        report = design(self.doc)
        self.config['templates'][0]['steps'][0]['min_level'] = .91
        self.save()
        with self.assertRaises(DesignError):
            design_to_rdf(self.doc, report)
        with self.assertRaises(DesignError):
            knowledge_outputs(self.doc, report)
        self.config['templates'][0]['steps'][-1]['incoming_relation'] = 'control'
        self.save()
        with self.assertRaises(DesignError):
            design(self.doc)
        self.doc['knowledge']['sequences_file'] = '../outside.json'
        with self.assertRaises(DesignError):
            design(self.doc)


class ConnectionTests(unittest.TestCase):
    def test_undirected_physical_and_directed_control_and_unknown(self):
        edge = {'communicates': True, 'controls': True, 'distance': {'value': 1},
                'max_distance': {'value': 2}, 'blocking_issue_ids': []}
        links = {('b', 'a'): edge}
        self.assertEqual(connection('a', 'b', 'communication', links, 'undirected')[0], 'pass')
        self.assertEqual(connection('a', 'b', 'control', links, 'undirected')[0], 'unknown')
        self.assertEqual(connection('a', 'b', 'communication', links, 'directed')[0], 'unknown')
        links['a', 'b'] = {**edge, 'communicates': False}
        self.assertEqual(connection('a', 'b', 'communication', links, 'undirected')[0], 'fail')
        links['a', 'b']['communicates'] = None
        self.assertEqual(connection('a', 'b', 'communication', links, 'undirected')[0], 'unknown')


if __name__ == '__main__':
    unittest.main()
