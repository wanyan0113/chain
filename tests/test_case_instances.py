"""Instance mapping, source traceability and rejection of inconsistent input."""
from copy import deepcopy
from decimal import Decimal, localcontext
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
        self.assertEqual(report['counts']['entities'], 32)
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
        self.assertEqual(len(list(graph.triples((None, KW.hasCapability, None)))), 10)
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
                    self.document['units']['distance'] = 'unsupported-unit'
                else:
                    del self.document['table9'][0]['max_range_nm']
                with self.assertRaises(ValidationError):
                    map_case_instances(self.bundle())
                self.document = original

    def test_probability_conversion_and_disjoint_coverage(self):
        graph, report = map_case_instances(self.bundle())
        resource = next(e for e in report['entities'] if e['code'] == 'R-Sigil-A')
        self.assertEqual(float(graph.value(URIRef(resource['iri']), KW.attunementProbability)), 0.95)
        counts = report['counts']
        self.assertEqual(counts['identity_values'] + counts['property_values'] + counts['unmapped_values'], 139)
        self.assertEqual(counts['unmapped_values'], 21)
        self.assertFalse(any(v['path'].endswith('.id') for v in report['unmapped_values']))
        conversion = next(c for c in report['claims'] if c['property'] == str(KW.attunementProbability))
        self.assertEqual(conversion['conversion']['input_unit'], '%')
        self.document['table9'][0]['attunement_probability_percent'] = 200
        with self.assertRaisesRegex(ValidationError, '概率'):
            map_case_instances(self.bundle())

    def test_direct_checks_include_unmapped_fields(self):
        for key, value in [('resonance_value', -1), ('attunement_probability_percent', True),
                           ('max_range_nm', None)]:
            with self.subTest(key=key):
                self.document['table9'][0][key] = value
                with self.assertRaises(ValidationError):
                    map_case_instances(self.bundle())
                self.setUp()

    def test_unit_conversion_and_raw_snapshot_preserved(self):
        self.document['units']['distance'] = 'km'
        self.document['table8'][0]['detection_range_nm'] = 1.852
        self.document['units']['altitude'] = 'm'
        self.document['table8'][0]['altitude_ft'] = 304.8
        for row in self.document['table9']:
            row['attunement_probability_percent'] /= 100
        self.document['units']['attunement_probability'] = '1'
        bundle = self.bundle()
        graph, report = map_case_instances(bundle)
        resource = next(e for e in report['entities'] if e['code'] == 'R-Eye-A')
        platform = next(e for e in report['entities'] if e['code'] == 'R-Golem' and e['kind'] == 'platform')
        self.assertAlmostEqual(float(graph.value(URIRef(resource['iri']), KW.detectionRangeNm)), 1)
        self.assertAlmostEqual(float(graph.value(URIRef(platform['iri']), KW.altitudeFt)), 1000)
        self.assertEqual(bundle_from_graph(graph), bundle)
        self.assertEqual(bundle['metadata']['raw_document']['units']['distance'], 'km')

    def test_cleaning_is_derived_and_conflicts_are_flagged(self):
        self.document['table8'][0]['insight_artifact'] = ' R-Eye-A '
        self.document['table8'][0]['target_types'] = ['空中', '空中', ' 海面 ']
        bundle = self.bundle()
        original = deepcopy(bundle)
        graph, report = map_case_instances(bundle)
        self.assertEqual(bundle, original)
        self.assertTrue(any(e['code'] == 'R-Eye-A' for e in report['entities']))
        self.assertEqual(len(report['cleaning']['changes']), 2)
        self.assertTrue(any(f['code'] == 'BOUND_ORDER' for f in report['cleaning']['findings']))
        self.assertTrue(any(f['code'] == 'COORDINATE_DIRECTION' for f in report['cleaning']['findings']))
        self.document['table9'][0]['manifestation_artifact'] = ' R-Eye-A '
        with self.assertRaisesRegex(ValidationError, '重复'):
            map_case_instances(self.bundle())

    def test_explicit_task_relations_require_registered_sources(self):
        # Technical binding fixture, not a claim about the article's true target.
        bindings = {'format_version': 1, 'tasks': [{'record_path': 'table11',
            'target': {'id': 'technical-target', 'source_keys': ['case_text']},
            'capabilities': [{'id': 'technical-capability', 'type': 'InsightCapability', 'source_keys': ['table11']}]}]}
        graph, report = map_case_instances(self.bundle(), bindings=bindings)
        self.assertEqual(len(list(graph.triples((None, KW.hasTarget, None)))), 1)
        self.assertEqual(len(list(graph.triples((None, KW.requiresCapability, None)))), 1)
        self.assertEqual(report['task_bindings'][0]['target'], 'mapped')
        bindings['tasks'][0]['target']['source_keys'] = ['not-a-material']
        with self.assertRaisesRegex(ValidationError, 'source_keys'):
            map_case_instances(self.bundle(), bindings=bindings)

    def test_coordinate_syntax_and_bounds_rejected(self):
        for value in ['garbage', 'N91°00′00″', 'E181°00′00″',
                      'N70°60′00″', 'N70°00′60″', 'N90°00′01″', 'N-70', 'NaN']:
            with self.subTest(value=value):
                self.setUp()
                self.document['table8'][0]['longitude_column_raw'] = value
                with self.assertRaisesRegex(ValidationError, '坐标'):
                    map_case_instances(self.bundle())

    def test_valid_coordinates_preserve_header_conflict(self):
        for longitude, latitude in [('180', '-90'), ('E180°00′00″', 'N90°00′00″'),
                                    ('N70°58′08″', 'E40°25′06″')]:
            with self.subTest(longitude=longitude):
                self.setUp()
                self.document['table8'][0]['longitude_column_raw'] = longitude
                self.document['table8'][0]['latitude_column_raw'] = latitude
                bundle = self.bundle()
                _, report = map_case_instances(bundle)
                self.assertEqual(bundle['metadata']['raw_document']['table8'][0]['longitude_column_raw'], longitude)
                row_findings = [f for f in report['cleaning']['findings']
                                if 'table8[0].longitude_column_raw' in f['paths']]
                self.assertEqual(bool(row_findings), longitude.startswith('N'))

    def test_large_number_mapping_preserves_digits_under_small_context(self):
        value = 123456789012345678901234567890
        self.document['table8'][0]['altitude_ft'] = value
        self.document['table9'][0]['attunement_probability_percent'] = 95
        with localcontext() as context:
            context.prec = 6
            graph, report = map_case_instances(self.bundle())
        platform = next(e for e in report['entities'] if e['code'] == 'R-Golem' and e['kind'] == 'platform')
        self.assertEqual(graph.value(URIRef(platform['iri']), KW.altitudeFt).toPython(), Decimal(value))
        resource = next(e for e in report['entities'] if e['code'] == 'R-Sigil-A')
        self.assertEqual(graph.value(URIRef(resource['iri']), KW.attunementProbability).toPython(), Decimal('0.95'))


if __name__ == '__main__':
    unittest.main()
