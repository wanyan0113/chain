#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""杀伤网本体第一轮结构审查。

用法：
    python -m pip install rdflib
    python check_ontology.py
    python check_ontology.py killweb_ontology.ttl
    python check_ontology.py --fail-on-warn killweb_ontology.ttl

边界：
    - 本脚本检查语法、声明、domain/range、restriction、inverse、
      functional、disjointness，以及本项目关心的链结构和快照一致性。
    - 它不是 OWL 2 DL 推理器。一致性、unsatisfiable 类和推理后的类层次，
      仍需在 Protégé 中用 HermiT 检查。
    - OWL 采用开放世界假设：domain/range 和 someValuesFrom 首先是推理规则，
      不是数据库约束。本脚本把“缺少显式类型”等记为 WARN，而不是 ERROR。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

try:
    from rdflib import BNode, Graph, Literal, RDF, RDFS, OWL, URIRef
    from rdflib.collection import Collection
    from rdflib.namespace import XSD
except ImportError:
    sys.exit("缺少 rdflib。请先运行：python -m pip install rdflib")


ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"


class Report:
    def __init__(self) -> None:
        self.items = []

    def add(self, level: str, code: str, message: str) -> None:
        self.items.append((level, code, message))

    def error(self, code: str, message: str) -> None:
        self.add(ERROR, code, message)

    def warn(self, code: str, message: str) -> None:
        self.add(WARN, code, message)

    def info(self, code: str, message: str) -> None:
        self.add(INFO, code, message)

    def counts(self):
        result = {ERROR: 0, WARN: 0, INFO: 0}
        for level, _, _ in self.items:
            result[level] += 1
        return result

    def print(self) -> None:
        order = {ERROR: 0, WARN: 1, INFO: 2}
        for level, code, message in sorted(
            self.items, key=lambda item: (order[item[0]], item[1], item[2])
        ):
            print(f"[{level}] {code}: {message}")

    def to_dict(self, path: Path, exit_code: int) -> dict:
        order = {ERROR: 0, WARN: 1, INFO: 2}
        return {
            "schema_version": 1,
            "file": str(path.resolve()),
            "exit_code": exit_code,
            "counts": self.counts(),
            "issues": [
                {"level": level, "code": code, "message": message}
                for level, code, message in sorted(
                    self.items, key=lambda item: (order[item[0]], item[1], item[2])
                )
            ],
            "scope": "静态结构检查；未执行完整 OWL 逻辑一致性推理，也未自动加载 owl:imports",
        }


def local_name(term) -> str:
    if isinstance(term, URIRef):
        text = str(term)
        if "#" in text:
            return text.rsplit("#", 1)[1]
        return text.rstrip("/").rsplit("/", 1)[-1]
    if isinstance(term, BNode):
        return f"_:{term}"
    return str(term)


def is_datatype(term) -> bool:
    if not isinstance(term, URIRef):
        return False
    if term in {RDFS.Literal, OWL.real, OWL.rational}:
        return True
    return str(term).startswith(str(XSD))


def find_named(graph: Graph, name: str):
    candidates = set()
    candidates |= set(graph.subjects(RDF.type, OWL.Class))
    candidates |= set(graph.subjects(RDF.type, OWL.ObjectProperty))
    candidates |= set(graph.subjects(RDF.type, OWL.DatatypeProperty))
    for candidate in candidates:
        if isinstance(candidate, URIRef) and local_name(candidate) == name:
            return candidate
    return None


def superclasses(graph: Graph, cls) -> set:
    result = set()
    stack = [cls]
    while stack:
        current = stack.pop()
        for parent in graph.objects(current, RDFS.subClassOf):
            if isinstance(parent, BNode):
                continue
            if parent not in result:
                result.add(parent)
                stack.append(parent)
    return result


def subclasses(graph: Graph, cls) -> set:
    result = {cls}
    stack = [cls]
    while stack:
        current = stack.pop()
        for child in graph.subjects(RDFS.subClassOf, current):
            if isinstance(child, URIRef) and child not in result:
                result.add(child)
                stack.append(child)
    return result


def instances_of(graph: Graph, cls) -> set:
    result = set()
    if cls is None:
        return result
    for type_ in subclasses(graph, cls):
        result |= set(graph.subjects(RDF.type, type_))
    return result


def type_matches(graph: Graph, actual, expected) -> bool:
    if actual == expected:
        return True
    if expected in {OWL.Thing, RDFS.Resource}:
        return True
    if not isinstance(actual, URIRef) or not isinstance(expected, URIRef):
        return False
    return expected in superclasses(graph, actual)


def explicit_types(graph: Graph, subject) -> set:
    return set(graph.objects(subject, RDF.type))


def any_type_matches(graph: Graph, subject, expected) -> bool:
    if expected is None:
        return False
    return any(type_matches(graph, actual, expected) for actual in explicit_types(graph, subject))


def collect_declarations(graph: Graph):
    classes = set(graph.subjects(RDF.type, OWL.Class))
    obj_props = set(graph.subjects(RDF.type, OWL.ObjectProperty))
    data_props = set(graph.subjects(RDF.type, OWL.DatatypeProperty))
    meta_types = {
        OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty,
        OWL.Ontology, OWL.Restriction, OWL.AllDisjointClasses,
        OWL.FunctionalProperty, OWL.InverseFunctionalProperty, OWL.TransitiveProperty,
        OWL.SymmetricProperty, OWL.AsymmetricProperty, OWL.IrreflexiveProperty,
        OWL.ReflexiveProperty, OWL.DeprecatedClass, OWL.DeprecatedProperty,
    }
    individuals = {
        subject for subject, type_ in graph.subject_objects(RDF.type)
        if isinstance(subject, URIRef) and type_ not in meta_types
    }

    for predicate in (RDFS.subClassOf, OWL.equivalentClass, OWL.disjointWith):
        for subject, obj in graph.subject_objects(predicate):
            if isinstance(subject, URIRef):
                classes.add(subject)
            if isinstance(obj, URIRef):
                classes.add(obj)

    for all_disjoint in graph.subjects(RDF.type, OWL.AllDisjointClasses):
        for members in graph.objects(all_disjoint, OWL.members):
            try:
                for member in Collection(graph, members):
                    if isinstance(member, URIRef):
                        classes.add(member)
            except Exception:
                pass

    return classes, obj_props, data_props, individuals


def check_header(graph: Graph, report: Report) -> None:
    ontologies = list(graph.subjects(RDF.type, OWL.Ontology))
    if not ontologies:
        report.warn("HEADER", "没有找到 owl:Ontology 声明")
    else:
        report.info("HEADER", f"owl:Ontology 声明 {len(ontologies)} 个")
    for ontology in ontologies:
        if not list(graph.objects(ontology, RDFS.label)):
            report.warn("HEADER", f"本体 {local_name(ontology)} 没有 rdfs:label")
        if not list(graph.objects(ontology, OWL.versionInfo)):
            report.warn("HEADER", f"本体 {local_name(ontology)} 没有 owl:versionInfo")
        if list(graph.objects(ontology, OWL.imports)):
            report.warn("HEADER", "存在 owl:imports；本脚本未解析导入的本体，相关声明可能被误报")


def check_declarations(graph: Graph, classes, obj_props, data_props, report: Report) -> None:
    overlap = set(obj_props) & set(data_props)
    for prop in sorted(overlap, key=str):
        report.error("DECL", f"{local_name(prop)} 同时声明为对象属性和数据属性")

    class_prop_overlap = set(classes) & (set(obj_props) | set(data_props))
    for term in sorted(class_prop_overlap, key=str):
        report.error("DECL", f"{local_name(term)} 同时声明为类和属性")

    for prop in sorted(set(obj_props) | set(data_props), key=str):
        if not list(graph.objects(prop, RDFS.label)):
            report.warn("LABEL", f"属性 {local_name(prop)} 没有 rdfs:label")

    for cls in sorted(classes, key=str):
        if isinstance(cls, BNode):
            continue
        if not list(graph.objects(cls, RDFS.label)):
            report.warn("LABEL", f"类 {local_name(cls)} 没有 rdfs:label")

def check_domain_range(graph: Graph, classes, obj_props, data_props, report: Report) -> None:
    class_like = set(classes) | {OWL.Thing, RDFS.Resource}

    for prop in sorted(obj_props, key=str):
        for domain in graph.objects(prop, RDFS.domain):
            if isinstance(domain, BNode):
                report.info("DOMAIN", f"{local_name(prop)} 的 domain 是匿名类表达式，未展开检查")
            elif domain not in class_like:
                report.error("DOMAIN", f"对象属性 {local_name(prop)} 的 domain 不是已声明的类：{local_name(domain)}")
        for range_ in graph.objects(prop, RDFS.range):
            if isinstance(range_, BNode):
                report.info("RANGE", f"{local_name(prop)} 的 range 是匿名类表达式，未展开检查")
            elif is_datatype(range_):
                report.error("RANGE", f"对象属性 {local_name(prop)} 的 range 不能是数据类型：{local_name(range_)}")
            elif range_ not in class_like:
                report.error("RANGE", f"对象属性 {local_name(prop)} 的 range 不是已声明的类：{local_name(range_)}")

    for prop in sorted(data_props, key=str):
        for domain in graph.objects(prop, RDFS.domain):
            if isinstance(domain, BNode):
                report.info("DOMAIN", f"{local_name(prop)} 的 domain 是匿名类表达式，未展开检查")
            elif domain not in class_like:
                report.error("DOMAIN", f"数据属性 {local_name(prop)} 的 domain 不是已声明的类：{local_name(domain)}")
        for range_ in graph.objects(prop, RDFS.range):
            if isinstance(range_, BNode):
                report.info("RANGE", f"{local_name(prop)} 的 range 是匿名表达式，未展开检查")
            elif not is_datatype(range_):
                report.error("RANGE", f"数据属性 {local_name(prop)} 的 range 不是数据类型：{local_name(range_)}")


INTEGER_TYPES = {
    XSD.integer, XSD.int, XSD.long, XSD.short, XSD.byte,
    XSD.nonNegativeInteger, XSD.nonPositiveInteger,
    XSD.negativeInteger, XSD.positiveInteger,
    XSD.unsignedLong, XSD.unsignedInt, XSD.unsignedShort, XSD.unsignedByte,
}


INTEGER_PARENTS = {
    XSD.integer: XSD.decimal,
    XSD.long: XSD.integer, XSD.int: XSD.long,
    XSD.short: XSD.int, XSD.byte: XSD.short,
    XSD.nonPositiveInteger: XSD.integer,
    XSD.negativeInteger: XSD.nonPositiveInteger,
    XSD.nonNegativeInteger: XSD.integer,
    XSD.positiveInteger: XSD.nonNegativeInteger,
    XSD.unsignedLong: XSD.nonNegativeInteger,
    XSD.unsignedInt: XSD.unsignedLong,
    XSD.unsignedShort: XSD.unsignedInt,
    XSD.unsignedByte: XSD.unsignedShort,
}


def datatype_ok(actual, ranges) -> bool:
    compatible = {actual, RDFS.Literal}
    current = actual
    while current in INTEGER_PARENTS:
        current = INTEGER_PARENTS[current]
        compatible.add(current)
    return all(expected in compatible for expected in ranges)


def check_abox_types(graph: Graph, obj_props, data_props, report: Report) -> None:
    for prop in sorted(obj_props, key=str):
        domains = [d for d in graph.objects(prop, RDFS.domain) if isinstance(d, URIRef)]
        ranges = [r for r in graph.objects(prop, RDFS.range) if isinstance(r, URIRef)]
        for subject, obj in graph.subject_objects(prop):
            if isinstance(obj, Literal):
                report.error("ABOX", f"对象属性 {local_name(prop)} 的宾语是字面量：{local_name(subject)} -> {obj}")
                continue
            if not explicit_types(graph, subject):
                report.warn("ABOX", f"主语 {local_name(subject)} 没有显式类型；OWL 会按 domain 推理")
            elif domains and not all(any_type_matches(graph, subject, d) for d in domains):
                report.warn(
                    "ABOX",
                    f"主语 {local_name(subject)} 的显式类型与 {local_name(prop)} 的 domain 不匹配："
                    f"{[local_name(t) for t in explicit_types(graph, subject)]} vs {[local_name(d) for d in domains]}",
                )
            if isinstance(obj, (URIRef, BNode)):
                if not explicit_types(graph, obj):
                    report.warn("ABOX", f"宾语 {local_name(obj)} 没有显式类型；OWL 会按 range 推理")
                elif ranges and not all(any_type_matches(graph, obj, r) for r in ranges):
                    report.warn(
                        "ABOX",
                        f"宾语 {local_name(obj)} 的显式类型与 {local_name(prop)} 的 range 不匹配："
                        f"{[local_name(t) for t in explicit_types(graph, obj)]} vs {[local_name(r) for r in ranges]}",
                    )

    for prop in sorted(data_props, key=str):
        domains = [d for d in graph.objects(prop, RDFS.domain) if isinstance(d, URIRef)]
        ranges = [r for r in graph.objects(prop, RDFS.range) if is_datatype(r)]
        for subject, obj in graph.subject_objects(prop):
            if not isinstance(obj, Literal):
                report.error("ABOX", f"数据属性 {local_name(prop)} 的宾语不是字面量：{local_name(subject)} -> {obj}")
                continue
            if domains and not all(any_type_matches(graph, subject, d) for d in domains):
                report.warn("ABOX", f"主语 {local_name(subject)} 的显式类型与 {local_name(prop)} 的 domain 不匹配")
            if obj.ill_typed:
                report.error("LITERAL", f"{local_name(subject)} 的 {local_name(prop)} 含非法字面量：{obj.n3()}")
            if ranges:
                actual = RDF.langString if obj.language else (obj.datatype or XSD.string)
                if not datatype_ok(actual, ranges):
                    report.warn(
                        "ABOX",
                        f"字面量类型 {local_name(actual)} 与 {local_name(prop)} 的 range 不匹配："
                        f"{[local_name(r) for r in ranges]}",
                    )


def check_restrictions(graph: Graph, classes, obj_props, data_props, report: Report) -> None:
    for restriction in graph.subjects(RDF.type, OWL.Restriction):
        properties = list(graph.objects(restriction, OWL.onProperty))
        if not properties:
            report.error("RESTRICTION", "restriction 没有 owl:onProperty")
            continue
        for prop in properties:
            if not isinstance(prop, URIRef) or (prop not in obj_props and prop not in data_props):
                report.error("RESTRICTION", f"owl:onProperty 未声明为对象/数据属性：{local_name(prop)}")

        all_object = bool(properties) and all(p in obj_props for p in properties)
        all_data = bool(properties) and all(p in data_props for p in properties)
        for filler in graph.objects(restriction, OWL.someValuesFrom):
            if isinstance(filler, BNode):
                report.info("RESTRICTION", "someValuesFrom 的 filler 是匿名类表达式，未展开检查")
            elif all_object:
                if is_datatype(filler) or filler not in classes:
                    report.error("RESTRICTION", f"对象属性 restriction 的 filler 不是类：{local_name(filler)}")
            elif all_data:
                if not is_datatype(filler):
                    report.error("RESTRICTION", f"数据属性 restriction 的 filler 不是数据类型：{local_name(filler)}")
            else:
                report.warn("RESTRICTION", f"restriction 的属性种类混杂，无法判断 filler：{local_name(filler)}")

        for filler in graph.objects(restriction, OWL.onClass):
            if isinstance(filler, URIRef) and filler not in classes:
                report.error("RESTRICTION", f"owl:onClass 未声明为类：{local_name(filler)}")

        for predicate in (
            OWL.minQualifiedCardinality, OWL.maxQualifiedCardinality, OWL.qualifiedCardinality,
            OWL.minCardinality, OWL.maxCardinality, OWL.cardinality,
        ):
            for value in graph.objects(restriction, predicate):
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    report.error("RESTRICTION", f"{local_name(predicate)} 不是整数：{value}")
                    continue
                if number < 0:
                    report.error("RESTRICTION", f"{local_name(predicate)} 不能为负数：{number}")


def check_inverse_functional(graph: Graph, obj_props, data_props, report: Report) -> None:
    one_way = 0
    seen_pairs = set()
    for prop, inverse in graph.subject_objects(OWL.inverseOf):
        if not isinstance(prop, URIRef) or prop not in obj_props:
            report.error("INVERSE", f"owl:inverseOf 的主语不是对象属性：{local_name(prop)}")
            continue
        if not isinstance(inverse, URIRef) or inverse not in obj_props:
            report.error("INVERSE", f"owl:inverseOf 的宾语不是对象属性：{local_name(inverse)}")
            continue
        key = tuple(sorted((str(prop), str(inverse))))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        for subject, obj in graph.subject_objects(prop):
            if (obj, inverse, subject) not in graph:
                one_way += 1
        for subject, obj in graph.subject_objects(inverse):
            if (obj, prop, subject) not in graph:
                one_way += 1
    if one_way:
        report.info("INVERSE", f"{one_way} 条 inverse 断言是单向的；OWL 推理机会补齐，不必手工重复")

    for prop in sorted(set(obj_props) | set(data_props), key=str):
        if (prop, RDF.type, OWL.FunctionalProperty) not in graph:
            continue
        for subject in set(graph.subjects(prop, None)):
            values = set(graph.objects(subject, prop))
            if len(values) > 1:
                report.warn(
                    "FUNCTIONAL",
                    f"FunctionalProperty {local_name(prop)} 在 {local_name(subject)} 上有 {len(values)} 个不同值；"
                    "OWL 会合并或判不一致，需用 HermiT 确认",
                )


def check_disjointness(graph: Graph, classes, report: Report) -> None:
    pairs = set()
    for left, right in graph.subject_objects(OWL.disjointWith):
        if isinstance(left, URIRef) and isinstance(right, URIRef):
            pairs.add((left, right))
            pairs.add((right, left))
    for all_disjoint in graph.subjects(RDF.type, OWL.AllDisjointClasses):
        for members in graph.objects(all_disjoint, OWL.members):
            try:
                items = list(Collection(graph, members))
            except Exception:
                continue
            for index, left in enumerate(items):
                for right in items[index + 1:]:
                    if isinstance(left, URIRef) and isinstance(right, URIRef):
                        pairs.add((left, right))
                        pairs.add((right, left))
    if not pairs:
        return

    seen = set()
    for subject in set(graph.subjects(RDF.type, None)):
        types = [t for t in explicit_types(graph, subject) if isinstance(t, URIRef)]
        for index, left in enumerate(types):
            for right in types[index + 1:]:
                if (left, right) in pairs:
                    key = ("individual", subject, left, right)
                    if key not in seen:
                        seen.add(key)
                        report.error(
                            "DISJOINT",
                            f"个体 {local_name(subject)} 同时属于不相交类 {local_name(left)} 和 {local_name(right)}",
                        )

    for cls in classes:
        if isinstance(cls, BNode):
            continue
        parents = superclasses(graph, cls) | {cls}
        for left in parents:
            for right in parents:
                if left != right and (left, right) in pairs:
                    key = ("class", cls, left, right)
                    if key not in seen:
                        seen.add(key)
                        report.error(
                            "DISJOINT",
                            f"类 {local_name(cls)} 同时是 {local_name(left)} 和 {local_name(right)} 的子类，可能 unsatisfiable",
                        )

CORE_CLASSES = [
    "CombatMission", "BattlefieldSituation", "CombatCapability",
    "OperationalRequirement", "WeaponEquipment",
]

OODA_CLASSES = [
    "ReconMission", "JudgmentMission", "DecisionMission", "StrikeMission",
    "ReconCapability", "JudgmentCapability", "DecisionCapability", "StrikeCapability",
    "ReconEquipment", "JudgmentEquipment", "DecisionEquipment", "StrikeEquipment",
]

TABLE3_PROPERTIES = [
    "equipmentId", "equipmentCategory", "equipmentName", "equipmentShortName",
    "longitudeDeg", "latitudeDeg", "altitudeFt", "speedKn", "operationalStatus",
    "reconTargetType", "detectionRangeNm", "azimuthCoverageDeg", "elevationCoverageDeg",
    "maxSearchTargets", "detectionPrecision", "distanceResolutionM",
    "azimuthResolutionDeg", "elevationResolutionDeg", "reconIntervalS",
    "evaluationTimeS", "maxEvaluationTargets", "evaluationAreaKm2",
    "communicationBandwidthMbps", "communicationPowerW", "relayDelayMs",
    "strikeTargetType", "maxRangeNm", "minRangeNm", "damageValue", "damageRadiusM",
    "responseTimeS", "salvoSize", "reloadTimeS", "maxTargetSpeedKn",
    "flightSpeedKn", "climbSpeedKn", "minTargetAltitudeFt", "maxTargetAltitudeFt",
    "minLaunchAltitudeFt", "maxLaunchAltitudeFt", "flightAltitudeFt",
    "hitProbability", "damageRate", "cost",
]

TABLE5_PROPERTIES = [
    "missionName", "missionShortName", "missionCode", "missionType",
    "missionLocation", "missionTime", "missionMetrics", "missionTargetType",
    "missionTargetSpeedKn", "missionTargetAltitudeFt", "missionTargetLongitudeDeg",
    "missionTargetLatitudeDeg", "missionTargetSurvivability",
]


def check_paper_coverage(graph: Graph, report: Report) -> None:
    missing_core = [name for name in CORE_CLASSES if find_named(graph, name) is None]
    if missing_core:
        for name in missing_core:
            report.error("PAPER", f"缺少 1.1 核心类：{name}")
    else:
        report.info("PAPER", f"1.1 核心类 {len(CORE_CLASSES)}/{len(CORE_CLASSES)}")

    missing_ooda = [name for name in OODA_CLASSES if find_named(graph, name) is None]
    if missing_ooda:
        for name in missing_ooda:
            report.warn("PAPER", f"缺少 OODA 子类：{name}")
    else:
        report.info("PAPER", f"OODA 子类 {len(OODA_CLASSES)}/{len(OODA_CLASSES)}")

    missing_table3 = [name for name in TABLE3_PROPERTIES if find_named(graph, name) is None]
    if missing_table3:
        for name in missing_table3:
            report.warn("PAPER", f"缺少表 3 属性：{name}")
        report.info("PAPER", f"表 3 属性 {len(TABLE3_PROPERTIES) - len(missing_table3)}/{len(TABLE3_PROPERTIES)}")
    else:
        report.info("PAPER", f"表 3 属性 {len(TABLE3_PROPERTIES)}/{len(TABLE3_PROPERTIES)}")

    missing_table5 = [name for name in TABLE5_PROPERTIES if find_named(graph, name) is None]
    if missing_table5:
        for name in missing_table5:
            report.warn("PAPER", f"缺少表 5 属性：{name}")
        report.info("PAPER", f"表 5 属性 {len(TABLE5_PROPERTIES) - len(missing_table5)}/{len(TABLE5_PROPERTIES)}")
    else:
        report.info("PAPER", f"表 5 属性 {len(TABLE5_PROPERTIES)}/{len(TABLE5_PROPERTIES)}")


def check_project_rules(graph: Graph, report: Report) -> None:
    names = [
        "KillChain", "KillWeb", "ChainNode", "ReconChainNode", "CommandChainNode",
        "StrikeChainNode", "ChainRelation", "SerialRelation", "BranchRelation",
        "hasChainNode", "nodeOrder", "nextNode", "previousNode", "nodeEquipment",
        "chainTarget", "chainId", "hasChain", "hasRelation", "relatedChain",
        "fromChain", "toChain", "chainRelation", "hasTarget", "Target",
        "CombatMission", "ReconEquipment", "CommandControlEquipment",
        "StrikeEquipment", "OperationalRequirement",
        "requirementForMission", "requirementConstrainsCapability",
        "requirementConstrainsEquipment", "supportsMission", "satisfiesRequirement",
    ]
    term = {name: find_named(graph, name) for name in names}
    required = [
        "KillChain", "ChainNode", "hasChainNode", "nodeOrder", "nextNode",
        "nodeEquipment", "chainTarget",
    ]
    if any(term[name] is None for name in required):
        report.info("PROJECT", "未检测到完整的 KillChain/ChainNode 结构，跳过项目规则检查")
        return

    chains = instances_of(graph, term["KillChain"])
    membership = defaultdict(list)

    for chain in chains:
        nodes = list(graph.objects(chain, term["hasChainNode"]))
        if not nodes:
            report.error("CHAIN", f"链 {local_name(chain)} 没有 hasChainNode")
        if not list(graph.objects(chain, term["chainTarget"])):
            report.error("CHAIN", f"链 {local_name(chain)} 没有 chainTarget")
        for target in graph.objects(chain, term["chainTarget"]):
            if not any_type_matches(graph, target, term["Target"]):
                report.warn("CHAIN", f"链 {local_name(chain)} 的 chainTarget 不是 Target：{local_name(target)}")

        orders = {}
        node_data = {}
        for node in nodes:
            membership[node].append(chain)
            if not any_type_matches(graph, node, term["ChainNode"]):
                report.error("CHAIN", f"节点 {local_name(node)} 未声明为 ChainNode")

            equipment = list(graph.objects(node, term["nodeEquipment"]))
            if not equipment:
                report.error("CHAIN", f"节点 {local_name(node)} 没有 nodeEquipment")

            order_values = list(graph.objects(node, term["nodeOrder"]))
            order = None
            if len(order_values) != 1:
                report.error("CHAIN", f"节点 {local_name(node)} 应有且仅有一个 nodeOrder")
            else:
                try:
                    order = int(order_values[0])
                except (TypeError, ValueError):
                    report.error("CHAIN", f"节点 {local_name(node)} 的 nodeOrder 不是整数：{order_values[0]}")
                if order is not None:
                    if order in orders:
                        report.error("CHAIN", f"链 {local_name(chain)} 中 nodeOrder {order} 重复")
                    orders[order] = node

            roles = [
                role for role in ("ReconChainNode", "CommandChainNode", "StrikeChainNode")
                if any_type_matches(graph, node, term[role])
            ]
            if not roles:
                report.error("CHAIN", f"节点 {local_name(node)} 没有侦察/指控/打击角色类型")
            elif len(roles) > 1:
                report.warn("CHAIN", f"节点 {local_name(node)} 同时具有多个角色类型：{roles}")

            equipment_type_map = {
                "ReconChainNode": "ReconEquipment",
                "CommandChainNode": "CommandControlEquipment",
                "StrikeChainNode": "StrikeEquipment",
            }
            for role in roles:
                expected = term[equipment_type_map[role]]
                if expected is None:
                    continue
                for item in equipment:
                    if not any_type_matches(graph, item, expected):
                        report.warn(
                            "CHAIN",
                            f"节点 {local_name(node)} 的角色是 {role}，但装备 {local_name(item)} "
                            f"没有显式类型 {equipment_type_map[role]}",
                        )

            next_values = list(graph.objects(node, term["nextNode"]))
            if len(next_values) > 1:
                report.error("CHAIN", f"节点 {local_name(node)} 有多个 nextNode")
            previous_values = list(graph.objects(node, term["previousNode"])) if term["previousNode"] else []
            node_data[node] = {
                "order": order,
                "next": next_values,
                "previous": previous_values,
                "equipment": equipment,
                "roles": roles,
            }

        max_order = max(orders) if orders else None
        for node, data in node_data.items():
            order = data["order"]
            next_values = data["next"]
            if next_values:
                successor = next_values[0]
                if successor not in nodes:
                    report.error("CHAIN", f"节点 {local_name(node)} 的 nextNode 不在同一条链中：{local_name(successor)}")
                elif order is not None:
                    successor_orders = list(graph.objects(successor, term["nodeOrder"]))
                    if successor_orders:
                        try:
                            successor_order = int(successor_orders[0])
                        except (TypeError, ValueError):
                            successor_order = None
                        if successor_order is not None and successor_order != order + 1:
                            report.warn(
                                "CHAIN",
                                f"nextNode 与 nodeOrder 不一致：{local_name(node)}({order}) -> "
                                f"{local_name(successor)}({successor_order})",
                            )
            elif order is not None and max_order is not None and order != max_order:
                report.warn("CHAIN", f"非末节点 {local_name(node)} 没有 nextNode")

            if order == 1 and data["previous"]:
                report.warn("CHAIN", f"首节点 {local_name(node)} 不应有 previousNode")

        if orders:
            expected_orders = set(range(1, len(nodes) + 1))
            if set(orders) != expected_orders:
                report.warn(
                    "CHAIN",
                    f"链 {local_name(chain)} 的 nodeOrder 不是 1..{len(nodes)}：{sorted(orders)}",
                )

        role_presence = {
            role: any(any_type_matches(graph, node, term[role]) for node in nodes)
            for role in ("ReconChainNode", "CommandChainNode", "StrikeChainNode")
        }
        for role, present in role_presence.items():
            if not present:
                report.error("CHAIN", f"链 {local_name(chain)} 缺少 {role}")

        for start in nodes:
            seen = set()
            current = start
            while current is not None:
                if current in seen:
                    report.error("CHAIN", f"链 {local_name(chain)} 的 nextNode 存在环，起点 {local_name(start)}")
                    break
                seen.add(current)
                next_values = list(graph.objects(current, term["nextNode"]))
                current = next_values[0] if len(next_values) == 1 else None

    for node, chains_for_node in membership.items():
        if len(chains_for_node) > 1:
            report.warn(
                "CHAIN",
                f"节点 {local_name(node)} 同时属于多条链；nodeOrder 是节点级属性，"
                "可能无法表达不同链中的不同顺序",
            )
    report.info("PROJECT", f"链结构检查完成：{len(chains)} 条链，{len(membership)} 个节点")

    if term["ChainRelation"] is not None and term["relatedChain"] is not None:
        all_relations = instances_of(graph, term["ChainRelation"])
        for relation in all_relations:
            participants = set(graph.objects(relation, term["relatedChain"]))
            if len(participants) < 2:
                report.error("RELATION", f"链间关系 {local_name(relation)} 少于 2 条参与链")
            is_directed = (
                (term["SerialRelation"] is not None and (relation, RDF.type, term["SerialRelation"]) in graph)
                or (term["BranchRelation"] is not None and (relation, RDF.type, term["BranchRelation"]) in graph)
            )
            if is_directed and term["fromChain"] is not None and term["toChain"] is not None:
                from_values = list(graph.objects(relation, term["fromChain"]))
                to_values = list(graph.objects(relation, term["toChain"]))
                if len(from_values) != 1:
                    report.error("RELATION", f"有向关系 {local_name(relation)} 应有且仅有一个 fromChain")
                if len(to_values) != 1:
                    report.error("RELATION", f"有向关系 {local_name(relation)} 应有且仅有一个 toChain")
                if from_values and to_values and from_values[0] == to_values[0]:
                    report.error("RELATION", f"有向关系 {local_name(relation)} 的 fromChain 与 toChain 相同")
        report.info("RELATION", f"链间关系检查完成：{len(all_relations)} 个关系")

        if term["KillWeb"] is not None and term["hasChain"] is not None and term["hasRelation"] is not None:
            for web in instances_of(graph, term["KillWeb"]):
                web_chains = set(graph.objects(web, term["hasChain"]))
                web_relations = set(graph.objects(web, term["hasRelation"]))
                if not web_chains:
                    report.error("WEB", f"杀伤网 {local_name(web)} 没有 hasChain")
                for relation in web_relations:
                    for chain in graph.objects(relation, term["relatedChain"]):
                        if chain not in web_chains:
                            report.error(
                                "WEB",
                                f"关系 {local_name(relation)} 的参与链 {local_name(chain)} "
                                f"不在网 {local_name(web)} 的 hasChain 中",
                            )
                for mission in graph.objects(web, term["supportsMission"]):
                    if not any_type_matches(graph, mission, term["CombatMission"]):
                        report.warn("WEB", f"网 {local_name(web)} 的 supportsMission 不是 CombatMission")
                for requirement in graph.objects(web, term["satisfiesRequirement"]):
                    if not any_type_matches(graph, requirement, term["OperationalRequirement"]):
                        report.warn("WEB", f"网 {local_name(web)} 的 satisfiesRequirement 不是 OperationalRequirement")

    if term["OperationalRequirement"] is not None:
        capability_class = find_named(graph, "CombatCapability")
        equipment_class = find_named(graph, "WeaponEquipment")
        for requirement in instances_of(graph, term["OperationalRequirement"]):
            for mission in graph.objects(requirement, term["requirementForMission"]):
                if not any_type_matches(graph, mission, term["CombatMission"]):
                    report.warn("REQUIREMENT", f"需求 {local_name(requirement)} 的 requirementForMission 不是任务")
            for capability in graph.objects(requirement, term["requirementConstrainsCapability"]):
                if not any_type_matches(graph, capability, capability_class):
                    report.warn("REQUIREMENT", f"需求 {local_name(requirement)} 的 requirementConstrainsCapability 不是能力")
            for equipment in graph.objects(requirement, term["requirementConstrainsEquipment"]):
                if not any_type_matches(graph, equipment, equipment_class):
                    report.warn("REQUIREMENT", f"需求 {local_name(requirement)} 的 requirementConstrainsEquipment 不是装备")

    if term["CombatMission"] is not None and term["hasTarget"] is not None and term["Target"] is not None:
        pairs = [
            ("missionTargetType", "targetType"),
            ("missionTargetSpeedKn", "targetSpeedKn"),
            ("missionTargetAltitudeFt", "targetAltitudeFt"),
            ("missionTargetLongitudeDeg", "targetLongitudeDeg"),
            ("missionTargetLatitudeDeg", "targetLatitudeDeg"),
            ("missionTargetSurvivability", "targetSurvivability"),
        ]
        for mission in instances_of(graph, term["CombatMission"]):
            targets = list(graph.objects(mission, term["hasTarget"]))
            if not targets:
                continue
            target = targets[0]
            for mission_prop, target_prop in pairs:
                mission_term = find_named(graph, mission_prop)
                target_term = find_named(graph, target_prop)
                if mission_term is None or target_term is None:
                    continue
                mission_values = list(graph.objects(mission, mission_term))
                target_values = list(graph.objects(target, target_term))
                if mission_values and target_values and normalize(mission_values[0]) != normalize(target_values[0]):
                    report.warn(
                        "SNAPSHOT",
                        f"目标快照不一致：{local_name(mission)}.{mission_prop}={mission_values[0]} "
                        f"vs {local_name(target)}.{target_prop}={target_values[0]}",
                    )


def normalize(value):
    if isinstance(value, Literal):
        try:
            if value.datatype in (XSD.integer, XSD.decimal, XSD.float, XSD.double):
                return float(value)
        except (TypeError, ValueError):
            pass
    return str(value)

def analyze_graph(graph: Graph, *, generic: bool = False) -> Report:
    report = Report()
    report.info("PARSE", f"语法解析通过，共 {len(graph)} 个三元组")

    classes, obj_props, data_props, individuals = collect_declarations(graph)
    report.info(
        "COUNT",
        f"类 {len(classes)}，对象属性 {len(obj_props)}，数据属性 {len(data_props)}，"
        f"命名个体 {len(individuals)}",
    )

    check_header(graph, report)
    check_declarations(graph, classes, obj_props, data_props, report)
    check_domain_range(graph, classes, obj_props, data_props, report)
    check_abox_types(graph, obj_props, data_props, report)
    check_restrictions(graph, classes, obj_props, data_props, report)
    check_inverse_functional(graph, obj_props, data_props, report)
    check_disjointness(graph, classes, report)
    if not generic:
        check_paper_coverage(graph, report)
        check_project_rules(graph, report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RDF/OWL 本体静态结构审查")
    parser.add_argument("path", nargs="?", default="killweb_ontology.ttl", help="本地 TTL 文件路径")
    parser.add_argument("--fail-on-warn", action="store_true", help="把 WARN 也视为失败")
    parser.add_argument("--generic", action="store_true", help="只执行通用 RDF/OWL 检查")
    parser.add_argument("--json-output", type=Path, help="另存 UTF-8 JSON 检查报告")
    args = parser.parse_args(argv)

    path = Path(args.path)
    if args.json_output and args.json_output.resolve() == path.resolve():
        print("报告路径不能与输入文件相同", file=sys.stderr)
        return 2

    report = Report()
    exit_code = 0
    try:
        source = path.read_bytes()
    except OSError as exc:
        report.error("INPUT", f"无法读取本地文件：{exc}")
        exit_code = 2
    else:
        graph = Graph()
        try:
            graph.parse(data=source, publicID=path.resolve().as_uri(), format="turtle")
        except Exception as exc:
            report.error("PARSE", str(exc))
            exit_code = 1
        else:
            report = analyze_graph(graph, generic=args.generic)
            counts = report.counts()
            exit_code = int(counts[ERROR] > 0 or (args.fail_on_warn and counts[WARN] > 0))

    print("\n=== RDF/OWL 本体静态结构审查报告 ===")
    print(f"文件：{path}")
    report.print()
    counts = report.counts()
    print(f"\n汇总：ERROR={counts[ERROR]}，WARN={counts[WARN]}，INFO={counts[INFO]}")
    print("提醒：本脚本不能替代 HermiT；请在 Protégé 中执行 Reasoner > HermiT > Start reasoner。")

    if args.json_output:
        try:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(
                json.dumps(report.to_dict(path, exit_code), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"无法写入 JSON 报告：{exc}", file=sys.stderr)
            return 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
