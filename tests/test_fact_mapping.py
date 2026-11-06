"""Boundary tests for typed facts and open-world rule conditions."""
import unittest

from rdflib import Graph, RDF, URIRef
from rdflib.namespace import OWL, XSD

from design.engine import DesignError, condition_result
from design.fact_mapping import SCENE_MAPPINGS, mapped_literal, project_facts, validate_mappings
from design.owl_scene import scene_uri


class FactMappingTests(unittest.TestCase):
    def mapping(self, **overrides):
        return dict(id='flag', kind='task', field='flag', property='urn:test:flag',
                    datatype='boolean', source_refs=['test'], **overrides)

    def test_boolean_integer_and_string_types_do_not_coerce(self):
        mapping = self.mapping()
        self.assertEqual(mapped_literal(mapping, True)[0].datatype, XSD.boolean)
        self.assertIsNone(mapped_literal(mapping, 1)[0])
        mapping.update(datatype='integer', unit='count')
        self.assertEqual(mapped_literal(mapping, {'value': 2, 'unit': 'count'})[0].toPython(), 2)
        for value in (True, 2.5):
            self.assertIsNone(mapped_literal(mapping, {'value': value, 'unit': 'count'})[0])
        mapping.update(datatype='string')
        self.assertIsNone(mapped_literal(mapping, False)[0])
        self.assertEqual(str(mapped_literal(mapping, 'name')[0]), 'name')

    def test_task_and_artifact_projection_preserves_optional_unknown_and_blocked_source(self):
        mappings = [self.mapping(required=False),
                    {**self.mapping(), 'id': 'artifact-flag', 'kind': 'artifact'}]
        document = {'tasks': [{'id': 't', 'attributes': {}, 'blocking_issue_ids': [], 'source_refs': ['scene']}],
                    'artifacts': [{'id': 'a', 'attributes': {'flag': True}, 'blocking_issue_ids': [], 'source_refs': ['scene']}]}
        token = SCENE_MAPPINGS.set(mappings)
        self.addCleanup(SCENE_MAPPINGS.reset, token)
        graph = Graph()
        evidence = project_facts(document, graph, scene_uri)
        self.assertEqual(evidence[0]['status'], 'unknown')
        self.assertFalse(evidence[0]['required'])
        self.assertEqual(evidence[1]['status'], 'pass')
        self.assertTrue(list(graph.objects(scene_uri('artifact', 'a'), URIRef('urn:test:flag'))))
        document['artifacts'][0]['blocking_issue_ids'] = ['unverified']
        graph = Graph()
        self.assertEqual(project_facts(document, graph, scene_uri)[1]['status'], 'unknown')
        self.assertEqual(len(graph), 0)

    def test_duplicate_mapping_ids_are_rejected(self):
        graph = Graph()
        graph.add((URIRef('urn:test:flag'), RDF.type, OWL.DatatypeProperty))
        with self.assertRaises(DesignError):
            validate_mappings({'mappings': [self.mapping(), self.mapping()]}, graph)

    def test_absent_owl_membership_is_unknown_but_known_numeric_failure_is_false(self):
        for field in ('artifact.ontology_types', 'task.ontology_types', 'artifact.ontology_capabilities'):
            scope, key = field.split('.')
            status, _ = condition_result({'field': field, 'op': 'contains', 'value': 'urn:test:Class'}, {scope: {key: []}})
            self.assertEqual(status, 'unknown')
        status, _ = condition_result({'field': 'task.height', 'op': 'gt', 'value': {'value': 100, 'unit': 'ft'}},
                                     {'task': {'height': {'value': 50, 'unit': 'ft'}}})
        self.assertEqual(status, 'fail')
