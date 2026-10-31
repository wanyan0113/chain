"""Standalone task-to-capability inference (article equations 1 and 12)."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from jsonschema import Draft202012Validator
from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef

from .engine import DesignError, infer_task_capabilities, require_finite
from .rdf import CAPABILITY_TYPES, KW
from .capability_identity import capability_binding

META = Namespace("urn:arcane-capability-inference:")


def mapping_summary(bindings):
    """Identity coverage is independent of whether inference has completed."""
    total = len(bindings)
    mapped = sum(b['status'] == 'mapped' for b in bindings)
    return {'status': 'not_run' if not total else 'complete' if mapped == total else 'incomplete',
            'total': total, 'mapped': mapped, 'unmapped': total - mapped}


def capability_schema():
    """Reuse the design contract; target identity and schedule are not needed yet."""
    base = json.loads(Path(__file__).with_name("schema.json").read_text(encoding="utf-8"))
    definitions = deepcopy(base["$defs"])
    definitions["task"]["required"] = ["id", "attributes", "source_refs", "blocking_issue_ids"]
    return {
        "$schema": base["$schema"], "type": "object", "additionalProperties": False,
        "required": ["format_version", "scope", "tasks", "task_rules", "required_roles"],
        "properties": {
            "format_version": {"const": "1.0"}, "scope": definitions["id"],
            "tasks": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/task"}},
            "task_rules": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/taskRule"}},
            "required_roles": {"type": "array", "minItems": 1, "uniqueItems": True,
                               "items": {"$ref": "#/$defs/role"}},
        }, "$defs": definitions,
    }


def infer_capabilities(document):
    """Infer requirements only; no artifact, metric or scheduling input is needed."""
    require_finite(document)
    errors = sorted(Draft202012Validator(capability_schema()).iter_errors(document),
                    key=lambda e: str(list(e.path)))
    if errors:
        raise DesignError("; ".join(f"{list(e.path)}: {e.message}" for e in errors[:8]))
    for collection in ("tasks", "task_rules"):
        ids = [item["id"] for item in document[collection]]
        if len(ids) != len(set(ids)):
            raise DesignError(f"{collection} 包含重复 ID")
    for rule in document["task_rules"]:
        if any(not c["field"].startswith("task.") or
               ("ref" in c and not c["ref"].startswith("task.")) for c in rule["when"]):
            raise DesignError("任务—能力规则只能引用 task 字段")
    tasks = [infer_task_capabilities(task, document["task_rules"], document["required_roles"])
             for task in document["tasks"]]
    for task, result in zip(document["tasks"], tasks):
        result["attributes"] = deepcopy(task["attributes"])
        result["ontology_bindings"] = [capability_binding(role, label)
                                       for role, labels in result["required_capabilities"].items()
                                       for label in labels]
        result['ontology_mapping'] = mapping_summary(result['ontology_bindings'])
        result['canonical_export_ready'] = (result['status'] == 'complete' and
                                             result['ontology_mapping']['status'] == 'complete')
    mapping = mapping_summary([b for task in tasks for b in task['ontology_bindings']])
    return {
        "format_version": "1.0", "scope": document["scope"],
        "status": "complete" if all(t["status"] == "complete" for t in tasks) else "blocked",
        "required_roles": list(document["required_roles"]), "tasks": tasks,
        "ontology_mapping": mapping,
        "canonical_export_ready": all(t['canonical_export_ready'] for t in tasks),
        "input_sha256": hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True,
                                                  allow_nan=False).encode("utf-8")).hexdigest(),
        "validation_scope": "仅给定任务事实和显式规则下的能力需求推理；未验证规则完备性、来源真实性或法器可行性",
    }


def capability_rdf(document, report, *, require_canonical=False):
    """Export complete requirements to the existing ontology with rule evidence."""
    if report != infer_capabilities(document) or report["status"] != "complete":
        raise DesignError("只有与当前输入一致且完整的能力推理结果才能导出业务实例")
    if require_canonical and not report['canonical_export_ready']:
        raise DesignError('存在未映射能力，不能按规范URI模式导出；请先核对 ontology_bindings')
    graph = Graph()
    graph.bind("kw", KW)
    graph.bind("inference", META)

    def entity(kind, identifier):
        return URIRef(f"urn:arcane-capability-inference:{report['input_sha256']}:{kind}:"
                      + quote(identifier, safe=""))

    run = entity("run", "current")
    graph.add((run, META.inputPayload, Literal(json.dumps(document, ensure_ascii=False, allow_nan=False))))
    graph.add((run, META.reportPayload, Literal(json.dumps(report, ensure_ascii=False, allow_nan=False))))
    for task in report["tasks"]:
        mission = entity("mission", task["task"])
        graph.add((mission, RDF.type, KW.ArcaneMission))
        graph.add((mission, RDFS.label, Literal(task["task"])))
        for source in task["source_refs"]:
            graph.add((mission, META.sourceRef, Literal(source)))
        for result in task["inference"]:
            if result["status"] != "pass":
                continue
            binding = capability_binding(result['role'], result['capability'])
            capability = (URIRef(binding['canonical_uri']) if binding['status'] == 'mapped' else
                          entity("capability", f"{result['role']}/{result['capability']}"))
            rule = entity("rule", result["rule"])
            graph.add((capability, RDF.type, CAPABILITY_TYPES[result["role"]]))
            graph.add((capability, RDFS.label, Literal(result["capability"])))
            graph.add((mission, KW.requiresCapability, capability))
            # Evidence is per task/rule, so shared capability instances do not
            # mix the justifications of different tasks.
            evidence = entity("evidence", json.dumps([task["task"], result["rule"]]))
            graph.add((evidence, META.forMission, mission))
            graph.add((evidence, META.concludes, capability))
            graph.add((evidence, META.matchedRule, rule))
            graph.add((evidence, META.identityStatus, Literal(binding['status'])))
            graph.add((evidence, META.identityReason, Literal(binding['reason'])))
            graph.add((evidence, META.checksPayload, Literal(json.dumps(result["checks"], ensure_ascii=False))))
            for source in result["source_refs"]:
                graph.add((rule, META.sourceRef, Literal(source)))
    return graph


def capability_summary(report):
    roles = {"insight": "探知", "coordination": "协调", "manifestation": "具现"}
    mapping = report['ontology_mapping']
    lines = ["# 能力推理报告", "", f"推理状态：{report['status']}",
             f"本体映射：{mapping['status']}；已映射 {mapping['mapped']}／{mapping['total']}；未映射 {mapping['unmapped']}",
             f"规范URI导出就绪：{'是' if report['canonical_export_ready'] else '否'}", "", report["validation_scope"], "",
             "blocked 时已命中能力仅为部分结果，不能作为完整需求交给后续法器筛选。", ""]
    for task in report["tasks"]:
        lines += [f"## {task['task']}", "", f"推理状态：{task['status']}",
                  f"本体映射：{task['ontology_mapping']['status']}；规范URI导出就绪："
                  + ('是' if task['canonical_export_ready'] else '否'), ""]
        for role, capabilities in task["required_capabilities"].items():
            lines.append(f"- {roles[role]}：{'、'.join(capabilities) or '未推出'}")
        for binding in task['ontology_bindings']:
            lines.append(f"- 本体对应 {binding['capability']}："
                         + (binding['canonical_uri'] if binding['status'] == 'mapped' else
                            '未映射（允许局部导出时使用本次局部个体）') + f"；{binding['reason']}")
        for rule in task["inference"]:
            lines += ["", f"规则 {rule['rule']} → {rule['capability']}：{rule['status']}",
                      f"来源：{'；'.join(rule['source_refs']) or '未提供'}"]
            for check in rule["checks"]:
                lines.append(f"- {json.dumps(check['condition'], ensure_ascii=False)}：{check['reason']}")
            if rule["blocking_issue_ids"]:
                lines.append(f"- 规则疑点：{'、'.join(rule['blocking_issue_ids'])}")
        lines += ["", "阻塞项：" + json.dumps(task["blockers"], ensure_ascii=False), ""]
    return "\n".join(lines)
