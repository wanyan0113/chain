"""Optional real reasoner checks, including rejection of inconsistent data."""
import importlib.util
import shutil
import unittest

from rdflib import Graph, Namespace, RDF, RDFS, OWL

from adapters.owl_check import check_owl

T = Namespace('urn:technical-owl-test:')


class OwlCheckTests(unittest.TestCase):
    def test_external_import_not_loaded(self):
        graph = Graph()
        graph.add((T.ontology, OWL.imports, T.external))
        self.assertEqual(check_owl(graph)['status'], 'not_run')

    @unittest.skipUnless(importlib.util.find_spec('owlready2') and shutil.which('java'), 'optional HermiT dependency and Java required')
    def test_real_consistent_and_inconsistent_ontology(self):
        graph = Graph()
        graph.add((T.A, RDF.type, OWL.Class))
        graph.add((T.B, RDF.type, OWL.Class))
        graph.add((T.A, OWL.disjointWith, T.B))
        graph.add((T.example, RDF.type, T.A))
        self.assertEqual(check_owl(graph)['status'], 'passed')
        graph.add((T.example, RDF.type, T.B))
        report = check_owl(graph)
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['ontology_consistent'])
