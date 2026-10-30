"""Instance mapping, source traceability and rejection of inconsistent input."""
from copy import deepcopy
from pathlib import Path
import unittest

from rdflib import Graph, Literal, RDF, URIRef
from rdflib.namespace import XSD

from adapters.case_instances import KW, META, PROV, map_case_instances
from source_data.common import ValidationError, read_json, source_iri
from source_data.ingest import build_bundle
from source_data.load import bundle_from_graph

ROOT = Path(__file__).resolve().parents[1]


class CaseInstanceTests(unittest.TestCase):
    def setUp(self):
        self.document = read_json(ROOT / 'data/article_source/case_records.json')
        self.mapping = read_json(ROOT / 'source_mappings/case_fields.json')

    def bundle(self):
        return build_bundle(self.document, self.mapping, input_filename='case.json', input_sha256='0' * 64)

    def test_instances_roundtrip_and_source_field_trace(self):
        bundle = self.bundle()
        snapshot = deepcopy(bundle)
        graph, report = map_case_instances(bundle)
        graph = Graph().parse(data=graph.serialize(format='turtle'), format='turtle')
        self.assertEqual(bundle_from_graph(graph), bundle)
        self.assertEqual(bundle, snapshot)
        self.assertEqual(report['counts']['entities'], 22)
        self.assertEqual(report['ontology_check']['ERROR'], 0)
        self.assertEqual(report['ontology_check']['WARN'], 0)
        eye = next(e for e in report['entities'] if e['code'] == 'R-Eye-A')
        node = URIRef(eye['iri'])
        self.assertIn((node, RDF.type, KW.InsightArtifact), graph)
        self.assertEqual(float(graph.value(node, KW.detectionRangeNm)), 350)
        record_id = eye['source_records'][0]
        self.assertIn((node, PROV.wasDerivedFrom, URIRef(source_iri(record_id))), graph)
        statement = next(s for s in graph.subjects(RDF.subject, node)
                         if graph.value(s, RDF.predicate) == KW.detectionRangeNm)
        source_value = graph.value(statement, PROV.wasDerivedFrom)
        self.assertEqual(source_value, URIRef(source_iri(record_id.replace('/record/', '/value/') + '.detection_range_nm')))
        platform = next(e for e in report['entities'] if e['code'] == 'R-Golem' and e['kind'] == 'platform')
        self.assertIn((URIRef(platform['iri']), KW.hosts, node), graph)

    def test_collections_unknown_fields_and_registered_issues_preserved(self):
        graph, report = map_case_instances(self.bundle())
        self.assertEqual(len(list(graph.subjects(RDF.type, META.ObjectCollection))), 7)
        self.assertFalse(list(graph.triples((None, KW.latitudeDeg, None))))
        self.assertFalse(list(graph.triples((None, KW.detectionPrecision, None))))
        self.assertFalse(list(graph.triples((None, KW.hasTarget, None))))
        self.assertFalse(list(graph.triples((None, KW.hasCapability, None))))
        resource = next(e for e in report['entities'] if e['code'] == 'R-Sigil-D')
        node = URIRef(resource['iri'])
        self.assertEqual(float(graph.value(node, KW.minRangeNm)), 3)
        self.assertEqual(float(graph.value(node, KW.maxRangeNm)), 0.2)
        self.assertTrue(list(graph.objects(node, META.registeredIssue)))
        self.assertTrue(any(v['path'].endswith('.precision_raw') for v in report['unmapped_values']))

    def test_input_change_updates_instance_and_namespace(self):
        _, old = map_case_instances(self.bundle())
        self.document['table9'][0]['max_range_nm'] = 12
        graph, changed = map_case_instances(self.bundle())
        resource = next(e for e in changed['entities'] if e['code'] == 'R-Sigil-A')
        self.assertEqual(float(graph.value(URIRef(resource['iri']), KW.maxRangeNm)), 12)
        self.assertNotEqual(old['entities'][0]['iri'], changed['entities'][0]['iri'])

    def test_missing_platform_duplicate_resource_and_invalid_type_rejected(self):
        for change in ('platform', 'duplicate', 'number', 'unit', 'required'):
            with self.subTest(change=change):
                original = deepcopy(self.document)
                if change == 'platform':
                    self.document['table9'][0]['platform'] = 'not-declared'
                elif change == 'duplicate':
                    self.document['table9'][0]['manifestation_artifact'] = 'R-Eye-A'
                elif change == 'number':
                    self.document['table9'][0]['max_range_nm'] = '12'
                elif change == 'unit':
                    self.document['units']['distance'] = 'km'
                else:
                    del self.document['table9'][0]['max_range_nm']
                with self.assertRaises(ValidationError):
                    map_case_instances(self.bundle())
                self.document = original


if __name__ == '__main__':
    unittest.main()
