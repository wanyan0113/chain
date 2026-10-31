"""Canonical identities through task, artifact selection, chains and network."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph, RDF, Literal

from check_ontology import analyze_graph
from design import design
from design.engine import DesignError
from design.rdf import DESIGN, KW, design_to_rdf

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'design/examples/canonical_network_demo.json'


class DesignIdentityTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(DEMO.read_text(encoding='utf-8'))

    def test_complete_network_reuses_capabilities_for_missions_and_artifacts(self):
        original = deepcopy(self.doc)
        report = design(self.doc)
        self.assertEqual(self.doc, original)
        self.assertEqual(report['status'], 'complete')
        self.assertTrue(report['canonical_export_ready'])
        self.assertEqual(report['ontology_mapping'], {'status': 'complete', 'total': 6, 'mapped': 6, 'unmapped': 0})
        self.assertEqual([len(t['chains']) for t in report['tasks']], [2, 2])
        self.assertEqual(report['network_search']['evaluated'], 4)
        self.assertEqual(report['network_search']['feasible'], 3)
        graph = design_to_rdf(self.doc, report, require_canonical=True)
        for capability, artifacts in [(KW.AirInsightCapability, 1), (KW.RapidResponseCoordinationCapability, 1),
                                      (KW.ShortRangeManifestationCapability, 2)]:
            self.assertEqual(len(set(graph.subjects(KW.requiresCapability, capability))), 2)
            self.assertEqual(len(set(graph.subjects(KW.hasCapability, capability))), artifacts)
        self.assertEqual(len(set(graph.subjects(RDF.type, KW.ArcaneWeb))), 1)
        self.assertEqual(len(set(graph.subjects(RDF.type, KW.SpellChain))), 2)
        self.assertEqual(len(set(graph.objects(None, KW.hasChainNode))), 6)
        evidence = list(graph.subjects(DESIGN.identityStatus, Literal('mapped')))
        self.assertEqual(len(evidence), 6)
        for item in evidence:
            self.assertIsNotNone(graph.value(item, DESIGN.forMission))
            self.assertIsNotNone(graph.value(item, DESIGN.forChain))
            self.assertIsNotNone(graph.value(item, DESIGN.forArtifact))
            payload = json.loads(str(graph.value(item, DESIGN.ruleEvidencePayload)))
            self.assertTrue(payload['task_rules'])
            self.assertTrue(payload['artifact_rules'])
        counts = analyze_graph(Graph().parse(ROOT / 'arcane_ontology.ttl') + graph).counts()
        self.assertEqual((counts['ERROR'], counts['WARN']), (0, 0))

    def test_unknown_capability_preserves_design_but_blocks_strict_export(self):
        self.rename_capability('空中灵视探知能力', '自定义探知能力')
        report = design(self.doc)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['ontology_mapping']['unmapped'], 2)
        self.assertFalse(report['canonical_export_ready'])
        self.assertTrue(len(design_to_rdf(self.doc, report)))
        with self.assertRaisesRegex(DesignError, '未映射能力'):
            design_to_rdf(self.doc, report, require_canonical=True)

    def rename_capability(self, old, new):
        for rule in self.doc['task_rules'] + self.doc['artifact_rules']:
            if rule['capability'] == old:
                rule['capability'] = new

    def test_blocked_or_no_feasible_network_never_ready(self):
        del self.doc['tasks'][0]['attributes']['target_type']
        blocked = design(self.doc)
        self.assertEqual(blocked['status'], 'blocked')
        self.assertFalse(blocked['canonical_export_ready'])
        with self.assertRaises(DesignError):
            design_to_rdf(self.doc, blocked)
        self.setUp()
        self.doc['resource_groups'] = [{'id': 'single-channel', 'capacity': 1,
                                       'artifacts': ['demo-fast', 'demo-slow']}]
        impossible = design(self.doc)
        self.assertEqual(impossible['status'], 'no_feasible_design')
        self.assertEqual(impossible['ontology_mapping']['status'], 'complete')
        self.assertFalse(impossible['canonical_export_ready'])

    def test_mapping_tampering_and_changed_ontology_are_rejected(self):
        report = design(self.doc)
        tampered = deepcopy(report)
        tampered['tasks'][0]['ontology_bindings'][0]['canonical_uri'] = str(KW.GroundInsightCapability)
        with self.assertRaisesRegex(DesignError, '映射'):
            design_to_rdf(self.doc, tampered)
        ontology = Graph().parse(ROOT / 'arcane_ontology.ttl')
        ontology.remove((KW.AirInsightCapability, None, None))
        with patch('design.capability_identity.load_capability_ontology', return_value=ontology):
            changed = design(self.doc)
            self.assertFalse(changed['canonical_export_ready'])
            with self.assertRaisesRegex(DesignError, '映射'):
                design_to_rdf(self.doc, report, require_canonical=True)

    def test_cli_strict_export_and_stale_result_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.json'
            def run(output, *flags):
                source.write_text(json.dumps(self.doc, ensure_ascii=False), encoding='utf-8')
                return subprocess.run([sys.executable, '-B', '-m', 'design', 'run', str(source),
                                       '--output-dir', str(output), '--require-canonical', *flags],
                                      cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            success = root / 'success'
            completed = run(success)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            old = {p.name: p.read_bytes() for p in success.iterdir()}
            summary = (success / 'design_summary.md').read_text(encoding='utf-8')
            self.assertIn('demo-target-1 → demo-eye → demo-coordinator', summary)
            self.assertIn('检查 4 组，可行 3 组', summary)
            self.rename_capability('空中灵视探知能力', '未登记测试能力')
            output = root / 'unmapped'
            completed = run(output)
            self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
            self.assertFalse((output / 'design.ttl').exists())
            result = json.loads((output / 'design_report.json').read_text(encoding='utf-8'))
            self.assertEqual(result['status'], 'complete')
            self.assertFalse(result['canonical_export_ready'])
            self.assertEqual(result, json.loads(completed.stdout))
            self.assertEqual(run(success, '--overwrite').returncode, 2)
            self.assertEqual(old, {p.name: p.read_bytes() for p in success.iterdir()})

    def test_changed_input_cannot_be_exported_with_old_report(self):
        report = design(self.doc)
        for field in ('scope', 'task_source', 'artifact_source', 'context_source', 'rule_source'):
            with self.subTest(field=field):
                changed = deepcopy(self.doc)
                if field == 'scope':
                    changed['scope'] += ' changed'
                else:
                    key = {'task_source': 'tasks', 'artifact_source': 'artifacts',
                           'context_source': 'contexts', 'rule_source': 'task_rules'}[field]
                    changed[key][0]['source_refs'].append('changed-source')
                for strict in (False, True):
                    with self.assertRaises(DesignError):
                        design_to_rdf(changed, report, require_canonical=strict)

    def test_changed_evidence_and_network_report_are_rejected(self):
        report = design(self.doc)
        for field in ('task_evidence', 'artifact_evidence', 'network_score', 'unexpected_metadata'):
            with self.subTest(field=field):
                changed = deepcopy(report)
                if field == 'task_evidence':
                    changed['tasks'][0]['inference'][0]['source_refs'].append('changed-source')
                elif field == 'artifact_evidence':
                    changed['tasks'][0]['selection'][0]['rules'][0]['source_refs'].append('changed-source')
                elif field == 'network_score':
                    changed['network']['score'] = 999
                else:
                    changed['unexpected'] = 'not produced by the engine'
                for strict in (False, True):
                    with self.assertRaises(DesignError):
                        design_to_rdf(self.doc, changed, require_canonical=strict)

    def test_cli_metadata_is_not_serialized_as_reasoning_evidence(self):
        report = design(self.doc)
        annotated = deepcopy(report)
        annotated['input_file_sha256'] = 'not-verifiable-from-the-parsed-document'
        annotated['rdf_validation'] = {'ERROR': 999}
        snapshot = deepcopy(annotated)
        graph = design_to_rdf(self.doc, annotated, require_canonical=True)
        payload = json.loads(str(next(graph.objects(None, DESIGN.reportPayload))))
        self.assertEqual(payload, report)
        self.assertNotIn('input_file_sha256', payload)
        self.assertNotIn('rdf_validation', payload)
        self.assertEqual(annotated, snapshot)

    def test_content_digest_covers_facts_not_used_by_matching_rules(self):
        report = design(self.doc)
        changed = deepcopy(self.doc)
        changed['artifacts'][0]['source_refs'].append('new-source')
        current = design(changed)
        self.assertEqual(report['network'], current['network'])
        self.assertNotEqual(report['input_sha256'], current['input_sha256'])
        graph = design_to_rdf(changed, current, require_canonical=True)
        stored_input = json.loads(str(next(graph.objects(None, DESIGN.inputPayload))))
        stored_report = json.loads(str(next(graph.objects(None, DESIGN.reportPayload))))
        self.assertEqual(stored_input, changed)
        self.assertEqual(stored_report, current)


if __name__ == '__main__':
    unittest.main()
