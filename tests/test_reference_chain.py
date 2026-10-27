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
from design.reference import REF, reference_to_rdf, reproduce_reference

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
            for name in ("reference_chain.ttl", "reference_report.json", "reference_chain.md"):
                self.assertTrue((Path(folder) / name).is_file())
            self.assertEqual(subprocess.run(command, cwd=ROOT, capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
