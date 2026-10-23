"""Data quality failure branches; no planning, scoring or database access."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from rdflib import Graph, Literal, Namespace, RDF, XSD

from validate_data import check_profile, main, validate

ROOT = Path(__file__).resolve().parents[1]
M = Namespace("https://example.org/maintenance#")


class DataValidationTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph().parse(ROOT / "maintenance_demo/maintenance_ontology.ttl")
        self.profile = json.loads((ROOT / "examples/maintenance_data_profile.json").read_text(encoding="utf-8"))

    def errors(self):
        return {code for level, code, _ in validate(self.graph, self.profile).items if level == "ERROR"}

    def test_existing_maintenance_example_passes_and_covers_subclasses(self):
        report = validate(self.graph, self.profile)
        self.assertEqual(report.counts()["ERROR"], 0)
        self.assertTrue(any("11 " in message for _, code, message in report.items if code == "DATA"))

    def test_missing_required_field_fails_even_with_owl_existential(self):
        self.graph.remove((M.inspect01, M.requiresCapability, None))
        self.assertIn("MIN_COUNT", self.errors())

    def test_multiple_distinct_duration_values_fail(self):
        self.graph.add((M.observe01, M.durationMinutes, Literal("4.0", datatype=XSD.decimal)))
        self.assertIn("MAX_COUNT", self.errors())

    def test_string_duration_and_ill_typed_decimal_fail(self):
        for value in (Literal("two"), Literal("not-a-number", datatype=XSD.decimal, normalize=False)):
            with self.subTest(value=value):
                self.graph.set((M.observe01, M.durationMinutes, value))
                self.assertIn("DATATYPE", self.errors())

    def test_negative_duration_fails(self):
        self.graph.set((M.observe01, M.durationMinutes, Literal("-1.0", datatype=XSD.decimal)))
        self.assertIn("MIN_VALUE", self.errors())

    def test_integer_duration_is_compatible_with_decimal(self):
        self.graph.set((M.observe01, M.durationMinutes, Literal(2, datatype=XSD.int)))
        self.assertFalse(self.errors())

    def test_missing_capability_type_is_not_silently_inferred_from_range(self):
        self.graph.remove((M.detect, RDF.type, M.Capability))
        self.assertIn("VALUE_CLASS", self.errors())

    def test_boolean_false_is_valid_explicit_availability(self):
        self.assertEqual(self.graph.value(M.technician02, M.available).toPython(), False)
        self.assertFalse(self.errors())

    def test_dependency_cycle_and_self_loop_fail(self):
        for start, end in ((M.observe01, M.inspect01), (M.observe01, M.observe01)):
            with self.subTest(start=start, end=end):
                self.graph.add((start, M.dependsOn, end))
                self.assertIn("CYCLE", self.errors())
                self.graph.remove((start, M.dependsOn, end))

    def test_empty_data_fails_required_target_coverage(self):
        for subject, type_ in list(self.graph.subject_objects(RDF.type)):
            if str(type_).startswith(str(M)):
                self.graph.remove((subject, RDF.type, type_))
        self.assertIn("NO_TARGET", self.errors())

    def test_misspelled_class_and_property_fail(self):
        self.profile["rules"][0]["target_class"] += "Typo"
        self.profile["rules"][1]["fields"][0]["path"] += "Typo"
        self.assertIn("PROFILE", self.errors())

    def test_maximum_and_nonfinite_values(self):
        field = self.profile["rules"][0]["fields"][1]
        field["max_value"] = 20
        self.graph.set((M.observe01, M.durationMinutes, Literal("21", datatype=XSD.decimal)))
        self.assertIn("MAX_VALUE", self.errors())
        field["datatype"] = str(XSD.double)
        self.graph.set((M.observe01, M.durationMinutes, Literal(float("inf"), datatype=XSD.double)))
        self.assertIn("NUMBER", self.errors())

    def test_invalid_configuration_fails_instead_of_ignoring(self):
        for update in ({"min_cout": 1}, {"min_count": True}, {"min_count": 2, "max_count": 1},
                       {"min_value": "NaN"}, {"datatype": str(XSD.boolean), "min_value": 0}):
            profile = copy.deepcopy(self.profile)
            profile["rules"][0]["fields"][1].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                check_profile(profile)

    def test_cli_report_and_overwrite_protection(self):
        data = ROOT / "maintenance_demo/maintenance_ontology.ttl"
        profile = ROOT / "examples/maintenance_data_profile.json"
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            output = Path(folder) / "report.json"
            self.assertEqual(main([str(data), "--profile", str(profile), "--json-output", str(output)]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["counts"]["ERROR"], 0)
            self.assertEqual(report["profile"], str(profile.resolve()))
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
                main([str(data), "--profile", str(profile), "--json-output", str(data)])
            self.assertEqual(failure.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
