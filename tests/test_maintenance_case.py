"""Verify the unified case's provenance and explanations against its RDF output."""
import contextlib
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from rdflib import Graph, Literal, RDF, URIRef, XSD

from maintenance_demo.collaboration.run_case import main, markdown, run_case
from maintenance_demo.collaboration.run_collaboration import M, load_input


class MaintenanceCaseTests(unittest.TestCase):
    def modified_case(self, modify):
        graph = load_input()
        modify(graph)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "facts.ttl"
            graph.serialize(source, format="turtle")
            return run_case(source)

    def test_unified_case_has_consistent_output_evidence_and_statistics(self):
        result, report = run_case()
        self.assertEqual(report["status"], "feasible")
        self.assertEqual(len(report["evidence"]["tasks"]), 4)
        self.assertEqual(len(report["evidence"]["transitions"]), 3)
        self.assertEqual(report["collaboration"]["summary"]["generated_workflows"], 3)
        self.assertEqual(float(report["statistics"]["time"]["elapsed_minutes"]), 6)
        for item in report["evidence"]["tasks"]:
            task, resource = URIRef(item["task"]), URIRef(item["resource"])
            self.assertIn((task, M.candidateResource, resource), result.merged)
            self.assertEqual(set(item["required_capabilities"]),
                             set(map(str, result.merged.objects(task, M.requiresCapability))))
            self.assertTrue(item["matched_rules"])
            self.assertTrue(item["available"])
            self.assertLessEqual(float(item["response_minutes"]), float(item["max_wait_minutes"]))

    def test_handoff_evidence_reports_explicit_facts(self):
        result, report = run_case()
        for item in report["evidence"]["transitions"]:
            self.assertFalse(item["same_resource"])
            self.assertTrue(item["explicit_authorization"])
            self.assertTrue(item["inferred_handoff"])
            self.assertEqual(item["common_formats"], ["maintenance-json"])

    def test_source_fingerprints_match_files_and_cover_all_layers(self):
        _, report = run_case()
        self.assertEqual({source["kind"] for source in report["sources"]},
                         {"facts", "schema", "validation_profile", "rule", "code", "dependencies"})
        for source in report["sources"]:
            self.assertEqual(source["sha256"], hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest())
        self.assertTrue(report["runtime"]["rdflib"])

    def test_cli_exports_one_case_and_replaces_success_with_infeasible_result(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            output = Path(folder) / "case"
            self.assertEqual(main(["--output-dir", str(output)]), 0)
            graph = Graph().parse(output / "validated_workflows.ttl")
            self.assertEqual(len(list(graph.subjects(RDF.type, M.Execution))), 4)
            graph = load_input()
            graph.set((M.recordingSensor, M.available, Literal(False)))
            source = Path(folder) / "unavailable.ttl"
            graph.serialize(source, format="turtle")
            self.assertEqual(main(["--input", str(source), "--output-dir", str(output)]), 1)
            report = json.loads((output / "case_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "infeasible")
            self.assertIsNone(report["statistics"])
            self.assertEqual(report["evidence"]["tasks"], [])
            self.assertTrue(report["collaboration"]["unmatched_tasks"])
            self.assertEqual(len(Graph().parse(output / "validated_workflows.ttl")), 0)

    def test_markdown_has_diagram_and_escapes_input_labels(self):
        _, report = run_case()
        report["evidence"]["tasks"][0]["task_label"] = '<b>test</b>|\nnew'
        text = markdown(report)
        self.assertIn("```mermaid", text)
        self.assertIn("W1 -->", text)
        self.assertIn("&lt;b&gt;test&lt;/b&gt;&#124; new", text)
        self.assertNotIn("<b>test</b>", text)

    def test_cli_protects_input_and_reports_bad_input(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "merged.ttl"
            source.write_text("invalid turtle", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["--input", str(source), "--output-dir", folder])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--input", str(Path(folder) / 'missing.ttl'),
                                       "--output-dir", str(Path(folder) / 'out')]), 2)
                self.assertEqual(main(["--input", str(source),
                                       "--output-dir", str(Path(folder) / 'out')]), 2)
            self.assertEqual(source.read_text(encoding="utf-8"), "invalid turtle")

    def test_success_report_exposes_capacity_inventory_and_time_evidence(self):
        _, report = run_case()
        checks = report["constraint_evidence"]
        self.assertEqual({key: len(items) for key, items in checks.items()},
                         {"task_response": 4, "dependency_time": 3, "resource_capacity": 3, "supply_stock": 1})
        self.assertTrue(all(item["satisfied"] for items in checks.values() for item in items))
        self.assertEqual(checks["supply_stock"][0]["remaining_units"], 0)
        text = markdown(report)
        self.assertIn("峰值并发 | 容量", text)
        self.assertIn("前置结束 | 后续开始", text)
        self.assertIn("一次性检测试纸 | 1 | 1 | 0", text)

    def test_capacity_failure_is_explained_with_numbers_without_success_checks(self):
        def overlap(graph):
            graph.remove((M.monitorRequest, M.afterWorkflow, None))
            graph.set((M.monitorCheck, M.plannedStartMinutes, Literal("1", datatype=XSD.decimal)))
        _, report = self.modified_case(overlap)
        self.assertEqual(report["status"], "infeasible")
        self.assertTrue(all(not items for items in report["constraint_evidence"].values()))
        text = markdown(report)
        self.assertIn("共享资源并发超过容量", text)
        self.assertIn("active=2; capacity=1", text)
        self.assertNotIn("## 已选维修计划的约束证据", text)

    def test_consumable_failure_exposes_required_and_stock_counts(self):
        _, report = self.modified_case(lambda graph: graph.add((M.monitorCheck, M.usesSupply, M.diagnosticStrip)))
        text = markdown(report)
        self.assertIn("维修耗材库存不足", text)
        self.assertIn("required_units=2; stock_units=1", text)
        self.assertIsNone(report["statistics"])

    def test_handoff_failure_names_the_participating_resources(self):
        _, report = self.modified_case(lambda graph: graph.remove((M.recordingSensor, M.handoffAuthorized, M.basicSensor)))
        text = markdown(report)
        self.assertIn("资源交接条件不满足", text)
        self.assertIn("sender=" + str(M.recordingSensor), text)
        self.assertIn("receiver=" + str(M.basicSensor), text)

    def test_unmatched_tasks_explain_rejected_resources_in_readable_report(self):
        _, report = self.modified_case(lambda graph: graph.set((M.recordingSensor, M.available, Literal(False))))
        text = markdown(report)
        self.assertIn("没有满足全部条件的单个候选资源", text)
        self.assertIn("资源未显式可用", text)
        self.assertIn("缺少能力", text)
        self.assertNotIn("## 已选维修计划的约束证据", text)

    def test_positive_gap_is_reported_without_becoming_execution_work(self):
        def wait(graph):
            for task, start in ((M.confirmCheck, "3"), (M.repairAction, "4"), (M.monitorCheck, "4")):
                graph.set((task, M.plannedStartMinutes, Literal(start, datatype=XSD.decimal)))
        _, report = self.modified_case(wait)
        check = next(item for item in report["constraint_evidence"]["dependency_time"]
                     if item["kind"] == "within_workflow")
        self.assertEqual(Decimal(check["gap_minutes"]), 1)
        self.assertEqual(Decimal(report["statistics"]["time"]["task_minutes"]), 8)

    def test_same_resource_continuation_is_not_presented_as_authorized_self_handoff(self):
        def same_resource(graph):
            graph.set((M.confirmCheck, M.maxWaitMinutes, Literal("5", datatype=XSD.decimal)))
            graph.set((M.confirmCheck, M.requiresRecord, Literal(True)))
            graph.add((M.recordingSensor, M.handoffAuthorized, M.serviceTeam))
        _, report = self.modified_case(same_resource)
        self.assertEqual(report["status"], "feasible")
        transitions = [item for item in report["evidence"]["transitions"] if item["same_resource"]]
        self.assertTrue(transitions)
        for item in transitions:
            self.assertFalse(item["explicit_authorization"])
            self.assertFalse(item["inferred_handoff"])
        self.assertIn("同资源延续不要求自交接授权", markdown(report))


if __name__ == "__main__":
    unittest.main()
