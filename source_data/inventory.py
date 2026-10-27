"""Describe local RDF declarations and JSON fields without creating mappings."""
from __future__ import annotations

import json
from pathlib import Path

from rdflib import BNode, Graph, RDF, RDFS, URIRef
from rdflib.namespace import OWL

from .common import read_json, write_outputs


def ontology_inventory(path):
    graph = Graph().parse(data=Path(path).read_bytes(), format="turtle")
    kinds = [("classes", OWL.Class), ("object_properties", OWL.ObjectProperty),
             ("datatype_properties", OWL.DatatypeProperty),
             ("annotation_properties", OWL.AnnotationProperty), ("rdf_properties", RDF.Property)]
    def term(value):
        if isinstance(value, BNode):
            # Do not mistake an anonymous union/restriction for an absent declaration.
            return "匿名表达式（详见原 TTL）"
        if isinstance(value, URIRef):
            return graph.namespace_manager.normalizeUri(value)
        return str(value)
    groups = {}
    for category, kind in kinds:
        rows = []
        for subject in sorted(set(graph.subjects(RDF.type, kind)), key=str):
            if not isinstance(subject, URIRef):
                continue
            rows.append({"iri": str(subject), "term": term(subject),
                "labels": sorted(str(v) for v in graph.objects(subject, RDFS.label)),
                "domains": sorted(term(v) for v in graph.objects(subject, RDFS.domain)),
                "ranges": sorted(term(v) for v in graph.objects(subject, RDFS.range)),
                "parents": sorted(term(v) for v in graph.objects(subject, RDFS.subClassOf)),
                "comments": sorted(str(v) for v in graph.objects(subject, RDFS.comment))})
        groups[category] = rows
    return {"path": str(path).replace("\\", "/"), "counts": {k: len(v) for k, v in groups.items()}, **groups}


def project_inventory(root):
    root = Path(root)
    paths = ["killweb_ontology.ttl", "environment/ontology.ttl", "source_data/ontology.ttl",
             "integration/environment_mapping.ttl"]
    modules = []
    for path in paths:
        module = ontology_inventory(root / path)
        module["path"] = path
        modules.append(module)
    schema = read_json(root / "source_data/schema.json")
    return {"scope": "仅清点现有声明；不提供来源到业务属性的对应，不生成实例或执行规则。",
            "modules": modules,
            "source_json_fields": {key: list(value.get("properties", {})) for key, value in schema["$defs"].items()},
            "environment_profile": read_json(root / "environment/profile.json")}


def render_inventory(result):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    lines = ["# 现有模块、类与属性完整清单", "", result["scope"], "",
             "由实际 TTL 和 JSON schema 提取。只列显式具名类及属性；不把继承属性重复展开，不执行推理。",
             "domain/range 未声明不等于允许任意使用；应用约束还需结合 profile 和加载器。",
             "匿名表达式保留提示，完整结构见原 TTL；本清单不替代约束验证。", ""]
    for module in result["modules"]:
        lines.extend(["## " + module["path"], "", "声明计数：" + json.dumps(module["counts"], ensure_ascii=False), ""])
        for category, title in [("classes", "类"), ("object_properties", "对象属性"),
                                ("datatype_properties", "数据属性"), ("annotation_properties", "注释属性"),
                                ("rdf_properties", "一般 RDF 属性")]:
            lines.extend(["### " + title, "", "| 名称 | 标签 | 定义域 | 值域 | 父类 | 原有注释 |",
                          "| --- | --- | --- | --- | --- | --- |"])
            for row in module[category]:
                values = [row["term"], "；".join(row["labels"]), "；".join(row["domains"]),
                          "；".join(row["ranges"]), "；".join(row["parents"]), "；".join(row["comments"])]
                lines.append("| " + " | ".join(cell(v) for v in values) + " |")
            if not module[category]:
                lines.append("| 无 | | | | | |")
            lines.append("")
    lines.extend(["## 来源 JSON 字段（与 RDF 属性分开）", "", "| 对象 | 全部字段 |", "| --- | --- |"])
    for key, fields in result["source_json_fields"].items():
        lines.append("| " + cell(key) + " | " + cell("、".join(fields)) + " |")
    lines.extend(["", "## 环境应用层字段约束", "", "以下数量和类型要求来自 environment/profile.json；加载器还检查非空值、有限数值、单位、时区与稳定 IRI。", "",
                  "| 类 | 字段 IRI | 最少 | 最多 | 字面量类型 | 引用类型 |", "| --- | --- | --- | --- | --- | --- |"])
    for rule in result["environment_profile"]["rules"]:
        for field in rule["fields"]:
            lines.append("| " + " | ".join(cell(v) for v in [rule["target_class"], field["path"],
                field.get("min_count", 0), field.get("max_count", "未设上限"), field.get("datatype", ""), field.get("value_class", "")]) + " |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    result = project_inventory(root)
    directory = root / "generated/module_inventory"
    write_outputs(directory, {"inventory.json": json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                              "inventory.md": render_inventory(result)}, overwrite=args.overwrite)
    print(json.dumps({"status": "valid", "modules": [{"path": m["path"], **m["counts"]} for m in result["modules"]]}, ensure_ascii=False))
