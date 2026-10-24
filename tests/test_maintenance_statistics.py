"""Check maintenance time accounting against parallel, idle and shared use."""
import contextlib
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest

from rdflib import Literal, XSD

from maintenance_demo.collaboration.run_collaboration import M, collaborate, load_input
from maintenance_demo.collaboration.summarize_workflows import interval_statistics, main, summarize


class MaintenanceStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.graph = load_input()

    def stats(self):
        return summarize(collaborate(self.graph))

    def test_parallel_work_is_not_network_elapsed_time(self):
        report = self.stats()
        expected = {"start_minutes": 0, "end_minutes": 6, "elapsed_minutes": 6,
                    "task_minutes": 8, "occupied_minutes": 6, "idle_minutes": 0,
                    "peak_concurrent_tasks": 2}
        self.assertEqual({k: Decimal(str(v)) for k, v in report["time"].items()}, expected)
        self.assertEqual(sum(Decimal(w["elapsed_minutes"]) for w in report["workflows"]), 8)
        self.assertEqual(report["supplies"][0]["remaining_units"], 0)
        self.assertEqual(set(report["unavailable_metrics"]), {"cost", "quality"})

    def test_resource_reuse_is_counted_once_per_participation(self):
        report = self.stats()
        sensor = next(r for r in report["resources"] if r["resource"] == str(M.recordingSensor))
        self.assertEqual(sensor["participations"], 2)
        self.assertEqual(Decimal(sensor["occupied_minutes"]), 4)
        self.assertEqual(sensor["peak_concurrent"], 1)
        self.assertEqual(Decimal(sensor["capacity_utilization"]), Decimal(4) / Decimal(6))

    def test_concurrent_resource_use_has_distinct_union_and_work(self):
        self.graph.remove((M.monitorRequest, M.afterWorkflow, None))
        self.graph.set((M.monitorCheck, M.plannedStartMinutes, Literal("1", datatype=XSD.decimal)))
        self.graph.set((M.recordingSensor, M.capacity, Literal(2, datatype=XSD.positiveInteger)))
        sensor = next(r for r in self.stats()["resources"] if r["resource"] == str(M.recordingSensor))
        self.assertEqual(Decimal(sensor["occupied_minutes"]), 3)
        self.assertEqual(Decimal(sensor["task_minutes"]), 4)
        self.assertEqual(sensor["peak_concurrent"], 2)
        self.assertEqual(Decimal(sensor["capacity_utilization"]), Decimal(4) / Decimal(12))

    def test_workflow_waiting_is_not_added_to_task_work(self):
        for task, start in ((M.confirmCheck, "3"), (M.repairAction, "4"), (M.monitorCheck, "4")):
            self.graph.set((task, M.plannedStartMinutes, Literal(start, datatype=XSD.decimal)))
        report = self.stats()
        inspection = next(w for w in report["workflows"] if w["request"] == str(M.inspectionRequest))
        self.assertEqual(Decimal(inspection["idle_gap_minutes"]), 1)
        self.assertEqual(Decimal(report["time"]["idle_minutes"]), 1)
        self.assertEqual(Decimal(report["time"]["elapsed_minutes"]), 7)

    def test_interval_union_handles_nested_and_touching_intervals(self):
        windows = [(Decimal(a), Decimal(b)) for a, b in ((0, 4), (1, 2), (4, 5), (7, 8))]
        self.assertEqual(interval_statistics(windows), (Decimal(6), 2))
        self.assertEqual(interval_statistics([(Decimal(0), Decimal(1)),
                                              (Decimal(1), Decimal(2))]), (Decimal(2), 1))

    def test_invalid_interval_is_rejected(self):
        for start, end in (("0", "0"), ("2", "1"), ("0", "Infinity"), ("NaN", "1")):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                interval_statistics([(Decimal(start), Decimal(end))])

    def test_infeasible_plan_cannot_receive_success_statistics(self):
        self.graph.set((M.recordingSensor, M.available, Literal(False)))
        with self.assertRaisesRegex(ValueError, "已验证可行"):
            self.stats()

    def test_cli_writes_json_and_does_not_change_input(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            output = Path(folder) / "maintenance_statistics.json"
            before = set(self.graph)
            self.stats()
            self.assertEqual(set(self.graph), before)
            self.assertEqual(main(["--output", str(output)]), 0)
            self.assertEqual(main(["--output", str(output)]), 0)
            self.assertEqual(Decimal(json.loads(output.read_text(encoding="utf-8"))["time"]["task_minutes"]), 8)

    def test_cli_refuses_overwriting_an_existing_configuration(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stderr(io.StringIO()):
            config = Path(folder) / "config.json"
            config.write_text('{"keep": true}', encoding="utf-8")
            with self.assertRaises(SystemExit) as exc:
                main(["--output", str(config)])
            self.assertEqual(exc.exception.code, 2)
            self.assertEqual(config.read_text(encoding="utf-8"), '{"keep": true}')

    def test_cli_infeasible_input_does_not_write_report(self):
        self.graph.set((M.recordingSensor, M.available, Literal(False)))
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            source = Path(folder) / "facts.ttl"
            self.graph.serialize(source, format="turtle")
            output = Path(folder) / "maintenance_statistics.json"
            self.assertEqual(main(["--input", str(source), "--output", str(output)]), 1)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
