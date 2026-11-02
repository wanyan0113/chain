"""Article-score reproduction stays distinct from independent design validation."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from rdflib import Graph, RDF

from check_ontology import analyze_graph
from design.rdf import KW
from design.reference import REF, reference_to_rdf, reproduce_reference, reference_view_html

ROOT = Path(__file__).resolve().parent.parent


class ReferenceChainTests(unittest.TestCase):
    def test_scores_units_and_explicit_winner(self):
        result = reproduce_reference()
        self.assertEqual(result["status"], "reference_reproduced")
        scores = {row["id"]: row["score"] for row in result["evaluation"]}
        for identifier, expected in (("1", .896153846), ("2", .3), ("3", .7), ("4", .092307692)):
            self.assertAlmostEqual(scores[identifier], expected)
        self.assertAlmostEqual(result["chain"]["metrics"]["time"], 198.260869565)
        self.assertAlmostEqual(result["chain"]["metrics"]["accuracy"], .765)
        self.assertEqual(result["chain"]["artifacts"]["manifestation"]["id"], "R-Sigil-B")
        self.assertEqual(result["validation"]["independent_feasibility"], "not_verified")
        self.assertTrue(all("artifacts" not in row for row in result["evaluation"]))

    def test_reported_second_chain_has_sources_but_no_fabricated_metrics(self):
        result=reproduce_reference()
        self.assertEqual(len(result['reported_topology']),2)
        second=result['reported_topology'][1]
        self.assertEqual(second['target'],'B-Orb-A')
        self.assertEqual(second['artifacts']['manifestation'],{'id':'R-Sigil-E','platform':'R-Rune'})
        self.assertIsNone(second['metrics'])
        self.assertIsNone(second['score'])
        self.assertTrue(second['source_refs'])
        self.assertEqual(result['validation']['independent_feasibility'],'not_verified')

    def test_offline_view_embeds_result_safely_and_has_no_external_dependencies(self):
        result=reproduce_reference()
        result['scope']='</script><img src=x onerror=alert(1)>'
        html=reference_view_html(result)
        self.assertNotIn(result['scope'],html)
        self.assertIn('\\u003c/script>',html)
        self.assertNotIn('/* REFERENCE_RESULT */',html)
        self.assertNotIn('/* SHARED_GRAPH_MODULE */',html)
        self.assertIn('export function buildReferenceGraph',html)
        self.assertNotIn('<script src=',html)

    def test_reference_topology_does_not_fabricate_communication(self):
        result = reproduce_reference()
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
            command = [sys.executable, "-B", "-m", "design", "reproduce-case", "--output-dir", folder]
            run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["status"], "reference_reproduced")
            for name in ("reference_chain.ttl", "reference_report.json", "reference_chain.md", "reference_view.html"):
                self.assertTrue((Path(folder) / name).is_file())
            self.assertEqual(subprocess.run(command, cwd=ROOT, capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
