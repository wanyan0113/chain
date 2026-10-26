"""Explicit environmental JSON or existing TTL; never interprets equipment tables."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

from jsonschema import Draft202012Validator
from rdflib import Graph, Literal, RDF, RDFS, URIRef
from rdflib.namespace import XSD

from environment.loader import (ENV, NUMERIC, SUPPORTED, load_environment,
                                query_observations, query_parameters, timestamp)
from source_data.common import (InputError, ValidationError, dumps, read_json,
                                report, write_outputs)

MAPPING = Path(__file__).resolve().parent.parent / "adapter_mappings/environment.json"


def checked_timestamp(value):
    try:
        return timestamp(value)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def validate_graph(graph):
    with tempfile.TemporaryDirectory(prefix="environment-adapter-") as directory:
        path = Path(directory) / "environment.ttl"
        graph.serialize(path, format="turtle")
        _, result = load_environment([path], require_observations=False, require_parameters=False)
    if result.counts()["ERROR"]:
        raise ValidationError(dumps({"errors": result.items, "counts": result.counts()}))


def environment_to_rdf(document):
    mapping = read_json(MAPPING)
    if mapping["format_version"] != "1.0" or mapping["namespace"] != str(ENV):
        raise InputError("环境映射版本或命名空间错误")
    errors = [f"{list(err.absolute_path)}: {err.message}"
              for err in Draft202012Validator(mapping["schema"]).iter_errors(document)]
    if errors:
        raise ValidationError(dumps(errors))
    identifiers = [obj["id"] for group in ("areas", "parameters", "observations") for obj in document[group]]
    if len(identifiers) != len(set(identifiers)):
        raise ValidationError("环境输入存在重复标识")
    properties = mapping["properties"]
    expected_properties = {"name":"name", "definition":"definition", "unit":"unit", "datatype":"valueDatatype",
        "parameter":"parameter", "area":"area", "value":"value", "observed_at":"observedAt", "sources":"source", "source_locations":"sourceLocation"}
    if properties != expected_properties:
        raise InputError("环境属性映射与现有本体契约不一致")
    graph = Graph()
    graph.bind("env", ENV)
    parameters = {obj["id"]: obj for obj in document["parameters"]}
    areas = {obj["id"] for obj in document["areas"]}
    for collection, cls in (("areas", ENV.Area), ("parameters", ENV.Parameter), ("observations", ENV.Observation)):
        for obj in document[collection]:
            node = URIRef(obj["id"])
            graph.add((node, RDF.type, cls))
            for reference in obj["source_refs"]:
                graph.add((node, RDFS.seeAlso, URIRef(reference)))
            if collection == "observations":
                if obj["blocking_issue_ids"]:
                    raise ValidationError(f"观测存在阻塞疑点，不能导出：{obj['id']}")
                if obj["parameter"] not in parameters or obj["area"] not in areas:
                    raise ValidationError("观测参数或区域引用未定义")
                datatype = URIRef(parameters[obj["parameter"]]["datatype"])
                value = obj["value"]
                if datatype == XSD.boolean and not isinstance(value, bool):
                    raise ValidationError("boolean 观测必须为 JSON 布尔值")
                if datatype in NUMERIC and (isinstance(value, bool) or not isinstance(value, (str, int, float))):
                    raise ValidationError("数值观测类型错误")
                if datatype in {XSD.string, XSD.dateTime} and not isinstance(value, str):
                    raise ValidationError("字符串/时间观测必须为 JSON 字符串")
                if datatype == XSD.dateTime:
                    checked_timestamp(value)
                checked_timestamp(obj["observed_at"])
                graph.add((node, ENV.value, Literal(value, datatype=datatype)))
                graph.add((node, ENV.observedAt, Literal(obj["observed_at"], datatype=XSD.dateTime)))
                for key in ("parameter", "area"):
                    graph.add((node, ENV[properties[key]], URIRef(obj[key])))
            else:
                graph.add((node, ENV.name, Literal(obj["name"], datatype=XSD.string)))
                if collection == "parameters":
                    datatype = URIRef(obj["datatype"])
                    if datatype not in SUPPORTED:
                        raise ValidationError(f"不支持的环境数据类型：{datatype}")
                    graph.add((node, ENV.valueDatatype, datatype))
                    graph.add((node, ENV.definition, Literal(obj["definition"], datatype=XSD.string)))
                    if obj["unit"] is not None:
                        graph.add((node, ENV.unit, Literal(obj["unit"], datatype=XSD.string)))
            if collection != "areas":
                for key in ("sources", "source_locations"):
                    for value in obj[key]:
                        graph.add((node, ENV[properties[key]], Literal(value, datatype=XSD.string)))
    validate_graph(graph)
    return graph


def load_environment_input(paths, *, input_format="turtle"):
    if input_format == "json":
        if len(paths) != 1:
            raise InputError("JSON 环境适配每次接受一个完整输入文件")
        graph = environment_to_rdf(read_json(paths[0]))
    elif input_format == "turtle":
        _, result = load_environment(paths, require_observations=False, require_parameters=False)
        if result.counts()["ERROR"]:
            raise ValidationError(dumps(result.items))
        graph = Graph()
        for path in paths:
            path = Path(path)
            graph.parse(data=path.read_bytes(), publicID=path.resolve().as_uri(), format="turtle")
    else:
        raise InputError("input_format 须为 json 或 turtle")
    return graph


def main(argv=None):
    from source_data.cli import JsonParser
    parser = JsonParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--format", choices=["turtle", "json"], default="turtle")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    try:
        args = parser.parse_args(argv)
        graph = load_environment_input(args.inputs, input_format=args.format)
        counts = {key: len(set(graph.subjects(RDF.type, cls))) for key, cls in
                  (("areas", ENV.Area), ("parameters", ENV.Parameter), ("observations", ENV.Observation))}
        result = report(**counts, independent_observations_missing=int(counts["observations"] == 0))
        write_outputs(args.output_dir, {"environment.ttl": graph.serialize(format="turtle"),
            "environment_report.json": json.dumps(result, ensure_ascii=False, indent=2)},
            overwrite=args.overwrite, protected=[*args.inputs, MAPPING])
        print(dumps(result))
        return 0
    except Exception as exc:
        result = report([str(exc)])
        print(dumps(result))
        print(str(exc), file=sys.stderr)
        return 1 if isinstance(exc, ValidationError) else 2


if __name__ == "__main__":
    raise SystemExit(main())
