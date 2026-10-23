"""Check both mapping stages and rejection branches against maintenance facts."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from rdflib import Graph, Literal, RDF, XSD

from maintenance_demo.mapping.run_mapping import BASE, M, load_input, main, map_tasks


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.graph = load_input()

    def candidate_pairs(self, result):
        return set(result.candidates.subject_objects(M.candidateResource))

    def test_two_stages_have_exact_expected_results_without_mutating_input(self):
        before = set(self.graph)
        result = map_tasks(self.graph)
        self.assertEqual(set(self.graph), before)
        self.assertFalse(list(self.graph.subject_objects(M.requiresCapability)))
        self.assertEqual(set(result.capabilities.subject_objects(M.requiresCapability)), {
            (M.inspectRecorded, M.detect), (M.inspectRecorded, M.record),
            (M.inspectBasic, M.detect), (M.repairPump, M.repair)})
        self.assertEqual(self.candidate_pairs(result), {
            (M.inspectRecorded, M.recordingSensor), (M.inspectBasic, M.basicSensor),
            (M.repairPump, M.serviceTeam)})
        self.assertEqual(result.report["summary"]["unmatched_tasks"], 0)

    def test_partial_capability_coverage_is_not_candidate(self):
        self.graph.remove((M.recordingSensor, M.providesCapability, M.record))
        result = map_tasks(self.graph)
        item = next(t for t in result.report["tasks"] if t["task"] == str(M.inspectRecorded))
        self.assertEqual(item["status"], "no_candidate_resource")
        self.assertFalse(item["candidates"])

    def test_two_separate_resources_are_not_treated_as_one_combined_provider(self):
        self.graph.set((M.recordingSensor, M.providesCapability, M.record))
        result = map_tasks(self.graph)
        self.assertNotIn((M.inspectRecorded, M.basicSensor), self.candidate_pairs(result))
        self.assertNotIn((M.inspectRecorded, M.recordingSensor), self.candidate_pairs(result))

    def test_unavailable_and_slow_resources_are_explained(self):
        result = map_tasks(self.graph)
        item = next(t for t in result.report["tasks"] if t["task"] == str(M.inspectRecorded))
        rejected = {r["resource"]: r["reasons"] for r in item["rejected_resources"]}
        self.assertIn("unavailable", rejected[str(M.offlineSensor)])
        self.assertIn("response_exceeds_limit", rejected[str(M.slowSensor)])

    def test_unknown_task_kind_does_not_match_empty_requirement(self):
        self.graph.set((M.inspectBasic, M.taskKind, Literal("unsupported")))
        result = map_tasks(self.graph)
        item = next(t for t in result.report["tasks"] if t["task"] == str(M.inspectBasic))
        self.assertEqual(item["status"], "no_matching_task_rule")
        self.assertFalse(item["required_capabilities"])
        self.assertFalse(item["candidates"])

    def test_task_rule_conjunction_requires_matching_asset_kind(self):
        self.graph.set((M.inspectBasic, M.assetKind, Literal("unknown")))
        self.assertFalse(list(map_tasks(self.graph).capabilities.objects(M.inspectBasic, M.requiresCapability)))

    def test_record_condition_changes_inferred_requirement(self):
        self.graph.set((M.inspectRecorded, M.requiresRecord, Literal(False)))
        result = map_tasks(self.graph)
        self.assertEqual(set(result.capabilities.objects(M.inspectRecorded, M.requiresCapability)), {M.detect})
        self.assertIn((M.inspectRecorded, M.basicSensor), self.candidate_pairs(result))

    def test_response_limit_is_inclusive(self):
        self.graph.set((M.recordingSensor, M.responseMinutes, Literal("5.0", datatype=XSD.decimal)))
        self.assertIn((M.inspectRecorded, M.recordingSensor), self.candidate_pairs(map_tasks(self.graph)))
        self.graph.set((M.recordingSensor, M.responseMinutes, Literal("5.01", datatype=XSD.decimal)))
        self.assertNotIn((M.inspectRecorded, M.recordingSensor), self.candidate_pairs(map_tasks(self.graph)))

    def test_missing_ambiguous_or_wrong_type_facts_fail_before_rule_execution(self):
        for action in ("missing", "multiple", "wrong_type"):
            with self.subTest(action=action):
                graph = load_input()
                if action == "missing":
                    graph.remove((M.basicSensor, M.available, None))
                elif action == "multiple":
                    graph.add((M.basicSensor, M.available, Literal(False)))
                else:
                    graph.set((M.basicSensor, M.responseMinutes, Literal("fast")))
                with self.assertRaisesRegex(ValueError, "输入数据校验失败"):
                    map_tasks(graph)

    def test_preexisting_conclusions_rejected_and_repeated_raw_runs_stable(self):
        first = map_tasks(self.graph)
        self.assertEqual(first.report, map_tasks(self.graph).report)
        with self.assertRaisesRegex(ValueError, "不能预填"):
            map_tasks(first.merged)

    def test_subclass_tasks_and_resources_are_included(self):
        result = map_tasks(self.graph)
        self.assertIn((M.inspectBasic, M.basicSensor), self.candidate_pairs(result))
        self.assertIn((M.inspectBasic, RDF.type, M.PumpTask), self.graph)
        self.assertIn((M.basicSensor, RDF.type, M.Sensor), self.graph)

    def test_cli_exports_parseable_separate_stage_outputs(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--output-dir", folder]), 0)
            report = json.loads((Path(folder) / "mapping_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["candidate_relations"], 3)
            self.assertEqual(len(report["rule_sha256"]["task_rules.rq"]), 64)
            self.assertEqual(len(Graph().parse(Path(folder) / "candidate_resources.ttl")), 3)
            self.assertEqual(len(Graph().parse(Path(folder) / "inferred_capabilities.ttl")), 8)
            self.assertTrue(len(Graph().parse(Path(folder) / "merged.ttl")) > 8)


if __name__ == "__main__":
    unittest.main()
