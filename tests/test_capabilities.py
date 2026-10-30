"""Step-2 inference semantics, evidence and standalone CLI integration."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from rdflib import Graph, RDF

from check_ontology import analyze_graph
from design import infer_capabilities
from design.capabilities import capability_rdf
from design.engine import DesignError, design
from design.rdf import KW

ROOT = Path(__file__).resolve().parents[1]


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((ROOT / "design/examples/capability_demo.json").read_text(encoding="utf-8"))

    def test_conjunction_multiple_capabilities_and_evidence(self):
        original = deepcopy(self.doc)
        report = infer_capabilities(self.doc)
        self.assertEqual(self.doc, original)
        self.assertEqual(report["status"], "complete")
        task = report["tasks"][0]
        self.assertEqual(set(task["required_capabilities"]["manifestation"]), {"对空具现能力", "低空具现能力"})
        self.assertEqual(task["attributes"]["target_altitude"]["value"], 500)
        self.assertEqual(task["inference"][0]["source_refs"], self.doc["task_rules"][0]["source_refs"])
        self.doc["tasks"][0]["attributes"]["target_altitude"]["value"] = 1001
        self.assertEqual(infer_capabilities(self.doc)["tasks"][0]["required_capabilities"]["manifestation"], ["对空具现能力"])

    def test_missing_null_and_mismatched_unit_block_partial_result(self):
        for value in (None, {"value": 500, "unit": "m"}):
            with self.subTest(value=value):
                self.doc["tasks"][0]["attributes"]["target_altitude"] = value
                report = infer_capabilities(self.doc)
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(report["tasks"][0]["inference"][1]["status"], "unknown")
                with self.assertRaises(DesignError):
                    capability_rdf(self.doc, report)
        del self.doc["tasks"][0]["attributes"]["target_altitude"]
        self.assertEqual(infer_capabilities(self.doc)["status"], "blocked")

    def test_failed_conjunction_does_not_become_unknown(self):
        self.doc["tasks"][0]["attributes"] = {"target_type": "地面目标"}
        report = infer_capabilities(self.doc)
        self.assertEqual([r["status"] for r in report["tasks"][0]["inference"]], ["fail", "fail"])
        self.assertEqual(report["tasks"][0]["blockers"], [{"code": "NO_CAPABILITY", "role": "manifestation"}])

    def test_task_and_rule_issues_block_export(self):
        for collection in ("tasks", "task_rules"):
            doc = deepcopy(self.doc)
            doc[collection][0]["blocking_issue_ids"] = ["not-confirmed"]
            report = infer_capabilities(doc)
            self.assertEqual(report["status"], "blocked")
            with self.assertRaises(DesignError):
                capability_rdf(doc, report)

    def test_duplicate_conclusions_keep_both_rule_justifications(self):
        alternative = deepcopy(self.doc["task_rules"][0])
        alternative["id"] = "alternative-rule"
        self.doc["task_rules"].append(alternative)
        task = infer_capabilities(self.doc)["tasks"][0]
        self.assertEqual(len(task["required_capabilities"]["manifestation"]), 2)
        self.assertEqual(len(task["inference"]), 3)

    def test_invalid_contracts_rejected(self):
        for change in ("duplicate", "foreign", "nan", "bare", "roles"):
            with self.subTest(change=change):
                doc = deepcopy(self.doc)
                if change == "duplicate":
                    doc["tasks"].append(deepcopy(doc["tasks"][0]))
                elif change == "foreign":
                    doc["task_rules"][0]["when"][0]["field"] = "artifact.target_type"
                elif change == "nan":
                    doc["tasks"][0]["attributes"]["target_altitude"]["value"] = float("nan")
                elif change == "bare":
                    doc["tasks"][0]["attributes"]["target_altitude"] = 500
                else:
                    doc["required_roles"] = []
                with self.assertRaises(DesignError):
                    infer_capabilities(doc)

    def test_shared_step_matches_full_design(self):
        demo = json.loads((ROOT / "design/examples/technical_demo.json").read_text(encoding="utf-8"))
        partial = {key: demo[key] for key in ("format_version", "scope", "tasks", "task_rules")}
        partial["required_roles"] = ["insight", "coordination", "manifestation"]
        step = infer_capabilities(partial)
        full = design(demo)
        for task, expected in zip(step["tasks"], full["tasks"]):
            self.assertEqual(task["inference"], expected["inference"])
            self.assertEqual(task["required_capabilities"], expected["required_capabilities"])

    def test_reports_are_independent_snapshots_in_both_directions(self):
        for kind in ("standalone", "full"):
            with self.subTest(kind=kind):
                if kind == "standalone":
                    document = deepcopy(self.doc)
                    infer = infer_capabilities
                else:
                    document = json.loads((ROOT / "design/examples/technical_demo.json").read_text(encoding="utf-8"))
                    infer = design
                report = infer(document)
                snapshot = deepcopy(report)
                for rule in document["task_rules"]:
                    rule["when"].append({"field": "task.changed", "op": "eq", "value": "B"})
                    for condition in rule["when"]:
                        if "value" in condition and isinstance(condition["value"], dict):
                            condition["value"]["value"] = 9999
                        elif "value" in condition:
                            condition["value"] = "B"
                    rule["source_refs"].append("changed-source")
                    rule["blocking_issue_ids"].append("changed-issue")
                document["tasks"][0]["source_refs"].append("changed-task-source")
                document["tasks"][0]["blocking_issue_ids"].append("changed-task-issue")
                document["tasks"][0]["attributes"].clear()
                if kind == "full":
                    document["links"][0]["distance"]["value"] = 9999
                    document["artifact_rules"][0]["when"][0]["ref"] = "context.changed"
                self.assertEqual(report, snapshot)
                input_snapshot = deepcopy(document)
                report["tasks"][0]["inference"][0]["source_refs"].append("report-only")
                report["tasks"][0]["inference"][0]["blocking_issue_ids"].append("report-only")
                report["tasks"][0]["inference"][0]["checks"].clear()
                self.assertEqual(document, input_snapshot)

    def test_explicit_article_rule_against_transcribed_expected_conclusion(self):
        fixture = json.loads((ROOT / "tests/fixtures/capability_article_cases.json").read_text(encoding="utf-8"))
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                document = {
                    "format_version": "1.0", "scope": fixture["scope"],
                    "required_roles": ["manifestation"],
                    "tasks": [{"id": case["id"], "attributes": deepcopy(case["attributes"]),
                               "source_refs": [case["basis"]], "blocking_issue_ids": []}],
                    "task_rules": [deepcopy(fixture["rule"])],
                }
                report = infer_capabilities(document)
                task = report["tasks"][0]
                self.assertEqual(task["inference"][0]["status"], case["expected_rule_status"])
                self.assertEqual(task["required_capabilities"]["manifestation"], case["expected_capabilities"])
                self.assertEqual(report["status"], case["expected_status"])

    def test_rdf_ontology_compatibility_and_stale_report(self):
        report = infer_capabilities(self.doc)
        graph = capability_rdf(self.doc, report)
        self.assertEqual(len(list(graph.triples((None, KW.requiresCapability, None)))), 2)
        self.assertEqual(len(list(graph.subjects(RDF.type, KW.ManifestationCapability))), 2)
        counts = analyze_graph(Graph().parse(ROOT / "arcane_ontology.ttl") + graph).counts()
        self.assertEqual((counts["ERROR"], counts["WARN"]), (0, 0))
        report["tasks"][0]["required_capabilities"]["manifestation"].clear()
        with self.assertRaises(DesignError):
            capability_rdf(self.doc, report)

    def test_cli_success_blocked_and_stale_rdf_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            out = root / "success"
            def run(target, overwrite=False):
                source.write_text(json.dumps(self.doc, ensure_ascii=False), encoding="utf-8")
                return subprocess.run([sys.executable, "-B", "-m", "design", "infer-capabilities",
                                       str(source), "--output-dir", str(target),
                                       *(["--overwrite"] if overwrite else [])],
                                      cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            result = run(out)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((out / "capabilities.ttl").exists())
            self.assertIn("对空具现能力", (out / "capability_summary.md").read_text(encoding="utf-8"))
            del self.doc["tasks"][0]["attributes"]["target_altitude"]
            self.assertEqual(run(out, True).returncode, 2)
            self.assertEqual(json.loads((out / "capability_report.json").read_text())["status"], "complete")
            blocked = root / "blocked"
            result = run(blocked)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertFalse((blocked / "capabilities.ttl").exists())
            self.assertEqual(json.loads(result.stdout)["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
