"""Maintenance handoff, workflow composition and fixed-window capacity checks."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from rdflib import Graph, Literal, RDF, XSD

from maintenance_demo.collaboration.run_collaboration import M, collaborate, load_input, main


class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.graph = load_input()

    def result(self):
        return collaborate(self.graph)

    def decimal(self, node, prop, value):
        self.graph.set((node, prop, Literal(value, datatype=XSD.decimal)))

    def rejected_for(self, result, reason):
        self.assertEqual(result.report["status"], "infeasible")
        self.assertGreater(result.report["rejected_combination_counts"].get(reason, 0), 0)
        self.assertEqual(len(result.workflows), 0)

    def independent_monitor(self, start):
        self.graph.remove((M.monitorRequest, M.afterWorkflow, None))
        self.decimal(M.monitorCheck, M.plannedStartMinutes, start)

    def test_expected_three_workflows_and_four_relations(self):
        result = self.result()
        self.assertEqual(result.report["status"], "feasible")
        self.assertEqual(result.report["summary"]["tasks"], 4)
        self.assertEqual(result.report["summary"]["generated_workflows"], 3)
        counts = {kind: len(list(result.workflows.subjects(RDF.type, M[kind])))
                  for kind in ("SequentialRelation", "ParallelRelation", "BranchRelation", "Execution")}
        self.assertEqual(counts, {"SequentialRelation": 2, "ParallelRelation": 1, "BranchRelation": 1, "Execution": 4})
        sequence = next(f for f in result.report["workflows"] if f["request"] == str(M.inspectionRequest))
        self.assertEqual([t["task"] for t in sequence["tasks"]], [str(M.recordCheck), str(M.confirmCheck)])

    def test_same_resource_has_distinct_participation_records(self):
        result = self.result()
        nodes = list(result.workflows.subjects(M.assignedResource, M.recordingSensor))
        self.assertEqual(len(nodes), 2)
        self.assertNotEqual(nodes[0], nodes[1])
        self.assertEqual({result.workflows.value(n, M.executionTask) for n in nodes}, {M.recordCheck, M.monitorCheck})

    def test_handoff_requires_explicit_authorization(self):
        self.graph.remove((M.recordingSensor, M.handoffAuthorized, M.basicSensor))
        self.rejected_for(self.result(), "handoff_not_feasible")

    def test_reverse_authorization_does_not_allow_forward_handoff(self):
        self.graph.remove((M.recordingSensor, M.handoffAuthorized, M.basicSensor))
        self.graph.add((M.basicSensor, M.handoffAuthorized, M.recordingSensor))
        self.rejected_for(self.result(), "handoff_not_feasible")

    def test_authorization_without_compatible_format_rejected(self):
        self.graph.set((M.basicSensor, M.inputFormat, Literal("incompatible-format")))
        self.rejected_for(self.result(), "handoff_not_feasible")

    def test_unavailable_provider_creates_unmatched_tasks_not_allocations(self):
        self.graph.set((M.recordingSensor, M.available, Literal(False)))
        result = self.result()
        self.assertEqual(result.report["status"], "infeasible")
        self.assertEqual(result.report["summary"]["evaluated_combinations"], 0)
        self.assertEqual(len(result.report["unmatched_tasks"]), 2)
        self.assertFalse(len(result.workflows))

    def test_overlapping_tasks_inside_sequence_rejected(self):
        self.decimal(M.confirmCheck, M.plannedStartMinutes, "1.5")
        self.rejected_for(self.result(), "dependency_time_overlap")

    def test_workflow_starts_before_parent_completes_rejected(self):
        self.decimal(M.repairAction, M.plannedStartMinutes, "2.5")
        self.rejected_for(self.result(), "workflow_time_overlap")

    def test_shared_resource_capacity_conflict_rejected(self):
        self.independent_monitor("1.0")
        self.rejected_for(self.result(), "resource_capacity_exceeded")

    def test_capacity_two_allows_two_overlapping_uses(self):
        self.independent_monitor("1.0")
        self.graph.set((M.recordingSensor, M.capacity, Literal(2, datatype=XSD.positiveInteger)))
        self.assertEqual(self.result().report["status"], "feasible")

    def test_end_equals_next_start_frees_resource_capacity(self):
        self.independent_monitor("2.0")
        self.assertEqual(self.result().report["status"], "feasible")

    def test_consumable_cannot_be_reused_by_later_workflow(self):
        self.graph.add((M.monitorCheck, M.usesSupply, M.diagnosticStrip))
        self.rejected_for(self.result(), "supply_stock_exceeded")

    def test_sufficient_consumable_stock_passes_without_mutating_stock(self):
        self.graph.add((M.monitorCheck, M.usesSupply, M.diagnosticStrip))
        self.graph.set((M.diagnosticStrip, M.stockCount, Literal(2, datatype=XSD.nonNegativeInteger)))
        result = self.result()
        self.assertEqual(result.report["status"], "feasible")
        self.assertEqual(result.report["supplies"][0]["reserved_units"], 2)
        self.assertEqual(int(self.graph.value(M.diagnosticStrip, M.stockCount)), 2)

    def test_zero_consumable_stock_cannot_be_reserved(self):
        self.graph.set((M.diagnosticStrip, M.stockCount, Literal(0, datatype=XSD.nonNegativeInteger)))
        result = self.result()
        self.rejected_for(result, "supply_stock_exceeded")
        self.assertEqual(result.report["supplies"][0]["reserved_units"], 0)

    def test_dependency_cycle_is_input_error(self):
        self.graph.add((M.inspectionRequest, M.afterWorkflow, M.monitorRequest))
        with self.assertRaisesRegex(ValueError, "CYCLE"):
            self.result()

    def test_task_cycle_is_input_error(self):
        self.graph.add((M.recordCheck, M.dependsOn, M.confirmCheck))
        with self.assertRaisesRegex(ValueError, "CYCLE"):
            self.result()

    def test_disconnected_tasks_are_not_exported_as_a_sequence(self):
        self.graph.remove((M.confirmCheck, M.dependsOn, None))
        with self.assertRaisesRegex(ValueError, "单条连通任务序列"):
            self.result()

    def test_task_must_belong_to_one_request(self):
        self.graph.add((M.monitorRequest, M.hasTask, M.repairAction))
        self.graph.add((M.repairAction, M.dependsOn, M.monitorCheck))
        with self.assertRaisesRegex(ValueError, "只能属于一个"):
            self.result()

    def test_cross_workflow_task_dependency_rejected(self):
        self.graph.add((M.monitorCheck, M.dependsOn, M.confirmCheck))
        with self.assertRaisesRegex(ValueError, "流程内前置任务"):
            self.result()

    def test_zero_duration_and_bad_capacity_rejected(self):
        self.decimal(M.monitorCheck, M.durationMinutes, "0")
        with self.assertRaisesRegex(ValueError, "大于零"):
            self.result()
        self.graph = load_input()
        self.graph.set((M.recordingSensor, M.capacity, Literal(0, datatype=XSD.integer)))
        with self.assertRaisesRegex(ValueError, "输入校验失败"):
            self.result()

    def test_empty_capability_inference_cannot_generate_a_workflow(self):
        self.graph.set((M.monitorCheck, M.taskKind, Literal("unsupported")))
        result = self.result()
        self.assertEqual(result.report["status"], "infeasible")
        self.assertEqual(result.report["unmatched_tasks"][0]["status"], "no_matching_task_rule")

    def add_alternative_repair_service(self):
        for prop, value in list(self.graph.predicate_objects(M.serviceTeam)):
            self.graph.add((M.aAlternativeService, prop, value))

    def test_skips_first_incompatible_assignment_and_selects_feasible_one(self):
        self.add_alternative_repair_service()
        result = self.result()
        self.assertEqual(result.report["status"], "feasible")
        self.assertEqual(result.report["summary"]["resource_combinations"], 2)
        self.assertEqual(result.report["summary"]["evaluated_combinations"], 2)
        repair = next(f for f in result.report["workflows"] if f["request"] == str(M.repairRequest))
        self.assertEqual(repair["tasks"][0]["resource"], str(M.serviceTeam))

    def test_search_limit_is_not_reported_as_proven_infeasibility(self):
        self.add_alternative_repair_service()
        with self.assertRaisesRegex(ValueError, "超过上限.*未证明"):
            collaborate(self.graph, max_combinations=1)

    def test_input_is_unchanged_and_generated_facts_cannot_be_reused(self):
        before = set(self.graph)
        result = self.result()
        self.assertEqual(before, set(self.graph))
        self.assertEqual(result.report, self.result().report)
        with self.assertRaisesRegex(ValueError, "原始事实"):
            collaborate(result.merged)

    def test_prepopulated_handoff_is_rejected_even_without_execution_objects(self):
        self.graph.add((M.recordingSensor, M.canHandoffTo, M.basicSensor))
        with self.assertRaisesRegex(ValueError, "不能预填"):
            self.result()

    def test_cli_exports_feasible_network_and_parseable_report(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--output-dir", folder]), 0)
            report = json.loads((Path(folder) / "collaboration_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "feasible")
            graph = Graph().parse(Path(folder) / "validated_workflows.ttl")
            self.assertEqual(len(list(graph.subjects(RDF.type, M.Workflow))), 3)


if __name__ == "__main__":
    unittest.main()
