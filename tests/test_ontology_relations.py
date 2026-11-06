"""Positive ontology facts must change selection and chain feasibility."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from rdflib import Graph, RDF, RDFS, URIRef
from rdflib.namespace import OWL

from check_ontology import analyze_graph
from design.engine import DesignError, SearchLimitError, design
from design.excel import OPTIONAL_HEADERS, convert
from design.rdf import KW, design_to_rdf
from excel_entry.importer import read_xlsx
from excel_entry.unified import convert_design, source_sheets

ROOT = Path(__file__).resolve().parents[1]


class OntologyRelationTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((ROOT / 'design/examples/ontology_relations_demo.json').read_text(encoding='utf-8'))

    def facts(self, report):
        return report['ontology_inference']['relations']['facts']

    def test_inverse_subproperty_and_symmetric_facts_drive_complete_network(self):
        original = deepcopy(self.doc)
        report = design(self.doc)
        self.assertEqual(self.doc, original)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['network_search']['evaluated'], 4)
        self.assertEqual(report['network_search']['feasible'], 3)
        self.assertEqual({fact['derivation']['rule'] for fact in self.facts(report)},
                         {'asserted', 'inverse', 'subproperty', 'symmetric'})
        self.assertIn(str(KW.RapidResponseCoordinationCapability), report['tasks'][0]['selection'][1]['ontology_capabilities'])
        for chain in report['tasks'][0]['chains']:
            self.assertTrue(all(link['communicates'] for link in chain['link_evidence']))
            self.assertTrue(chain['link_evidence'][1]['controls'])

    def test_every_derivation_axiom_is_in_the_actual_ontology(self):
        graph = Graph().parse(ROOT / 'arcane_ontology.ttl')
        for fact in self.facts(design(self.doc)):
            axiom = fact['derivation'].get('axiom')
            if axiom:
                self.assertIn(tuple(map(URIRef, axiom)), graph)
            if fact['derivation']['rule'] != 'asserted':
                self.assertTrue(fact['derivation']['source_refs'])

    def test_missing_or_uncertain_capability_blocks_instead_of_failing(self):
        for mode in ('remove', 'issue'):
            with self.subTest(mode=mode):
                doc = deepcopy(self.doc)
                if mode == 'remove':
                    doc['ontology_relations'].pop(0)
                else:
                    doc['ontology_relations'][0]['blocking_issue_ids'] = ['pending-source']
                report = design(doc)
                self.assertEqual(report['status'], 'blocked')
                rule = report['tasks'][0]['selection'][0]['rules'][0]
                self.assertEqual(rule['checks'][-1]['status'], 'unknown')

    def test_removing_axiom_changes_design_and_blocks_stale_export(self):
        report = design(self.doc)
        graph = Graph().parse(ROOT / 'arcane_ontology.ttl')
        graph.remove((KW.hasCapability, OWL.inverseOf, KW.realizedBy))
        with patch('design.capability_identity.load_capability_ontology', return_value=graph):
            # realizedBy no longer reaches a supported capability predicate.
            with self.assertRaises(DesignError):
                design_to_rdf(self.doc, report)

    def test_symmetric_axiom_changes_actual_chain_feasibility(self):
        graph = Graph().parse(ROOT / 'arcane_ontology.ttl')
        graph.remove((KW.communicatesWith, RDF.type, OWL.SymmetricProperty))
        with patch('design.capability_identity.load_capability_ontology', return_value=graph):
            report = design(self.doc)
        self.assertEqual(report['status'], 'blocked')
        reasons = report['tasks'][0]['rejected_chains'][0]['reasons']
        self.assertIn('UNKNOWN_COMMUNICATION', [reason['code'] for reason in reasons])

    def test_capability_fact_does_not_bypass_distance_or_resources(self):
        self.doc['contexts'][0]['attributes']['target_distance']['value'] = 1000
        self.assertEqual(design(self.doc)['status'], 'no_feasible_design')
        self.setUp()
        self.doc['resource_groups'] = [{'id': 'shared', 'capacity': 1, 'artifacts': ['demo-fast', 'demo-slow']}]
        self.assertEqual(design(self.doc)['status'], 'no_feasible_design')

    def test_false_link_conflicts_with_positive_fact(self):
        self.doc['links'][0]['communicates'] = False
        with self.assertRaisesRegex(DesignError, '冲突'):
            design(self.doc)

    def test_missing_assertion_does_not_become_false_or_create_a_distance(self):
        self.doc['ontology_relations'] = [r for r in self.doc['ontology_relations']
                                          if r['predicate'] != str(KW.orchestrates)]
        self.assertEqual(design(self.doc)['status'], 'blocked')
        self.setUp()
        del self.doc['links'][0]['distance']
        with self.assertRaises(DesignError):
            design(self.doc)
        self.setUp()
        self.doc['links'].pop(0)
        report = design(self.doc)
        self.assertEqual(report['status'], 'blocked')
        self.assertIn('MISSING_LINK', [r['code'] for r in report['tasks'][0]['rejected_chains'][0]['reasons']])

    def test_no_transitive_communication_or_reverse_specialized_property(self):
        self.doc['ontology_relations'] = [r for r in self.doc['ontology_relations']
                                          if r['predicate'] != str(KW.communicatesWith)]
        relation = {'subject': {'kind': 'artifact', 'id': 'demo-eye'}, 'predicate': str(KW.communicatesWith),
                    'object': {'kind': 'artifact', 'id': 'demo-fast'},
                    'source_refs': ['fictional-test'], 'blocking_issue_ids': []}
        self.doc['ontology_relations'].append(relation)
        report = design(self.doc)
        self.assertEqual(report['status'], 'blocked')
        self.assertFalse(any(f['subject']['id'] == 'demo-eye' and f['object']['id'] == 'demo-coordinator'
                             and f['predicate'] == str(KW.communicatesWith) for f in self.facts(report)))
        self.assertFalse(any(f['subject']['id'] == 'demo-fast' and f['object']['id'] == 'demo-coordinator'
                             and f['predicate'] == str(KW.coordinationToManifestationCommunication) for f in self.facts(report)))

    def test_unknown_endpoint_property_and_wrong_capability_role_are_rejected(self):
        mutations = [('predicate', 'urn:test:unknown'), ('object_id', 'missing'),
                     ('capability', str(KW.AirManifestationCapability))]
        for kind, value in mutations:
            with self.subTest(kind=kind):
                doc = deepcopy(self.doc)
                if kind == 'predicate':
                    doc['ontology_relations'][0]['predicate'] = value
                elif kind == 'object_id':
                    doc['ontology_relations'][0]['object']['id'] = value
                else:
                    doc['ontology_relations'][0]['subject']['id'] = value
                with self.assertRaises(DesignError):
                    design(doc)
        self.doc['ontology_relations'][0]['source_refs'] = []
        with self.assertRaises(DesignError):
            design(self.doc)

    def test_new_subproperty_works_without_selection_code_changes(self):
        graph = Graph().parse(ROOT / 'arcane_ontology.ttl')
        prop = URIRef('urn:test:NewCommunication')
        graph.add((prop, RDF.type, OWL.ObjectProperty))
        graph.add((prop, RDFS.subPropertyOf, KW.coordinationToManifestationCommunication))
        for relation in self.doc['ontology_relations']:
            if relation['predicate'] == str(KW.coordinationToManifestationCommunication):
                relation['predicate'] = str(prop)
        with patch('design.capability_identity.load_capability_ontology', return_value=graph):
            self.assertEqual(design(self.doc)['status'], 'complete')
            graph.add((KW.communicatesWith, RDFS.subPropertyOf, prop))
            # This cycle entails an invalid typed reverse relation; reject it.
            with self.assertRaises(DesignError):
                design(self.doc)

    def test_export_contains_derived_relations_and_rejects_tampering(self):
        report = design(self.doc)
        graph = design_to_rdf(self.doc, report, require_canonical=True)
        self.assertTrue(list(graph.triples((None, KW.realizedBy, None))))
        self.assertTrue(list(graph.triples((None, KW.communicatesWith, None))))
        counts = analyze_graph(Graph().parse(ROOT / 'arcane_ontology.ttl') + graph).counts()
        self.assertEqual((counts['ERROR'], counts['WARN']), (0, 0))
        self.facts(report)[0]['derivation']['source_refs'] = ['tampered']
        with self.assertRaises(DesignError):
            design_to_rdf(self.doc, report)

    def test_limits_are_not_silent_truncation_and_reserved_facts_cannot_be_spoofed(self):
        with patch('design.ontology_relations.MAX_FACTS', 1):
            with self.assertRaises(SearchLimitError):
                design(self.doc)
        self.doc['artifacts'][0]['attributes']['ontology_capabilities'] = [str(KW.AirInsightCapability)]
        with self.assertRaisesRegex(DesignError, '保留字段'):
            design(self.doc)

    def add_excel_relations(self, sheets):
        rows = {1: dict(enumerate(OPTIONAL_HEADERS['本体关系'], 1))}
        rows[2] = dict(enumerate(['法器', 'demo-coordinator', str(KW.communicatesWith),
                                  '法器', 'demo-eye', 'fictional-test', None], 1))
        sheets['本体关系'] = rows
        sheets['协同关系'][2][3] = '本体推导'

    def test_optional_excel_sheet_and_unified_source_entry_are_compatible(self):
        sheets = read_xlsx((ROOT / 'excel_entry/templates/链网设计示例.xlsx').read_bytes())
        self.add_excel_relations(sheets)
        doc = convert(sheets)
        self.assertIsNone(doc['links'][0]['communicates'])
        self.assertEqual(design(doc)['status'], 'complete')
        sheets = read_xlsx((ROOT / 'excel_entry/templates/案例设计演示.xlsx').read_bytes())
        self.add_excel_relations(sheets)
        self.assertNotIn('本体关系', source_sheets(sheets))
        doc, bridge = convert_design(sheets)
        self.assertTrue(bridge)
        self.assertEqual(design(doc)['status'], 'complete')
