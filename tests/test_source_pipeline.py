"""Technical fixtures only: never research observations or business instances."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from rdflib import Graph, Literal, RDF, URIRef
from rdflib.namespace import XSD

from adapters.check_project import check_project
from adapters.environment_to_rdf import environment_to_rdf, load_environment_input
from adapters.source_to_rdf import source_to_rdf
from environment.loader import ENV, load_environment, query_observations
from integration.load_project import LINK, load_project, query_project
from source_data.cli import main
from source_data.common import (InputError, ValidationError, digest, dumps, read_json,
                                source_iri, write_outputs)
from source_data.ingest import build_bundle, ingest
from source_data.load import SRC, bundle_from_graph, load_source
from source_data.query import query_issues, query_records, query_unconfirmed
from source_data.validate import validate_bundle

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data/article_source/raw_tables.json"
MAPPING = ROOT / "source_mappings/article_source.json"


def setUpModule():
    (ROOT / "tmp").mkdir(exist_ok=True)


class SourcePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = ingest(RAW, MAPPING)
        cls.original_digest = digest(RAW)
        cls.workspace = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="source-tests-")
        cls.directory = Path(cls.workspace.name)
        cls.ttl = cls.directory / "source.ttl"
        source_to_rdf(cls.bundle).serialize(cls.ttl, format="turtle")
        cls.graph, cls.report = load_source([cls.ttl])

    @classmethod
    def tearDownClass(cls):
        assert digest(RAW) == cls.original_digest
        cls.workspace.cleanup()

    def build(self, raw=None, mapping=None):
        return build_bundle(raw or read_json(RAW), mapping or read_json(MAPPING),
                            input_filename=RAW.name, input_sha256=self.original_digest)

    def test_review_counts_and_all_original_values(self):
        review = read_json(ROOT / "data/article_source/review_report.json")
        raw = read_json(RAW)
        rows = query_records(self.graph)
        self.assertEqual(len(rows), 18)
        for table, count in review["table_rows"].items():
            selected = [row for row in rows if row["table_info"]["input_path"] == table]
            self.assertEqual(len(selected), count)
            for row in selected:
                source = raw[table][row["row_number"] - 1] if isinstance(raw[table], list) else raw[table]
                self.assertEqual(dumps(row["raw_object"]), dumps(source))
                self.assertEqual({value["field"]["key"]: value["raw_value"] for value in row["fields"]}, source)
                self.assertTrue(row["materials"])
        self.assertEqual(sum(review["issue_counts"].values()), len(query_issues(self.graph)))

    def test_lossless_roundtrip_and_stability(self):
        self.assertEqual(self.report["counts"]["ERROR"], 0)
        self.assertEqual(dumps(bundle_from_graph(self.graph)), dumps(self.bundle))
        self.assertEqual(dumps(ingest(RAW, MAPPING)), dumps(self.bundle))
        # JSON object ordering must not influence IDs or entity/reference order.
        unordered = json.loads(dumps(read_json(RAW)))
        self.assertEqual(dumps(self.build(unordered, json.loads(dumps(read_json(MAPPING))))), dumps(self.bundle))

    def test_source_issues_not_resolved_by_transcription_review(self):
        self.assertEqual(len(query_issues(self.graph, status="open")), 13)
        self.assertEqual(query_issues(self.graph, status="resolved"), [])
        disputed = [v for v in self.bundle["values"] if v["input_path"] == "table9[3].max_range_nm"][0]
        self.assertEqual(disputed["raw_value"], 0.2)
        self.assertIsNone(disputed["normalized_value"])
        self.assertTrue(disputed["issue_ids"])
        self.assertTrue(query_unconfirmed(self.graph))

    def test_missing_null_boolean_and_nested_json_are_distinct(self):
        raw = read_json(RAW)
        raw["table7"][0]["count"] = None
        raw["table7"][1].pop("count")
        raw["table7"][2]["count"] = True
        raw["table7"][3]["count"] = {"a": [False, None, 1, 1.5]}
        bundle = self.build(raw)
        values = {v["input_path"]: v for v in bundle["values"]}
        self.assertEqual(values["table7[0].count"]["missing_status"], "null")
        self.assertEqual(values["table7[1].count"]["missing_status"], "missing")
        self.assertEqual(values["table7[2].count"]["raw_type"], "boolean")
        self.assertEqual(dumps(bundle_from_graph(source_to_rdf(bundle))), dumps(bundle))

    def test_unknown_and_dimensionless_units_not_conflated(self):
        fields = {f["key"]: f for f in self.bundle["fields"]}
        self.assertEqual(fields["precision_raw"]["unit"]["status"], "unconfirmed")
        self.assertEqual(fields["damage_value"]["unit"]["status"], "unknown")
        self.assertEqual(fields["altitude_ft"]["unit"]["description"], "ft")

    def test_invalid_mapping_path_and_expression(self):
        for path in ["table404", "__import__('os').system('x')"]:
            mapping = read_json(MAPPING)
            mapping["tables"][0]["path"] = path
            with self.assertRaises((InputError, ValidationError)):
                self.build(mapping=mapping)

    def test_duplicate_mapping_and_original_row_identifier(self):
        mapping = read_json(MAPPING)
        mapping["tables"].append(deepcopy(mapping["tables"][0]))
        with self.assertRaises(InputError):
            self.build(mapping=mapping)
        raw = read_json(RAW)
        raw["table7"][1]["id"] = raw["table7"][0]["id"]
        with self.assertRaises(ValidationError):
            self.build(raw)

    def test_issue_invalid_field_and_unknown_material(self):
        for key, value in [("field_paths", ["table8[99].altitude_ft"]), ("source_files", ["unknown.png"])]:
            raw = read_json(RAW)
            raw["issues"][0][key] = value
            with self.assertRaises(ValidationError):
                self.build(raw)

    def test_package_tampering_and_type_errors_fail(self):
        variants = []
        for mutate in [lambda b: b["values"][0].update(raw_value="changed"),
                       lambda b: b["values"][0].update(field_id="absent"),
                       lambda b: b["records"].append(b["records"][0]),
                       lambda b: b["issues"][0].update(status="resolved"),
                       lambda b: b["values"][0].update(normalized_value=10),
                       lambda b: b["records"][0].update(row_number="one")]:
            value = deepcopy(self.bundle)
            mutate(value)
            variants.append(value)
        for bundle in variants:
            self.assertGreater(validate_bundle(bundle)["counts"]["ERROR"], 0)

    def test_rdf_missing_or_extra_reference_and_type_fail(self):
        for change in ("reference", "type", "member", "unknown"):
            graph = source_to_rdf(self.bundle)
            node = URIRef(source_iri(self.bundle["values"][0]["id"]))
            if change == "reference":
                graph.set((node, SRC.field_id, URIRef("urn:absent")))
            elif change == "type":
                graph.remove((node, RDF.type, SRC.FieldValue))
            elif change == "member":
                head = next(graph.subjects(RDF.type, SRC.Bundle))
                graph.add((head, SRC.contains, URIRef("urn:absent")))
            else:
                graph.add((node, SRC.typo, Literal("bad")))
            with self.assertRaises(ValidationError):
                bundle_from_graph(graph)

    def test_changed_original_digest_detected(self):
        path = self.directory / "changed.json"
        path.write_text(dumps(read_json(RAW)), encoding="utf-8")
        self.assertGreater(validate_bundle(self.bundle, original_path=path)["counts"]["ERROR"], 0)

    def test_default_no_overwrite_and_atomic_replacement(self):
        directory = self.directory / "outputs"
        write_outputs(directory, {"a.json": "old"})
        with self.assertRaises(InputError):
            write_outputs(directory, {"new.json": "new", "a.json": "bad"})
        self.assertFalse((directory / "new.json").exists())
        self.assertEqual((directory / "a.json").read_text(), "old")
        write_outputs(directory, {"a.json": "new"}, overwrite=True)
        self.assertEqual((directory / "a.json").read_text(), "new")
        with self.assertRaises(InputError):
            write_outputs(RAW.parent, {"bundle.json": "bad"})
        with self.assertRaises(InputError):
            write_outputs(directory, {"../escape": "bad"})

    def test_independent_source_module_without_project_or_adapters(self):
        directory = self.directory / "standalone"
        directory.mkdir(exist_ok=True)
        shutil.copytree(ROOT / "source_data", directory / "source_data", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
        result = subprocess.run([sys.executable, "-m", "source_data.cli", "query", str(self.ttl),
                                 "--kind", "issues", "--status", "open"], cwd=directory,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["count"], 13)

    def test_cli_errors_are_json_and_have_input_exit_code(self):
        result = subprocess.run([sys.executable, "-m", "source_data.cli", "no-such-command"],
                                 cwd=ROOT, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "invalid")

    def test_real_background_and_expected_strict_missing_observations(self):
        result = check_project([self.ttl], [ROOT / "data/article_environment/scene.ttl"],
            [ROOT / "data/article_environment/environment.ttl"], [ROOT / "data/article_environment/links.ttl"])
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["counts"]["observations"], 0)
        self.assertEqual(result["modes"]["scene_background"]["connections"][0]["connection_status"], "background_only")
        self.assertEqual(result["modes"]["observations_required"]["counts"]["ERROR"], 4)
        self.assertEqual(result["modes"]["scene_background"]["area_filter_results"]["urn:article-environment:Area"], [])


class EnvironmentAdapterTests(unittest.TestCase):
    def fixture(self):
        return {"format_version":"1.0", "areas":[{"id":"urn:test:area", "name":"技术测试区域", "source_refs":[]}],
                "parameters":[{"id":"urn:test:parameter", "name":"技术测试量", "definition":"仅用于适配器技术验证", "datatype":str(XSD.decimal),
                    "unit":"1", "sources":["technical fixture"], "source_locations":[], "source_refs":[]}],
                "observations":[{"id":"urn:test:observation", "parameter":"urn:test:parameter", "area":"urn:test:area", "value":"1.25",
                    "observed_at":"2000-01-01T00:00:00Z", "sources":["technical fixture"], "source_locations":[],
                    "source_refs":[], "blocking_issue_ids":[]}]}

    def test_complete_technical_observation_uses_existing_loader_and_query(self):
        document = self.fixture()
        graph = environment_to_rdf(document)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            path = Path(directory) / "env.ttl"
            graph.serialize(path, format="turtle")
            loaded, result = load_environment([path])
            self.assertEqual(result.counts()["ERROR"], 0)
            rows = query_observations(loaded, area=URIRef("urn:test:area"))
            self.assertEqual(rows[0]["value"], "1.25")
            scene = Path(directory) / "scene.ttl"
            scene.write_text('@prefix kw: <http://example.org/killweb#> . <urn:test:mission> a kw:CombatMission ; kw:hasSituation <urn:test:situation> . <urn:test:situation> a kw:BattlefieldSituation . <urn:test:situation> <urn:environment-integration:hasObservation> <urn:test:observation> .', encoding="utf-8")
            joined, report = load_project([scene], [path], require_observations=True)
            self.assertEqual(report.counts()["ERROR"], 0)
            self.assertEqual(query_project(joined, require_observations=True)[0]["connection_status"], "observation_linked")

    def test_area_only_is_valid_and_raw_tables_are_not_environment(self):
        document = self.fixture()
        document["parameters"] = []
        document["observations"] = []
        graph = environment_to_rdf(document)
        self.assertEqual(len(list(graph.subjects(RDF.type, ENV.Area))), 1)
        with self.assertRaises(ValidationError):
            environment_to_rdf(read_json(RAW))

    def test_incomplete_invalid_references_types_and_blocking_issues_fail(self):
        for mutation in [lambda d: d["observations"][0].pop("observed_at"),
                         lambda d: d["observations"][0].update(observed_at="2000-01-01T00:00:00"),
                         lambda d: d["observations"][0].update(area="urn:absent"),
                         lambda d: d["observations"][0].update(blocking_issue_ids=["issue-1"]),
                         lambda d: d["observations"][0].update(value=True),
                         lambda d: d["observations"][0].update(value="NaN"),
                         lambda d: d["parameters"][0].update(unit=None),
                         lambda d: d["areas"].append(deepcopy(d["areas"][0]))]:
            document = self.fixture()
            mutation(document)
            with self.assertRaises(ValidationError):
                environment_to_rdf(document)

    def test_integer_fraction_does_not_silently_truncate(self):
        document = self.fixture()
        document["parameters"][0]["datatype"] = str(XSD.integer)
        document["observations"][0]["value"] = 1.25
        with self.assertRaises(ValidationError):
            environment_to_rdf(document)


if __name__ == "__main__":
    unittest.main()
