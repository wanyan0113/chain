"""Source fidelity and real HermiT/table10 integration regression coverage."""
import importlib.util
import json
from pathlib import Path
import shutil
import unittest

from experiments.article_pair.__main__ import build_cases, differences, AP, COORD
from design.engine import design, validate_document
from design.rdf import design_to_rdf
from design.knowledge import ontology_for_document
from check_ontology import analyze_graph

ROOT = Path(__file__).resolve().parents[1]


class ArticlePairDataTests(unittest.TestCase):
    def test_source_values_and_anomalies_are_preserved_in_both_cases(self):
        source, original, supplemented = build_cases()
        for document in (original, supplemented):
            validate_document(document)
            self.assertEqual(len(document['artifacts']), 11)
            artifacts = {a['id']: a for a in document['artifacts']}
            for row in source['table9']:
                attrs = artifacts[row['manifestation_artifact']]['attributes']
                self.assertEqual(attrs['min_range']['value'], row['min_range_nm'])
                self.assertEqual(attrs['max_range']['value'], row['max_range_nm'])
                self.assertEqual(attrs['resonance']['value'], row['resonance_value'])
                self.assertEqual(attrs['max_activation_altitude']['value'], row['max_activation_altitude_ft'])
            self.assertEqual(document['tasks'][0]['attributes']['target_altitude']['value'], 36000)
        self.assertTrue(all(a['metrics'] is None and a['blocking_issue_ids'] for a in original['artifacts']))
        self.assertFalse(original['links'])
        self.assertTrue(all('target_distance' not in c['attributes'] for c in original['contexts']))
        self.assertFalse(any(a['blocking_issue_ids'] for a in supplemented['artifacts']))
        self.assertTrue(differences(original, supplemented))

    def test_external_rules_preserve_every_table10_screening_condition(self):
        catalog = json.loads((ROOT / 'data/article_design/table10_screening.json').read_text(encoding='utf-8'))
        rules = json.loads((ROOT / 'knowledge/article-pair.rules.json').read_text(encoding='utf-8'))
        for group in catalog['rules']:
            actual = next(r for r in rules['artifact_rules'] if r['role'] == group['role'])
            for condition in group['conditions']:
                self.assertIn({k: v for k, v in condition.items() if k in ('field', 'op', 'value', 'ref')}, actual['when'])


@unittest.skipUnless(importlib.util.find_spec('owlready2') and shutil.which('java'), 'HermiT and Java required')
class ArticlePairReasoningTests(unittest.TestCase):
    def test_actual_owl_relations_and_table10_can_design_and_export_together(self):
        _, _, doc = build_cases()
        report = design(doc)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(len(report['tasks'][0]['chains']), 8)
        selected = [c for c in report['tasks'][0]['chains'] if c['id'] in report['network']['chains']][0]
        self.assertEqual(selected['artifacts'], {'insight': 'R-Eye-A', 'coordination': COORD, 'manifestation': 'R-Sigil-B'})
        self.assertEqual(report['article_table10']['selected']['status'], 'pass')
        self.assertIn(AP + 'FlyingEliminationMission', report['tasks'][0]['owl_evidence']['types'])
        # This previously failed when ontology_evidence leaked into the
        # standalone cooperation checker's strict link schema.
        self.assertTrue(any('ontology_evidence' in e for e in selected['link_evidence']))
        graph = design_to_rdf(doc, report, require_canonical=True)
        counts = analyze_graph(ontology_for_document(doc) + graph).counts()
        self.assertEqual((counts['ERROR'], counts['WARN']), (0, 0))
