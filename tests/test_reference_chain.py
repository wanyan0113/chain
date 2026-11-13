"""Article records retain source values without deriving execution metrics."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph, RDF

from check_ontology import analyze_graph
from design.rdf import KW
from design.reference import REF, REFERENCE_PATH, reference_to_rdf, load_reference, reference_view_html, reference_summary

ROOT = Path(__file__).resolve().parent.parent


class ReferenceChainTests(unittest.TestCase):
    def test_source_values_and_stated_chain_without_computed_metrics(self):
        source = json.loads(REFERENCE_PATH.read_text(encoding='utf-8'))
        result = load_reference()
        self.assertEqual(result["status"], "reference_recorded")
        self.assertEqual([row['raw'] for row in result['evaluation']], source['rows'])
        for row in result['evaluation'] + result['reported_topology'] + [result['chain']]:
            self.assertIsNone(row['metrics'])
            self.assertIsNone(row['score'])
            self.assertNotIn('normalized', row)
            self.assertNotIn('matches_reported_rounding', row)
        self.assertEqual(result['chain']['reported_score'], .896)
        self.assertEqual(result["chain"]["artifacts"]["manifestation"]["id"], "R-Sigil-B")
        self.assertEqual(result["validation"]["independent_feasibility"], "not_verified")
        self.assertEqual(result['validation']['score_reproduction'], 'not_performed')
        self.assertTrue(all("artifacts" not in row for row in result["evaluation"]))

    def test_zero_speed_and_changed_scores_do_not_compute_or_select_a_winner(self):
        source = json.loads(REFERENCE_PATH.read_text(encoding='utf-8'))
        source['rows'][0].update(speed=0, reported_score=0.)
        source['rows'][1]['reported_score'] = 1.
        with patch('design.reference.read_json', return_value=source), patch('design.engine.rank_chains', side_effect=AssertionError('must not rank source records')):
            result = load_reference()
        self.assertEqual(result['chain']['id'], source['explicit_best_chain']['evaluation_row'])
        self.assertEqual(result['chain']['reported_score'], 0.)
        self.assertIsNone(result['chain']['metrics'])
        self.assertIn('未计算指标或进行最优选择', reference_summary(result))

    def test_reported_second_chain_has_sources_but_no_fabricated_metrics(self):
        result=load_reference()
        self.assertEqual(len(result['reported_topology']),2)
        second=result['reported_topology'][1]
        self.assertEqual(second['target'],'B-Orb-A')
        self.assertEqual(second['artifacts']['manifestation'],{'id':'R-Sigil-E','platform':'R-Rune'})
        self.assertIsNone(second['metrics'])
        self.assertIsNone(second['score'])
        self.assertTrue(second['source_refs'])
        self.assertEqual(result['validation']['independent_feasibility'],'not_verified')

    def test_offline_view_embeds_result_safely_and_has_no_external_dependencies(self):
        result=load_reference()
        result['scope']='</script><img src=x onerror=alert(1)>'
        html=reference_view_html(result)
        self.assertNotIn(result['scope'],html)
        self.assertIn('\\u003c/script>',html)
        self.assertNotIn('/* REFERENCE_RESULT */',html)
        self.assertNotIn('/* SHARED_GRAPH_MODULE */',html)
        self.assertIn('export function buildReferenceGraph',html)
        self.assertNotIn('<script src=',html)
        self.assertIn('表13原始记录', html)
        self.assertNotIn('row.metrics.time', html)
        self.assertNotIn('评分已复算', html)

    def test_reference_topology_does_not_fabricate_communication(self):
        result = load_reference()
        graph = reference_to_rdf(result)
        self.assertEqual(len(set(graph.subjects(RDF.type, KW.SpellChain))), 1)
        self.assertEqual(len(set(graph.objects(None, KW.hasChainNode))), 3)
        for prop in (KW.communicatesWith, KW.insightToCoordinationCommunication,
                     KW.coordinationToManifestationCommunication, KW.orchestrates):
            self.assertFalse(list(graph.triples((None, prop, None))))
        self.assertTrue(list(graph.objects(None, REF.validationStatus)))
        report = analyze_graph(Graph().parse(ROOT / "arcane_ontology.ttl", format="turtle") + graph)
        self.assertEqual(report.counts()["ERROR"], 0, report.items)
        self.assertEqual(report.counts()["WARN"], 0, report.items)

    def test_cli_outputs_and_refuses_overwrite(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as folder:
            command = [sys.executable, "-B", "-m", "design", "reference-case", "--output-dir", folder]
            run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["status"], "reference_recorded")
            for name in ("reference_chain.ttl", "reference_report.json", "reference_chain.md", "reference_view.html"):
                self.assertTrue((Path(folder) / name).is_file())
            self.assertEqual(subprocess.run(command, cwd=ROOT, capture_output=True).returncode, 2)
            command[4] = 'reproduce-case'  # Compatibility alias records data too.
            command.append('--overwrite')
            legacy = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            self.assertEqual(json.loads(legacy.stdout)['validation']['score_reproduction'], 'not_performed')


if __name__ == "__main__":
    unittest.main()
