"""Real OWL + external rules: knowledge changes without engine changes."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph, RDF, URIRef

from design.engine import DesignError, design
from design.capabilities import infer_capabilities, capability_rdf
from design.capability_identity import CANONICAL_CAPABILITIES
from design.excel import convert
from design.knowledge import knowledge_outputs, ontology_for_document
from design.rdf import KW, design_to_rdf
from excel_entry.importer import read_xlsx
from check_ontology import analyze_graph

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'design/examples/knowledge_driven_demo.json'
D = 'urn:arcane-knowledge:demo:'


@unittest.skipUnless(importlib.util.find_spec('owlready2') and shutil.which('java'), 'real HermiT and Java required')
class KnowledgeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = design(json.loads(DEMO.read_text(encoding='utf-8')))

    def setUp(self):
        self.doc = json.loads(DEMO.read_text(encoding='utf-8'))
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ('demo-task-capabilities.ttl', 'demo-selection.rules.json'):
            shutil.copyfile(ROOT / 'knowledge' / name, self.root / name)
        self.patcher = patch('design.knowledge.KNOWLEDGE_ROOT', self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.ttl = self.root / 'demo-task-capabilities.ttl'
        self.rules = self.root / 'demo-selection.rules.json'

    def test_real_equivalent_classes_and_named_requirements_drive_network(self):
        report = self.baseline
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['owl_reasoning']['engine'], 'HermiT')
        self.assertEqual(report['owl_reasoning']['status'], 'passed')
        self.assertFalse(self.doc['task_rules'])
        self.assertFalse(self.doc['artifact_rules'])
        self.assertIn(D + 'OrbMission', report['tasks'][0]['owl_evidence']['types'])
        self.assertEqual([len(task['chains']) for task in report['tasks']], [2, 2])
        self.assertEqual(report['network_search']['feasible'], 3)
        self.assertTrue(all(artifact['role_basis'] == 'HermiT' for artifact in report['ontology_inference']['artifacts']))

    def test_scene_change_and_new_knowledge_class_need_no_engine_change(self):
        for task in self.doc['tasks']:
            task['attributes']['target_type'] = 'changed-target'
        for artifact in self.doc['artifacts']:
            if 'target_types' in artifact['attributes']:
                artifact['attributes']['target_types'] = ['changed-target']
        self.assertEqual(design(self.doc)['status'], 'blocked')
        extension = '''
demo:ChangedTarget a owl:Class ; owl:equivalentClass [ a owl:Class ; owl:intersectionOf (
  kw:Target [ a owl:Restriction ; owl:onProperty kw:targetType ; owl:hasValue "changed-target"^^xsd:string ]) ] .
demo:ChangedMission a owl:Class ; owl:equivalentClass [ a owl:Class ; owl:intersectionOf (
  kw:ArcaneMission [ a owl:Restriction ; owl:onProperty kw:hasTarget ; owl:someValuesFrom demo:ChangedTarget ]) ] ;
  rdfs:subClassOf demo:OrbMission .
'''
        self.ttl.write_text(self.ttl.read_text(encoding='utf-8') + extension, encoding='utf-8')
        report = design(self.doc)
        self.assertEqual(report['status'], 'complete')
        self.assertIn(D + 'ChangedMission', report['tasks'][0]['owl_evidence']['types'])

    def test_new_capability_uri_requires_no_python_registry_entry(self):
        self.ttl.write_text(self.ttl.read_text(encoding='utf-8').replace('kw:ShortRangeManifestationCapability', 'demo:LocalEffect')
                            + '\ndemo:LocalEffect a owl:NamedIndividual, kw:ManifestationCapability ; rdfs:label "Local effect"@en .\n', encoding='utf-8')
        self.rules.write_text(self.rules.read_text(encoding='utf-8').replace(str(KW.ShortRangeManifestationCapability), D + 'LocalEffect'), encoding='utf-8')
        report = design(self.doc)
        self.assertEqual(report['status'], 'complete')
        self.assertTrue(report['canonical_export_ready'])
        self.assertEqual(report['input_sha256'], self.baseline['input_sha256'])
        self.assertNotEqual(report['execution_sha256'], self.baseline['execution_sha256'])
        self.assertIn(D + 'LocalEffect', report['tasks'][0]['required_capabilities']['manifestation'])
        self.assertFalse(any('LocalEffect' in registry.values() for registry in CANONICAL_CAPABILITIES.values()))
        graph = design_to_rdf(self.doc, report, require_canonical=True)
        self.assertTrue(list(graph.subjects(KW.requiresCapability, URIRef(D + 'LocalEffect'))))
        self.assertTrue(all(report['execution_sha256'] in str(web) for web in graph.subjects(RDF.type, KW.ArcaneWeb)))
        counts = analyze_graph(ontology_for_document(self.doc) + graph).counts()
        self.assertEqual((counts['ERROR'], counts['WARN']), (0, 0))

    def test_external_numeric_rule_change_changes_feasibility_and_invalidates_report(self):
        rules = json.loads(self.rules.read_text(encoding='utf-8'))
        rules['artifact_rules'][0]['when'][0] = {'field': 'artifact.max_range', 'op': 'gt',
                                               'value': {'value': 10000, 'unit': 'nmi'}}
        self.rules.write_text(json.dumps(rules, ensure_ascii=False), encoding='utf-8')
        self.assertEqual(design(self.doc)['status'], 'no_feasible_design')
        with self.assertRaises(DesignError):
            design_to_rdf(self.doc, self.baseline)
        with self.assertRaises(DesignError):
            knowledge_outputs(self.doc, self.baseline)

    def test_explicit_rule_can_consume_actual_owl_task_classification(self):
        rules = json.loads(self.rules.read_text(encoding='utf-8'))
        rules['task_rules'] = [{'id': 'owl-class-to-extra-capability', 'role': 'manifestation',
            'capability': str(KW.AirManifestationCapability), 'when': [
                {'field': 'task.ontology_types', 'op': 'contains', 'value': D + 'OrbMission'}],
            'source_refs': ['fictional-rule'], 'blocking_issue_ids': []}]
        self.rules.write_text(json.dumps(rules), encoding='utf-8')
        doc = {key: deepcopy(self.doc[key]) for key in ('format_version', 'scope', 'tasks', 'task_rules', 'knowledge')}
        doc['required_roles'] = ['manifestation']
        report = infer_capabilities(doc)
        self.assertEqual(report['status'], 'complete')
        self.assertIn(str(KW.AirManifestationCapability), report['tasks'][0]['required_capabilities']['manifestation'])
        self.assertEqual(report['tasks'][0]['inference'][0]['checks'][0]['status'], 'pass')

    def test_existential_restriction_does_not_invent_a_named_capability(self):
        self.ttl.write_text(self.ttl.read_text(encoding='utf-8').replace(
            'owl:onProperty kw:requiresCapability ; owl:hasValue kw:ShortRangeManifestationCapability',
            'owl:onProperty kw:requiresCapability ; owl:someValuesFrom kw:ManifestationCapability'), encoding='utf-8')
        report = design(self.doc)
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['tasks'][0]['required_capabilities']['manifestation'], [])

    def test_inconsistency_or_reasoner_unavailability_never_silently_falls_back(self):
        with patch('adapters.owl_infer.infer_owl', return_value={'status': 'not_run', 'reason': 'missing Java'}):
            with self.assertRaisesRegex(DesignError, '不回退'):
                design(self.doc)
        self.ttl.write_text(self.ttl.read_text(encoding='utf-8') + '\ndemo:InsightUnit rdfs:subClassOf owl:Nothing .\n', encoding='utf-8')
        with self.assertRaisesRegex(DesignError, 'OWL推理未完成'):
            design(self.doc)

    def test_local_path_import_and_external_rule_contract_guards(self):
        self.doc['knowledge']['ontology_files'] = ['../arcane_ontology.ttl']
        with self.assertRaisesRegex(DesignError, 'knowledge'):
            design(self.doc)
        self.doc['knowledge']['ontology_files'] = ['demo-task-capabilities.ttl']
        self.ttl.write_text(self.ttl.read_text(encoding='utf-8') + '\ndemo:ontology owl:imports <https://example.invalid/remote> .\n', encoding='utf-8')
        with self.assertRaisesRegex(DesignError, 'imports'):
            design(self.doc)
        shutil.copyfile(ROOT / 'knowledge/demo-task-capabilities.ttl', self.ttl)
        self.rules.write_text('{"task_rules":[],"task_rules":[],"artifact_rules":[]}', encoding='utf-8')
        with self.assertRaisesRegex(DesignError, '重复'):
            design(self.doc)

    def test_duplicate_rule_ids_are_rejected_before_reasoning(self):
        self.doc['artifact_rules'] = [json.loads(self.rules.read_text(encoding='utf-8'))['artifact_rules'][0]]
        with patch('adapters.owl_infer.infer_owl') as reasoner:
            with self.assertRaisesRegex(DesignError, '重复'):
                design(self.doc)
            reasoner.assert_not_called()

    def test_snapshots_prove_materialization_and_no_input_mutation(self):
        original = deepcopy(self.doc)
        report = design(self.doc)
        self.assertEqual(self.doc, original)
        outputs = knowledge_outputs(self.doc, report)
        before = Graph().parse(data=outputs['reasoning_input.ttl'], format='turtle')
        after = Graph().parse(data=outputs['reasoning_result.ttl'], format='turtle')
        task_uri = URIRef(report['tasks'][0]['owl_evidence']['task_uri'])
        self.assertFalse(list(before.objects(task_uri, KW.requiresCapability)))
        self.assertTrue(list(after.objects(task_uri, KW.requiresCapability)))
        snapshot = json.loads(outputs['knowledge_snapshot.json'])
        self.assertEqual(len(snapshot['files']), 3)
        self.assertTrue(all(file['sha256'] and file['content'] for file in snapshot['files']))
        legacy = json.loads((ROOT / 'design/examples/canonical_network_demo.json').read_text(encoding='utf-8'))
        self.assertNotIn('knowledge', design(legacy))

    def test_standalone_owl_capabilities_and_verified_rdf(self):
        doc = {key: deepcopy(self.doc[key]) for key in ('format_version', 'scope', 'tasks', 'task_rules', 'knowledge')}
        doc['required_roles'] = ['insight', 'coordination', 'manifestation']
        report = infer_capabilities(doc)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['tasks'][0]['required_capabilities'], self.baseline['tasks'][0]['required_capabilities'])
        graph = capability_rdf(doc, report, require_canonical=True)
        counts = analyze_graph(ontology_for_document(doc) + graph).counts()
        self.assertEqual((counts['ERROR'], counts['WARN']), (0, 0))

    def test_excel_can_reference_external_knowledge_without_template_column_changes(self):
        sheets = read_xlsx((ROOT / 'excel_entry/templates/链网设计示例.xlsx').read_bytes())
        for key, value in [('本体知识文件', 'demo-task-capabilities.ttl'), ('业务规则文件', 'demo-selection.rules.json'), ('知识来源', 'fictional-demo')]:
            sheets['设计设置'][max(sheets['设计设置']) + 1] = {1: key, 2: value}
        for row, cls in [(2, 'InsightUnit'), (3, 'CoordinationUnit'), (4, 'EffectUnit'), (5, 'EffectUnit')]:
            sheets['法器'][row][2] = D + cls
        sheets['规则'] = {1: sheets['规则'][1]}
        sheets['规则条件'] = {1: sheets['规则条件'][1]}
        report = design(convert(sheets))
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['owl_reasoning']['engine'], 'HermiT')

    def test_cli_uses_merged_ontology_and_saves_reviewable_outputs(self):
        # The actual CLI resolves the repository's knowledge directory, not the
        # temporary test root in this process.
        out = self.root / 'cli-output'
        result = subprocess.run([sys.executable, '-B', '-m', 'design', 'run', str(DEMO), '--output-dir', str(out),
                                 '--require-canonical'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['rdf_validation']['ERROR'], 0)
        self.assertEqual(report['rdf_validation']['WARN'], 0)
        for name in ('design.ttl', 'reasoning_input.ttl', 'reasoning_result.ttl', 'knowledge_snapshot.json'):
            self.assertTrue((out / name).exists(), name)
