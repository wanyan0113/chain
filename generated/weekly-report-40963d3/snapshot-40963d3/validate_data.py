"""Validate explicit RDF data against a small JSON profile (not a SHACL engine)."""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from urllib.parse import urlsplit

from rdflib import Graph, Literal, RDF, RDFS, OWL, URIRef
from rdflib.namespace import XSD

from check_ontology import (
    INTEGER_TYPES, Report, any_type_matches, datatype_ok, instances_of, is_datatype, local_name,
)


def iri(value):
    if not isinstance(value, str) or not urlsplit(value).scheme or any(c.isspace() for c in value):
        raise ValueError(f"需要完整 IRI：{value!r}")
    return URIRef(value)


def number(value):
    if isinstance(value, bool):
        raise ValueError("布尔值不能用作数值边界")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"无效数值：{value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"数值必须有限：{value!r}")
    return result


def check_profile(profile):
    """Reject malformed policies instead of silently ignoring misspelled keys."""
    if not isinstance(profile, dict) or set(profile) - {"schema_version", "rules", "acyclic_properties"}:
        raise ValueError("配置必须是对象，且只能包含 schema_version、rules、acyclic_properties")
    if type(profile.get("schema_version")) is not int or profile["schema_version"] != 1:
        raise ValueError("配置 schema_version 必须为 1")
    rules = profile.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("配置 rules 必须为非空列表")
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {"target_class", "require_instances", "fields"}:
            raise ValueError("规则字段只允许 target_class、require_instances、fields")
        iri(rule.get("target_class"))
        if type(rule.get("require_instances", True)) is not bool:
            raise ValueError("require_instances 必须为布尔值")
        if not isinstance(rule.get("fields"), list) or not rule["fields"]:
            raise ValueError("规则 fields 必须为非空列表")
        for field in rule["fields"]:
            if not isinstance(field, dict) or set(field) - {
                "path", "min_count", "max_count", "datatype", "value_class", "min_value", "max_value"
            }:
                raise ValueError("字段存在未知配置项")
            iri(field.get("path"))
            for key in ("min_count", "max_count"):
                if key in field and (type(field[key]) is not int or field[key] < 0):
                    raise ValueError(f"{key} 必须为非负整数")
            if field.get("min_count", 0) > field.get("max_count", float("inf")):
                raise ValueError("min_count 不能大于 max_count")
            if "datatype" in field:
                if not is_datatype(iri(field["datatype"])):
                    raise ValueError("datatype 需要已支持的 RDF/XSD 数据类型 IRI")
            if "value_class" in field:
                iri(field["value_class"])
            if "datatype" in field and "value_class" in field:
                raise ValueError("datatype 与 value_class 不能同时使用")
            for key in ("min_value", "max_value"):
                if key in field:
                    number(field[key])
                    if "datatype" not in field:
                        raise ValueError("数值边界必须与 datatype 一起使用")
                    if iri(field["datatype"]) not in INTEGER_TYPES | {XSD.decimal, XSD.float, XSD.double}:
                        raise ValueError("数值边界只适用于数值数据类型")
            if "min_value" in field and "max_value" in field:
                if number(field["min_value"]) > number(field["max_value"]):
                    raise ValueError("min_value 不能大于 max_value")
    paths = profile.get("acyclic_properties", [])
    if not isinstance(paths, list):
        raise ValueError("acyclic_properties 必须为列表")
    for path in paths:
        iri(path)


def validate(graph, profile):
    """Closed-world field checks; only named subclass inheritance is followed."""
    check_profile(profile)
    report = Report()
    checked = set()
    for rule in profile["rules"]:
        target = iri(rule["target_class"])
        if (target, RDF.type, OWL.Class) not in graph and (target, RDF.type, RDFS.Class) not in graph:
            report.error("PROFILE", f"目标类未声明：{target}")
            continue
        subjects = instances_of(graph, target)
        if not subjects and rule.get("require_instances", True):
            report.error("NO_TARGET", f"没有找到 {local_name(target)} 的显式实例或子类实例")
        checked.update(subjects)
        for field in rule["fields"]:
            prop = iri(field["path"])
            if not any((prop, RDF.type, kind) in graph for kind in (OWL.ObjectProperty, OWL.DatatypeProperty, RDF.Property)):
                report.error("PROFILE", f"属性未声明：{prop}")
                continue
            expected = iri(field["value_class"]) if "value_class" in field else None
            if expected is not None and not any((expected, RDF.type, kind) in graph for kind in (OWL.Class, RDFS.Class)):
                report.error("PROFILE", f"值类型类未声明：{expected}")
                continue
            for subject in sorted(subjects, key=str):
                values = list(graph.objects(subject, prop))
                location = f"{local_name(subject)}.{local_name(prop)}"
                if len(values) < field.get("min_count", 0):
                    report.error("MIN_COUNT", f"{location} 值数量 {len(values)} 少于 {field['min_count']}")
                if "max_count" in field and len(values) > field["max_count"]:
                    report.error("MAX_COUNT", f"{location} 值数量 {len(values)} 超过 {field['max_count']}")
                for value in values:
                    if expected is not None and (isinstance(value, Literal) or not any_type_matches(graph, value, expected)):
                        report.error("VALUE_CLASS", f"{location} 的值 {value} 缺少要求的显式类/子类类型")
                    if "datatype" not in field:
                        continue
                    actual = (RDF.langString if value.language else (value.datatype or XSD.string)) if isinstance(value, Literal) else None
                    if actual is None or value.ill_typed or not datatype_ok(actual, [iri(field["datatype"])]):
                        report.error("DATATYPE", f"{location} 的值 {value} 不是有效的 {field['datatype']}")
                        continue
                    if "min_value" in field or "max_value" in field:
                        try:
                            numeric = number(value)
                        except ValueError:
                            report.error("NUMBER", f"{location} 的值必须是有限数值")
                            continue
                        if "min_value" in field and numeric < number(field["min_value"]):
                            report.error("MIN_VALUE", f"{location}={value} 小于 {field['min_value']}")
                        if "max_value" in field and numeric > number(field["max_value"]):
                            report.error("MAX_VALUE", f"{location}={value} 大于 {field['max_value']}")

    # Kahn elimination detects cycles without recursion limits on long dependency paths.
    for path in profile.get("acyclic_properties", []):
        prop = iri(path)
        if (prop, RDF.type, OWL.ObjectProperty) not in graph and (prop, RDF.type, RDF.Property) not in graph:
            report.error("PROFILE", f"待检查依赖属性未声明：{prop}")
            continue
        edges = list(graph.subject_objects(prop))
        nodes = {node for edge in edges for node in edge}
        outgoing = {node: [] for node in nodes}
        degree = {node: 0 for node in nodes}
        for start, end in edges:
            outgoing[start].append(end)
            degree[end] += 1
            if isinstance(end, Literal):
                report.error("NODE_KIND", f"{local_name(prop)} 的依赖对象不能是字面量：{end}")
        queue = [node for node in nodes if degree[node] == 0]
        removed = 0
        while queue:
            current = queue.pop()
            removed += 1
            for successor in outgoing[current]:
                degree[successor] -= 1
                if degree[successor] == 0:
                    queue.append(successor)
        if removed != len(nodes):
            report.error("CYCLE", f"{local_name(prop)} 存在自环或依赖环")
    report.info("DATA", f"已检查 {len(checked)} 个实例；采用显式数据与具名子类继承，未执行 OWL 推理")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--schema", type=Path, action="append", default=[])
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)
    inputs = [args.data, args.profile, *args.schema]
    if args.json_output and args.json_output.resolve() in {path.resolve() for path in inputs}:
        parser.error("输出报告不能覆盖输入数据、模式或配置")
    try:
        profile = json.loads(args.profile.read_text(encoding="utf-8"))
        check_profile(profile)
        graph = Graph()
        for path in [args.data, *args.schema]:
            graph.parse(data=path.read_bytes(), publicID=path.resolve().as_uri(), format="turtle")
        report = validate(graph, profile)
    except Exception as exc:
        print(f"读取或配置错误：{exc}")
        return 2
    report.print()
    status = int(report.counts()["ERROR"] > 0)
    if args.json_output:
        payload = report.to_dict(args.data, status)
        payload.update(profile=str(args.profile.resolve()), schema=[str(p.resolve()) for p in args.schema])
        payload["scope"] = "独立 JSON 配置的数据校验；显式 RDF 数据与具名子类继承；不是 SHACL 引擎或 OWL 推理器"
        try:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"报告写入失败：{exc}")
            return 2
    return status


if __name__ == "__main__":
    raise SystemExit(main())
