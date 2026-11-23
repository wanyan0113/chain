"""Independent local Turtle loader with lossless payload/reference validation."""
from __future__ import annotations

import json
from pathlib import Path

from rdflib import Graph, Literal, Namespace, RDF, URIRef
from rdflib.namespace import XSD

from .common import COLLECTIONS, MODULE, InputError, ValidationError, dumps, report, source_iri
from .validate import validate_bundle

SRC = Namespace("urn:source-data:")
REFERENCES = {
    "materials": {}, "tables": {"material_ids": "materials"},
    "fields": {"table_id": "tables"}, "records": {"table_id": "tables"},
    "values": {"record_id": "records", "field_id": "fields", "issue_ids": "issues", "normalization_basis": "statements"},
    "issues": {"record_ids": "records", "field_ids": "fields", "value_ids": "values", "table_ids": "tables",
               "statement_ids": "statements", "gap_ids": "gaps", "material_ids": "materials", "basis": "statements"},
    "gaps": {}, "statements": {"material_ids": "materials"}}


def bundle_graph(bundle):
    """Canonical instance graph, without ontology declarations."""
    result = validate_bundle(bundle)
    if result["counts"]["ERROR"]:
        raise ValidationError("; ".join(result["errors"]))
    graph = Graph()
    graph.bind("src", SRC)
    bundle_node = URIRef(source_iri(bundle["metadata"]["mapping"]["namespace"] + "/bundle"))
    graph.add((bundle_node, RDF.type, SRC.Bundle))
    graph.add((bundle_node, SRC.payload, Literal(dumps({"format_version": bundle["format_version"],
                                                      "metadata": bundle["metadata"]}), datatype=XSD.string)))
    for collection, cls in COLLECTIONS.items():
        for obj in bundle[collection]:
            node = URIRef(source_iri(obj["id"]))
            graph.add((bundle_node, SRC.contains, node))
            graph.add((node, RDF.type, SRC[cls]))
            graph.add((node, SRC.id, Literal(obj["id"], datatype=XSD.string)))
            graph.add((node, SRC.payload, Literal(dumps(obj), datatype=XSD.string)))
            for key in REFERENCES[collection]:
                for reference in obj[key] if isinstance(obj[key], list) else [obj[key]]:
                    graph.add((node, SRC[key], URIRef(source_iri(reference))))
    return graph


def bundle_from_graph(graph):
    bundles = list(graph.subjects(RDF.type, SRC.Bundle))
    if len(bundles) != 1:
        raise ValidationError("每次来源加载必须恰好包含一个完整来源包")
    def payload(node):
        values = list(graph.objects(node, SRC.payload))
        if len(values) != 1 or not isinstance(values[0], Literal) or values[0].language or values[0].datatype not in (None, XSD.string):
            raise ValidationError(f"来源 JSON payload 数量或字面量类型错误：{node}")
        try:
            value = json.loads(str(values[0]))
            if not isinstance(value, dict):
                raise ValueError("payload 必须为对象")
            return value
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"无效来源 payload：{node}: {exc}") from exc
    head = payload(bundles[0])
    if set(head) != {"format_version", "metadata"}:
        raise ValidationError("来源包头字段错误")
    bundle = dict(head)
    members = set(graph.objects(bundles[0], SRC.contains))
    for collection, cls in COLLECTIONS.items():
        nodes = set(graph.subjects(RDF.type, SRC[cls]))
        if nodes - members:
            raise ValidationError("存在不属于来源包的来源实例")
        bundle[collection] = [payload(node) for node in sorted(nodes, key=str)]
    # Restore deterministic array order from the original snapshot, not RDF iteration order.
    from .ingest import build_bundle
    try:
        metadata = bundle["metadata"]
        expected = build_bundle(metadata["raw_document"], metadata["mapping"],
            input_filename=metadata["input_filename"], input_sha256=metadata["input_sha256"])
        for collection in COLLECTIONS:
            ordering = {obj["id"]: index for index, obj in enumerate(expected[collection])}
            bundle[collection].sort(key=lambda obj: ordering.get(obj["id"], -1))
        canonical = bundle_graph(bundle)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc
    ontology = Graph().parse(data=(MODULE / "ontology.ttl").read_bytes(), format="turtle")
    # Include all source predicates and all triples on source instances. This rejects
    # dangling references, changed IDs, extra members and unknown source properties.
    entity_nodes = members | set(bundles)
    actual = {triple for triple in graph if triple not in ontology and (
        str(triple[1]).startswith(str(SRC)) or triple[0] in entity_nodes or
        (triple[1] == RDF.type and triple[2] in {SRC[c] for c in COLLECTIONS.values()} | {SRC.Bundle}))}
    if actual != set(canonical):
        raise ValidationError("RDF 类型、成员、标识或对象引用与来源 JSON 不一致")
    return bundle


def load_source(paths):
    graph = Graph()
    graph.parse(data=(MODULE / "ontology.ttl").read_bytes(), format="turtle")
    try:
        for path in paths:
            path = Path(path)
            graph.parse(data=path.read_bytes(), publicID=path.resolve().as_uri(), format="turtle")
    except Exception as exc:
        raise InputError(f"读取本地来源 TTL 失败：{exc}") from exc
    try:
        bundle = bundle_from_graph(graph)
        return graph, validate_bundle(bundle)
    except ValidationError as exc:
        return graph, report([str(exc)])
