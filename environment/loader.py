"""Load, validate and query environment observations; no domain inference."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import XSD

from check_ontology import INTEGER_TYPES, instances_of
from validate_data import iri, number, validate

ENV = Namespace("urn:environment:")
MODULE_DIR = Path(__file__).resolve().parent
NUMERIC = INTEGER_TYPES | {XSD.decimal, XSD.float, XSD.double}
SUPPORTED = NUMERIC | {XSD.string, XSD.boolean, XSD.dateTime}


def timestamp(value: str) -> datetime:
    """Return a timezone-aware UTC instant; never guess the source timezone."""
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("观测和查询时间必须包含时区")
    return result.astimezone(timezone.utc)


def load_environment(paths):
    """Load local Turtle inputs and return (graph, validation report)."""
    graph = Graph()
    graph.parse(data=(MODULE_DIR / "ontology.ttl").read_bytes(), format="turtle")
    for path in paths:
        path = Path(path)
        graph.parse(data=path.read_bytes(), publicID=path.resolve().as_uri(), format="turtle")
    profile = json.loads((MODULE_DIR / "profile.json").read_text(encoding="utf-8"))
    report = validate(graph, profile)
    parameters = instances_of(graph, ENV.Parameter)
    observations = instances_of(graph, ENV.Observation)
    areas = instances_of(graph, ENV.Area)
    # Catch untyped/orphan records that class-targeted validation cannot see.
    expected_subjects = {
        ENV.parameter: observations, ENV.value: observations,
        ENV.observedAt: observations, ENV.area: observations,
        ENV.definition: parameters, ENV.unit: parameters,
        ENV.valueDatatype: parameters,
        ENV.name: parameters | areas, ENV.source: parameters | observations,
    }
    for subject, prop, value in graph:
        if str(prop).startswith(str(ENV)) and prop not in expected_subjects:
            report.error("ENV_FIELD", f"未定义的环境字段：{prop}")
    for prop, expected in expected_subjects.items():
        for subject in graph.subjects(prop, None):
            if subject not in expected:
                report.error("ENV_TYPE", f"{subject} 使用 {prop} 但缺少对应记录类型")
    for subject in parameters | observations | areas:
        if not isinstance(subject, URIRef):
            report.error("ENV_ID", f"记录需要稳定 IRI，不能使用空白节点：{subject}")
    for prop in (ENV.name, ENV.definition, ENV.source, ENV.unit, ENV.value):
        for subject, value in graph.subject_objects(prop):
            if isinstance(value, Literal) and not str(value).strip():
                report.error("ENV_EMPTY", f"{subject} 的 {prop} 不能为空")
    for parameter in parameters:
        datatype = graph.value(parameter, ENV.valueDatatype)
        if datatype not in SUPPORTED:
            report.error("ENV_DATATYPE", f"{parameter} 需要受支持的 XSD 值类型")
        if datatype in NUMERIC and not list(graph.objects(parameter, ENV.unit)):
            report.error("ENV_UNIT", f"数值参数 {parameter} 缺少单位，无量纲请使用 1")
    for observation in observations:
        parameter = graph.value(observation, ENV.parameter)
        expected = graph.value(parameter, ENV.valueDatatype) if parameter is not None else None
        for value in graph.objects(observation, ENV.value):
            actual = (value.datatype or XSD.string) if isinstance(value, Literal) and not value.language else None
            if actual != expected or expected not in SUPPORTED or value.ill_typed:
                report.error("ENV_VALUE", f"{observation} 的值类型与参数定义不一致或字面量无效")
                continue
            try:
                if expected in NUMERIC:
                    number(value)
                elif expected == XSD.dateTime:
                    timestamp(str(value))
            except ValueError as exc:
                report.error("ENV_VALUE", f"{observation}：{exc}")
        for value in graph.objects(observation, ENV.observedAt):
            try:
                timestamp(str(value))
            except ValueError as exc:
                report.error("ENV_TIME", f"{observation}：{exc}")
    return graph, report


def query_observations(graph, *, parameter=None, area=None, start=None, end=None):
    """Query a validated graph; time boundaries inclusive, area matched by IRI."""
    for bound in (start, end):
        if bound is not None and (
            not isinstance(bound, datetime) or bound.tzinfo is None or bound.utcoffset() is None
        ):
            raise ValueError("查询时间必须是带时区的 datetime")
    for identifier in (parameter, area):
        if identifier is not None:
            if not isinstance(identifier, URIRef):
                raise ValueError("参数和区域过滤必须使用完整 IRI 的 URIRef")
            iri(str(identifier))
    if start is not None and end is not None and start > end:
        raise ValueError("起始时间不能晚于结束时间")
    rows = []
    for observation in instances_of(graph, ENV.Observation):
        param = graph.value(observation, ENV.parameter)
        region = graph.value(observation, ENV.area)
        observed_at = graph.value(observation, ENV.observedAt)
        instant = timestamp(str(observed_at))
        if parameter is not None and param != parameter:
            continue
        if area is not None and region != area:
            continue
        if (start is not None and instant < start) or (end is not None and instant > end):
            continue
        value = graph.value(observation, ENV.value)
        unit = graph.value(param, ENV.unit)
        rows.append({
            "id": str(observation), "parameter": str(param),
            "parameter_name": str(graph.value(param, ENV.name)),
            "value": str(value), "datatype": str(value.datatype or XSD.string),
            "unit": str(unit) if unit is not None else None,
            "observed_at": str(observed_at), "area": str(region),
            "sources": sorted(str(item) for item in graph.objects(observation, ENV.source)),
        })
    return sorted(rows, key=lambda row: (timestamp(row["observed_at"]), row["id"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", nargs="+", type=Path, help="真实参数定义和观测数据 TTL，可分文件")
    parser.add_argument("--parameter", type=iri, help="参数完整 IRI")
    parser.add_argument("--area", type=iri, help="区域完整 IRI")
    parser.add_argument("--from-time", type=timestamp)
    parser.add_argument("--to-time", type=timestamp)
    args = parser.parse_args(argv)
    try:
        graph, report = load_environment(args.data)
        for level, code, message in report.items:
            print(f"[{level}] {code}: {message}", file=sys.stderr)
        if report.counts()["ERROR"]:
            return 1
        rows = query_observations(graph, parameter=args.parameter, area=args.area,
                                  start=args.from_time, end=args.to_time)
    except Exception as exc:
        print(f"读取或查询错误：{exc}", file=sys.stderr)
        return 2
    print(json.dumps({"count": len(rows), "observations": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
