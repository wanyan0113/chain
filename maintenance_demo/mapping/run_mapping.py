"""Run the two-stage maintenance mapping example; no allocation or scheduling."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from rdflib import Graph, Namespace, RDFS

from check_ontology import instances_of
from validate_data import validate

BASE = Path(__file__).resolve().parent
M = Namespace("https://example.org/maintenance#")
DERIVED = (M.requiresCapability, M.candidateResource, M.matchedTaskRule)


@dataclass
class MappingResult:
    capabilities: Graph
    candidates: Graph
    merged: Graph
    report: dict


def load_input(path=BASE / "input.ttl"):
    graph = Graph()
    for source in (BASE / "schema.ttl", Path(path)):
        graph.parse(data=source.read_bytes(), publicID=source.resolve().as_uri(), format="turtle")
    return graph


def map_tasks(facts):
    # Start each run from raw facts; old conclusions must never become new input.
    if any(next(facts.triples((None, prop, None)), None) for prop in DERIVED):
        raise ValueError("输入不能预填 requiresCapability、candidateResource 或 matchedTaskRule；请使用原始输入")
    profile = json.loads((BASE / "input_profile.json").read_text(encoding="utf-8"))
    checked = validate(facts, profile)
    if checked.counts()["ERROR"]:
        messages = [f"{code}: {message}" for level, code, message in checked.items if level == "ERROR"]
        raise ValueError("输入数据校验失败：\n" + "\n".join(messages))

    merged = Graph()
    merged.bind("m", M)
    for prefix, namespace in facts.namespaces():
        merged.bind(prefix, namespace)
    merged += facts
    task_query = (BASE / "task_rules.rq").read_text(encoding="utf-8")
    resource_query = (BASE / "resource_rules.rq").read_text(encoding="utf-8")
    capabilities = merged.query(task_query).graph
    merged += capabilities
    candidates = merged.query(resource_query).graph
    merged += candidates

    tasks = sorted(instances_of(merged, M.MaintenanceTask), key=str)
    resources = sorted(instances_of(merged, M.Resource), key=str)
    items = []
    for task in tasks:
        required = set(merged.objects(task, M.requiresCapability))
        selected = set(merged.objects(task, M.candidateResource))
        rejected = []
        if required:
            for resource in resources:
                missing = required - set(merged.objects(resource, M.providesCapability))
                reasons = []
                if missing:
                    reasons.append("missing_capabilities")
                if merged.value(resource, M.available).toPython() is not True:
                    reasons.append("unavailable")
                if merged.value(resource, M.responseMinutes).toPython() > merged.value(task, M.maxWaitMinutes).toPython():
                    reasons.append("response_exceeds_limit")
                # Keep explanations aligned with actual CONSTRUCT results.
                if (resource in selected) == bool(reasons):
                    raise RuntimeError(f"候选规则结果与解释不一致：{task}, {resource}")
                if reasons:
                    rejected.append({"resource": str(resource), "reasons": reasons,
                                     "missing_capabilities": sorted(map(str, missing))})
        status = "no_matching_task_rule" if not required else "matched" if selected else "no_candidate_resource"
        items.append({
            "task": str(task), "label": str(merged.value(task, RDFS.label) or task),
            "status": status,
            "task_conditions": {"asset_kind": str(merged.value(task, M.assetKind)),
                                "task_kind": str(merged.value(task, M.taskKind)),
                                "requires_record": merged.value(task, M.requiresRecord).toPython(),
                                "max_wait_minutes": str(merged.value(task, M.maxWaitMinutes))},
            "matched_task_rules": sorted(map(str, merged.objects(task, M.matchedTaskRule))),
            "required_capabilities": sorted(map(str, required)),
            "candidates": [{"resource": str(resource),
                            "rule": str(M.AllCapabilitiesAvailableRule),
                            "covered_capabilities": sorted(map(str, required)),
                            "response_minutes": str(merged.value(resource, M.responseMinutes))}
                           for resource in sorted(selected, key=str)],
            "rejected_resources": rejected,
        })
    report = {
        "schema_version": 1,
        "scope": "维修两级映射；能力按同一 IRI 匹配，单资源全覆盖；未分配、排程或执行 OWL 一致性推理",
        "rule_sha256": {"task_rules.rq": hashlib.sha256(task_query.encode()).hexdigest(),
                        "resource_rules.rq": hashlib.sha256(resource_query.encode()).hexdigest()},
        "summary": {"tasks": len(tasks), "resources": len(resources),
                    "required_capability_relations": len(set(capabilities.subject_objects(M.requiresCapability))),
                    "candidate_relations": len(set(candidates.subject_objects(M.candidateResource))),
                    "unmatched_tasks": sum(item["status"] != "matched" for item in items)},
        "tasks": items,
    }
    return MappingResult(capabilities, candidates, merged, report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=BASE / "input.ttl")
    parser.add_argument("--output-dir", type=Path, default=BASE / "generated")
    args = parser.parse_args(argv)
    outputs = {name: args.output_dir / name for name in (
        "inferred_capabilities.ttl", "candidate_resources.ttl", "merged.ttl", "mapping_report.json")}
    protected = {path.resolve() for path in (args.input, BASE / "schema.ttl", BASE / "input_profile.json",
                                            BASE / "task_rules.rq", BASE / "resource_rules.rq")}
    if any(path.resolve() in protected for path in outputs.values()):
        parser.error("输出不能覆盖输入或规则文件")
    try:
        result = map_tasks(load_input(args.input))
        result.report["input"] = str(args.input.resolve())
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, graph in (("inferred_capabilities.ttl", result.capabilities),
                            ("candidate_resources.ttl", result.candidates), ("merged.ttl", result.merged)):
            outputs[name].write_text(graph.serialize(format="turtle").rstrip() + "\n", encoding="utf-8")
        outputs["mapping_report.json"].write_text(json.dumps(result.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError) as exc:
        print(f"运行失败：{exc}")
        return 2
    for item in result.report["tasks"]:
        names = ", ".join(local.rsplit("#", 1)[-1] for local in (c["resource"] for c in item["candidates"]))
        print(f"{item['label']}: {item['status']} -> {names or '(无候选)'}")
    print(json.dumps(result.report["summary"], ensure_ascii=False))
    print(f"结果：{args.output_dir.resolve()}")
    return int(result.report["summary"]["unmatched_tasks"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
