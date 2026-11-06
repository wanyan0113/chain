"""Ontology entailments must affect selection and survive verified export."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from rdflib import Graph, RDF, RDFS, URIRef
from rdflib.namespace import OWL

from check_ontology import analyze_graph
from design.engine import DesignError, design
from design.excel import convert
from design.rdf import KW, design_to_rdf
from excel_entry.importer import read_xlsx

ROOT = Path(__file__).resolve().parents[1]


class OntologyInferenceTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((ROOT / 'design/examples/ontology_network_demo.json').read_text(encoding='utf-8'))

    def test_role_is_inferred_and_inherited_type_drives_selection(self):
        original = deepcopy(self.doc)
        report = design(self.doc)
        self.assertEqual(self.doc, original)
        self.assertEqual(report['status'], 'complete')
        coordinator = report['ontology_inference']['artifacts'][1]
        self.assertEqual(coordinator['role'], 'coordination')
        self.assertEqual(coordinator['role_path'], [str(KW.DecisionArtifact), str(KW.CoordinationArtifact)])
        selection = report['tasks'][0]['selection'][1]
        self.assertTrue(selection['accepted'])
        self.assertEqual(selection['rules'][0]['checks'][-1]['status'], 'pass')
        expected = hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()
        self.assertEqual(report['input_sha256'], expected)

    def test_new_ontology_subclass_works_without_engine_changes(self):
        graph = Graph().parse(ROOT / 'arcane_ontology.ttl')
        custom = URIRef('urn:test:NewDecisionArtifact')
        graph.add((custom, RDF.type, OWL.Class))
        graph.add((custom, RDFS.subClassOf, KW.DecisionArtifact))
        self.doc['artifacts'][1]['ontology_types'] = [str(custom)]
        with patch('design.capability_identity.load_capability_ontology', return_value=graph):
            report = design(self.doc)
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(len(report['ontology_inference']['artifacts'][1]['role_path']), 3)
            graph.remove((custom, RDFS.subClassOf, KW.DecisionArtifact))
            with self.assertRaisesRegex(DesignError, '唯一角色'):
                design(self.doc)

    def test_hierarchy_change_changes_rule_outcome_and_rejects_old_export(self):
        report = design(self.doc)
        graph = Graph().parse(ROOT / 'arcane_ontology.ttl')
        graph.remove((KW.DecisionArtifact, RDFS.subClassOf, KW.CoordinationArtifact))
        graph.add((KW.DecisionArtifact, RDFS.subClassOf, KW.ManifestationArtifact))
        with patch('design.capability_identity.load_capability_ontology', return_value=graph):
            changed = design(self.doc)
            self.assertNotEqual(changed['status'], 'complete')
            self.assertFalse(changed['tasks'][0]['selection'][1]['accepted'])
            self.assertNotEqual(report['ontology_inference']['ontology_sha256'], changed['ontology_inference']['ontology_sha256'])
            with self.assertRaises(DesignError):
                design_to_rdf(self.doc, report)

    def test_unknown_ambiguous_and_conflicting_types_are_rejected(self):
        for types, role in [([str(KW.UnknownClass)], None),
                            ([str(KW.Artifact)], None),
                            ([str(KW.InsightArtifact), str(KW.ManifestationArtifact)], None),
                            ([str(KW.DecisionArtifact)], 'insight')]:
            with self.subTest(types=types, role=role):
                doc = deepcopy(self.doc)
                doc['artifacts'][1]['ontology_types'] = types
                if role:
                    doc['artifacts'][1]['role'] = role
                with self.assertRaises(DesignError):
                    design(doc)

    def test_inferred_types_cannot_be_spoofed_and_missing_type_is_invalid(self):
        self.doc['artifacts'][0]['attributes']['ontology_types'] = [str(KW.CoordinationArtifact)]
        with self.assertRaisesRegex(DesignError, '保留字段'):
            design(self.doc)
        del self.doc['artifacts'][0]['attributes']['ontology_types']
        del self.doc['artifacts'][0]['ontology_types']
        with self.assertRaises(DesignError):
            design(self.doc)

    def test_export_contains_classification_and_passes_static_checks(self):
        report = design(self.doc)
        graph = design_to_rdf(self.doc, report, require_canonical=True)
        self.assertEqual(len(set(graph.subjects(RDF.type, KW.DecisionArtifact))), 1)
        counts = analyze_graph(Graph().parse(ROOT / 'arcane_ontology.ttl') + graph).counts()
        self.assertEqual((counts['ERROR'], counts['WARN']), (0, 0))
        report['ontology_inference']['artifacts'][1]['role_path'] = []
        with self.assertRaises(DesignError):
            design_to_rdf(self.doc, report)

    def test_existing_excel_accepts_ontology_uri_in_role_column(self):
        sheets = read_xlsx((ROOT / 'excel_entry/templates/链网设计示例.xlsx').read_bytes())
        sheets['法器'][3][2] = str(KW.DecisionArtifact)
        doc = convert(sheets)
        self.assertNotIn('role', doc['artifacts'][1])
        self.assertEqual(design(doc)['status'], 'complete')

    def test_imports_are_not_silently_ignored(self):
        graph = Graph().parse(ROOT / 'arcane_ontology.ttl')
        graph.add((URIRef('urn:test:ontology'), OWL.imports, URIRef('https://example.invalid/remote')))
        with patch('design.capability_identity.load_capability_ontology', return_value=graph):
            with self.assertRaisesRegex(DesignError, 'imports'):
                design(self.doc)

    def test_cycle_terminates_and_anonymous_restrictions_do_not_create_capabilities(self):
        graph = Graph().parse(ROOT / 'arcane_ontology.ttl')
        graph.add((KW.CoordinationArtifact, RDFS.subClassOf, KW.DecisionArtifact))
        with patch('design.capability_identity.load_capability_ontology', return_value=graph):
            report = design(self.doc)
        self.assertEqual(report['status'], 'complete')
        del self.doc['artifacts'][1]['attributes']['supported']
        self.assertEqual(design(self.doc)['status'], 'blocked')
