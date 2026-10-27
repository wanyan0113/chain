"""Algorithmic checks with explicitly fictional technical inputs."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from rdflib import Graph, RDF

from check_ontology import analyze_graph
from design.engine import DesignError, SearchLimitError, condition_result, design
from design.rdf import KW, design_to_rdf

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "design/examples/technical_demo.json"


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(DEMO.read_text(encoding="utf-8"))

    def single(self):
        self.doc["tasks"] = self.doc["tasks"][:1]
        self.doc["contexts"] = self.doc["contexts"][:3]

    def test_six_steps_metrics_and_global_resource_choice(self):
        original = deepcopy(self.doc)
        result = design(self.doc)
        self.assertEqual(self.doc, original)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["network_search"]["evaluated"], 4)
        self.assertEqual(result["network_search"]["feasible"], 3)
        chosen = [c for t in result["tasks"] for c in t["chains"] if c["id"] in result["network"]["chains"]]
        self.assertEqual({c["artifacts"]["manifestation"] for c in chosen}, {"demo-fast", "demo-slow"})
        fast = result["tasks"][0]["chains"][0]
        self.assertEqual(fast["metrics"]["time"], 3)
        self.assertAlmostEqual(fast["metrics"]["accuracy"], .99 * .95 * .9)
        self.assertEqual(fast["metrics"]["cost"], 3)
        self.assertEqual(fast["closed_path"][0], fast["closed_path"][-1])
        self.assertEqual(fast["score"], 1)
        self.assertAlmostEqual(result["network"]["score"], .5)
        self.assertIn("parallel", {r["kind"] for r in result["network"]["relations"]})

    def test_corrected_near_rule(self):
        result = design(self.doc)
        self.assertEqual(result["tasks"][0]["required_capabilities"]["manifestation"], ["near-manifestation"])
        self.doc["tasks"][0]["attributes"]["distance_class"] = "far"
        self.assertEqual(design(self.doc)["status"], "blocked")

    def test_unknown_and_unit_mismatch_do_not_pass(self):
        for change in ("missing", "unit", "null"):
            with self.subTest(change=change):
                doc = deepcopy(self.doc)
                context = doc["contexts"][0]["attributes"]
                if change == "missing":
                    context.clear()
                elif change == "unit":
                    context["target_distance"]["unit"] = "km"
                else:
                    context["target_distance"] = None
                result = design(doc)
                self.assertEqual(result["status"], "blocked")
                self.assertIsNone(result["network"])

    def test_strict_range_target_and_control(self):
        self.single()
        self.doc["links"][0]["distance"]["value"] = 20
        self.assertEqual(design(self.doc)["status"], "no_feasible_design")
        self.doc["links"][0]["distance"]["value"] = 2
        for link in self.doc["links"][1:]:
            link["controls"] = False
        self.assertEqual(design(self.doc)["status"], "no_feasible_design")
        self.doc["artifacts"][0]["attributes"]["target_types"] = ["其他目标"]
        self.assertFalse(any(s["accepted"] for s in design(self.doc)["tasks"][0]["selection"] if s["artifact"] == "demo-eye"))

    def test_missing_links_and_blocking_issues(self):
        for collection in ("tasks", "artifacts", "task_rules", "artifact_rules", "links", "contexts"):
            doc = deepcopy(self.doc)
            doc[collection][0]["blocking_issue_ids"] = ["unresolved-1"]
            self.assertEqual(design(doc)["status"], "blocked", collection)
        self.doc["links"].clear()
        self.assertEqual(design(self.doc)["status"], "blocked")

    def test_missing_metrics_blocks_ranking_entire_comparison_set(self):
        self.doc["artifacts"][-1]["metrics"] = None
        result = design(self.doc)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(all(c["score"] is None for c in result["tasks"][0]["chains"]))

    def test_equal_metrics_explicit_policy(self):
        self.single()
        self.doc["artifacts"][-1]["available"] = False
        result = design(self.doc)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["tasks"][0]["chains"][0]["score"], .5)

    def test_precedence_endpoints_and_cooldown(self):
        self.doc["artifacts"][-1]["available"] = False
        fast = self.doc["artifacts"][2]
        fast["consumable"] = False
        self.doc["tasks"][1]["start_s"] = 3
        self.doc["dependencies"] = [{"before": "demo-task-1", "after": "demo-task-2", "gap_s": 0}]
        result = design(self.doc)
        self.assertEqual(result["status"], "complete")
        self.assertIn("serial", {r["kind"] for r in result["network"]["relations"]})
        fast["cooldown_s"] = 1
        self.assertEqual(design(self.doc)["status"], "no_feasible_design")
        fast["cooldown_s"] = 0
        self.doc["dependencies"][0]["gap_s"] = 1
        self.assertEqual(design(self.doc)["status"], "no_feasible_design")

    def test_inventory_across_nonoverlapping_tasks(self):
        self.doc["artifacts"][-1]["available"] = False
        self.doc["tasks"][1]["start_s"] = 100
        result = design(self.doc)
        self.assertEqual(result["status"], "no_feasible_design")
        self.assertIn("INVENTORY", {r["code"] for r in result["network_search"]["rejections"]})

    def test_global_shared_group_capacity(self):
        self.doc["resource_groups"] = [{"id": "shared-platform", "capacity": 1, "artifacts": ["demo-fast", "demo-slow"]}]
        result = design(self.doc)
        self.assertEqual(result["status"], "no_feasible_design")
        self.assertIn("GROUP_CAPACITY", {r["code"] for r in result["network_search"]["rejections"]})

    def test_weights_change_preference(self):
        self.single()
        self.doc["artifacts"][-1]["metrics"]["accuracy"] = 1
        self.doc["weights"] = {"time": 0, "accuracy": 1, "cost": 0}
        self.assertEqual(design(self.doc)["tasks"][0]["chains"][0]["artifacts"]["manifestation"], "demo-slow")
        self.doc["weights"] = {"time": 1, "accuracy": 0, "cost": 0}
        self.assertEqual(design(self.doc)["tasks"][0]["chains"][0]["artifacts"]["manifestation"], "demo-fast")

    def test_search_limits_are_never_silent_truncation(self):
        for key in ("max_chain_combinations", "max_network_combinations"):
            doc = deepcopy(self.doc)
            doc["policies"][key] = 1
            with self.assertRaises(SearchLimitError):
                design(doc)

    def test_invalid_input_rejected(self):
        variants = []
        for key, value in (("capacity", 0), ("inventory", -1), ("cooldown_s", float("inf"))):
            doc = deepcopy(self.doc)
            doc["artifacts"][0][key] = value
            variants.append(doc)
        doc = deepcopy(self.doc)
        doc["weights"]["time"] = .9
        variants.append(doc)
        doc = deepcopy(self.doc)
        doc["links"][0]["to"] = "missing"
        variants.append(doc)
        doc = deepcopy(self.doc)
        doc["dependencies"] = [{"before": "demo-task-1", "after": "demo-task-2", "gap_s": 0}, {"before": "demo-task-2", "after": "demo-task-1", "gap_s": 0}]
        variants.append(doc)
        doc = deepcopy(self.doc)
        doc["artifacts"][0]["metrics"]["cost"]["unit"] = "other"
        variants.append(doc)
        for doc in variants:
            with self.subTest(doc=doc):
                with self.assertRaises(DesignError):
                    design(doc)

    def test_conjunction_and_alternative_rules(self):
        self.single()
        self.doc["artifact_rules"][-1]["when"].append({"field": "artifact.max_range", "op": "lt", "value": {"value": 0, "unit": "nmi"}})
        self.assertEqual(design(self.doc)["status"], "no_feasible_design")
        alternative = deepcopy(self.doc["artifact_rules"][-1])
        alternative["id"] = "alternative"
        alternative["when"].pop()
        self.doc["artifact_rules"].append(alternative)
        self.assertEqual(design(self.doc)["status"], "complete")

    def test_rdf_matches_core_and_chain_nodes_are_unique(self):
        report = design(self.doc)
        graph = design_to_rdf(self.doc, report)
        combined = Graph().parse(ROOT / "arcane_ontology.ttl", format="turtle") + graph
        review = analyze_graph(combined)
        self.assertEqual(review.counts()["ERROR"], 0, review.items)
        self.assertEqual(review.counts()["WARN"], 0, review.items)
        self.assertEqual(len(set(graph.subjects(RDF.type, KW.SpellChain))), 2)
        self.assertEqual(len(set(graph.objects(None, KW.hasChainNode))), 6)

    def test_branch_dependencies_and_export(self):
        self.doc["artifacts"][2]["available"] = False
        self.doc["tasks"][1]["start_s"] = 5
        third = deepcopy(self.doc["tasks"][1])
        third.update(id="demo-task-3", target="demo-target-3")
        self.doc["tasks"].append(third)
        for context in deepcopy(self.doc["contexts"][3:]):
            context["task"] = "demo-task-3"
            self.doc["contexts"].append(context)
        self.doc["dependencies"] = [{"before": "demo-task-1", "after": task, "gap_s": 0} for task in ("demo-task-2", "demo-task-3")]
        result = design(self.doc)
        self.assertEqual(result["status"], "complete")
        branch = [r for r in result["network"]["relations"] if r["kind"] == "branch"]
        self.assertEqual(len(branch), 1)
        self.assertEqual(len(branch[0]["to"]), 2)
        combined = Graph().parse(ROOT / "arcane_ontology.ttl", format="turtle") + design_to_rdf(self.doc, result)
        review = analyze_graph(combined)
        self.assertEqual(review.counts()["ERROR"], 0, review.items)
        self.assertEqual(review.counts()["WARN"], 0, review.items)

    def test_distinct_inputs_have_distinct_exported_entities(self):
        first = design_to_rdf(self.doc, design(self.doc))
        self.doc["tasks"][0]["start_s"] = 20
        second = design_to_rdf(self.doc, design(self.doc))
        self.assertFalse(set(first.subjects(RDF.type, KW.SpellChain)) & set(second.subjects(RDF.type, KW.SpellChain)))

    def test_cli_and_real_case_audit(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            output = Path(temporary) / "output"
            command = [sys.executable, "-B", "-m", "design", "run", str(DEMO), "--output-dir", str(output)]
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((output / "design.ttl").exists())
            self.assertEqual(json.loads(completed.stdout)["status"], "complete")
            self.assertEqual(subprocess.run(command, cwd=ROOT, capture_output=True).returncode, 2)
            blocked = deepcopy(self.doc)
            blocked["artifacts"][0]["metrics"] = None
            input_path = Path(temporary) / "blocked.json"
            input_path.write_text(json.dumps(blocked), encoding="utf-8")
            overwrite = [sys.executable, "-B", "-m", "design", "run", str(input_path), "--output-dir", str(output), "--overwrite"]
            self.assertEqual(subprocess.run(overwrite, cwd=ROOT, capture_output=True).returncode, 2)
            audit_output = Path(temporary) / "audit"
            audited = subprocess.run([sys.executable, "-B", "-m", "design", "audit-case", "--output-dir", str(audit_output)], cwd=ROOT, capture_output=True)
            self.assertEqual(audited.returncode, 1, audited.stderr)
            self.assertEqual(json.loads((audit_output / "case_readiness.json").read_text(encoding="utf-8"))["status"], "blocked")
        audit = subprocess.run([sys.executable, "-B", "-m", "design", "audit-case"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(audit.returncode, 1, audit.stderr)
        result = json.loads(audit.stdout)
        self.assertEqual(len(result["issues"]), 13)
        self.assertEqual(len(result["gaps"]), 3)
        self.assertIsNone(result["network"])


if __name__ == "__main__":
    unittest.main()
