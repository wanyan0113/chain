"""Observer transparency and truthful nesting of phase-2B instrumentation."""
from pathlib import Path
import tempfile
import unittest
from rdflib import Graph,URIRef,RDF
from rdflib.namespace import OWL,RDFS
from adapters.owl_infer import infer_owl,worker
from workflow_tools.owl_profile_worker import Timings
from workflow_tools.benchmark_joint import ROOT,save


class OwlProfileTests(unittest.TestCase):
    def test_timer_nested_events_and_failure_keep_stack_balanced(self):
        t=Timings()
        def inner():raise ValueError('probe')
        def outer():return t.wrap('inner',inner)()
        with self.assertRaises(ValueError):t.wrap('outer',outer)()
        self.assertEqual(t.stack,[])
        self.assertGreaterEqual(t.values['outer'][0],t.values['inner'][0])

    def test_real_adapter_observer_does_not_change_entailments_or_result_schema(self):
        g=Graph();a,b,s=map(URIRef,('urn:profile:A','urn:profile:B','urn:profile:s'))
        for triple in [(a,RDF.type,OWL.Class),(b,RDF.type,OWL.Class),(a,RDFS.subClassOf,b),
                       (s,RDF.type,OWL.NamedIndividual),(s,RDF.type,a)]:g.add(triple)
        plain=infer_owl(g,[str(s)],[]);t=Timings();observed=infer_owl(g,[str(s)],[],observer=t)
        self.assertEqual(plain['status'],'passed');self.assertEqual(plain,observed)
        self.assertNotIn('timings',observed)
        self.assertIn('adapter_input_serialization',t.values)
        self.assertIn('python_worker_launch_and_wait',t.values)
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            path=Path(directory);g.serialize(path/'input.rdf',format='xml')
            save(path/'query.json',dict(subjects=[str(s)],predicates=[]));child=Timings()
            result=worker(path,observer=child)
        self.assertEqual(result,plain)
        self.assertIn('python_ontology_load',child.values)
        self.assertIn('python_query_and_result_conversion',child.values)
        self.assertEqual(child.stack,[])


if __name__=='__main__':unittest.main()
