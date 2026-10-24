"""Run and explain the independent pump maintenance case from raw RDF facts."""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import html
import importlib.metadata
import json
from pathlib import Path
import sys

from rdflib import RDFS, URIRef
from rdflib.exceptions import ParserError

from maintenance_demo.collaboration.run_collaboration import BASE, M, collaborate, load_input
from maintenance_demo.collaboration.summarize_workflows import summarize
from maintenance_demo.mapping.run_mapping import BASE as MAPPING_BASE

ROOT = BASE.parents[1]

REASONS = {
    "dependency_time_overlap": "前后任务时间重叠",
    "workflow_time_overlap": "后续流程早于前置流程完成",
    "handoff_not_feasible": "资源交接条件不满足",
    "resource_capacity_exceeded": "共享资源并发超过容量",
    "supply_stock_exceeded": "一次性维修耗材库存不足",
    "no_matching_task_rule": "任务条件未命中能力规则",
    "no_candidate_resource": "有能力需求，但没有满足全部条件的单个候选资源",
    "missing_capabilities": "缺少所需能力",
    "unavailable": "资源未显式可用",
    "response_exceeds_limit": "资源响应超过任务上限",
}


def label(graph, node):
    return str(graph.value(node, RDFS.label) or node)


def explain(result):
    """Expose existing conclusions and supporting input facts, without rerunning rules."""
    graph = result.merged
    tasks, transitions = [], []
    flows = {flow["request"]: flow for flow in result.report["workflows"]}
    for flow in flows.values():
        for item in flow["tasks"]:
            task, resource = URIRef(item["task"]), URIRef(item["resource"])
            tasks.append(dict(item, task_label=label(graph, task), resource_label=label(graph, resource),
                              request=flow["request"],
                              task_conditions={"asset_kind": str(graph.value(task, M.assetKind)),
                                               "task_kind": str(graph.value(task, M.taskKind)),
                                               "requires_record": graph.value(task, M.requiresRecord).toPython()},
                              matched_rules=sorted(map(str, graph.objects(task, M.matchedTaskRule))),
                              required_capabilities=sorted(map(str, graph.objects(task, M.requiresCapability))),
                              candidates=sorted(map(str, graph.objects(task, M.candidateResource))),
                              provided_capabilities=sorted(map(str, graph.objects(resource, M.providesCapability))),
                              response_minutes=str(graph.value(resource, M.responseMinutes)),
                              max_wait_minutes=str(graph.value(task, M.maxWaitMinutes)),
                              available=graph.value(resource, M.available).toPython()))

    def transition(before, after, kind):
        sender, receiver = URIRef(before["resource"]), URIRef(after["resource"])
        same = sender == receiver
        common = set(graph.objects(sender, M.outputFormat)) & set(graph.objects(receiver, M.inputFormat))
        transitions.append({"kind": kind, "before_task": before["task"], "after_task": after["task"],
                            "sender": str(sender), "receiver": str(receiver),
                            "same_resource": same,
                            "explicit_authorization": (sender, M.handoffAuthorized, receiver) in graph,
                            "common_formats": sorted(map(str, common)),
                            "inferred_handoff": (sender, M.canHandoffTo, receiver) in graph,
                            "before_end_minutes": before["end_minutes"],
                            "after_start_minutes": after["start_minutes"],
                            "gap_minutes": str(Decimal(after["start_minutes"]) - Decimal(before["end_minutes"])),
                            "note": "同资源延续不要求自交接授权" if same else "方向授权、共同格式及双方可用已由协同验证"})

    for flow in flows.values():
        for before, after in zip(flow["tasks"], flow["tasks"][1:]):
            transition(before, after, "within_workflow")
    for relation in result.report["relations"]:
        if relation["type"] == "SequentialRelation":
            transition(flows[relation["from"]]["tasks"][-1], flows[relation["to"]]["tasks"][0],
                       "between_workflows")
    return {"tasks": tasks, "transitions": transitions}


def constraint_evidence(result, evidence, statistics):
    """Describe checks for the selected plan; an infeasible request has no selected plan."""
    if statistics is None:
        return {"task_response": [], "dependency_time": [], "resource_capacity": [], "supply_stock": []}
    return {
        "task_response": [{"task": task["task"], "task_label": task["task_label"],
                           "resource": task["resource"], "response_minutes": task["response_minutes"],
                           "limit_minutes": task["max_wait_minutes"],
                           "satisfied": Decimal(task["response_minutes"]) <= Decimal(task["max_wait_minutes"])}
                          for task in evidence["tasks"]],
        "dependency_time": [{key: item[key] for key in (
            "kind", "before_task", "after_task", "before_end_minutes", "after_start_minutes", "gap_minutes")}
                            | {"satisfied": Decimal(item["gap_minutes"]) >= 0}
                            for item in evidence["transitions"]],
        "resource_capacity": [dict(item,
                                   resource_label=label(result.merged, URIRef(item["resource"])),
                                   satisfied=item["peak_concurrent"] <= item["capacity"])
                              for item in statistics["resources"]],
        "supply_stock": [dict(item, supply_label=label(result.merged, URIRef(item["supply"])),
                              satisfied=item["reserved_units"] <= item["stock_units"])
                         for item in statistics["supplies"]],
    }


def source_manifest(input_path):
    sources = [("facts", Path(input_path))]
    for directory in (MAPPING_BASE, BASE):
        sources.append(("schema", directory / "schema.ttl"))
        sources.append(("validation_profile", directory / "input_profile.json"))
        sources.extend(("rule", path) for path in sorted(directory.glob("*.rq")))
    sources.extend(("code", path) for path in (
        ROOT / "validate_data.py", ROOT / "check_ontology.py", MAPPING_BASE / "run_mapping.py",
        BASE / "run_collaboration.py", BASE / "summarize_workflows.py", Path(__file__)))
    sources.append(("dependencies", ROOT / "requirements.txt"))
    return [{"kind": kind, "path": str(path.resolve()),
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for kind, path in sources]


def markdown(report):
    def safe(value):
        return html.escape(str(value)).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")

    lines = ["# 维修完整案例运行说明", "", f"本次状态：{report['status']}。", "",
             "从原始维修事实重新校验、映射并验证；未加载主本体。任务、依赖与窗口来自输入。", "",
             "## 任务与资源", "",
             "| 任务 | 命中规则 | 所需能力 | 本次资源 | 窗口（分钟） |",
             "| --- | --- | --- | --- | --- |"]
    for task in report["evidence"]["tasks"]:
        short = lambda values: ", ".join(v.rsplit("#", 1)[-1] for v in values)
        lines.append("| " + " | ".join(safe(value) for value in (
            task["task_label"], short(task["matched_rules"]), short(task["required_capabilities"]),
            task["resource_label"], f"{task['start_minutes']}—{task['end_minutes']}")) + " |")
    lines.extend(["", "完整 IRI、候选、能力供给和响应条件保存在 case_report.json 的 evidence 中。", "",
                  "## 交接依据", ""])
    for item in report["evidence"]["transitions"]:
        names = [item[key].rsplit("#", 1)[-1] for key in ("before_task", "after_task")]
        lines.append(f"- {safe(names[0])} → {safe(names[1])}：{safe(item['note'])}；共同格式："
                     + safe(", ".join(item["common_formats"])) + "。")
    if report["statistics"] is not None:
        checks = report["constraint_evidence"]
        lines.extend(["", "## 已选维修计划的约束证据", "",
                      "以下数值说明本次已选计划，不证明未选组合可行。区间为 [开始, 结束)。", "",
                      "| 任务 | 响应（分钟） | 响应上限（分钟） | 满足 |",
                      "| --- | --- | --- | --- |"])
        for item in checks["task_response"]:
            lines.append(f"| {safe(item['task_label'])} | {item['response_minutes']} | "
                         f"{item['limit_minutes']} | {'是' if item['satisfied'] else '否'} |")
        lines.extend(["", "| 前置任务→后续任务 | 前置结束 | 后续开始 | 间隔（分钟） |",
                      "| --- | --- | --- | --- |"])
        for item in checks["dependency_time"]:
            names = [item[key].rsplit("#", 1)[-1] for key in ("before_task", "after_task")]
            lines.append(f"| {safe(names[0])} → {safe(names[1])} | {item['before_end_minutes']} | "
                         f"{item['after_start_minutes']} | {item['gap_minutes']} |")
        lines.extend(["", "| 资源 | 峰值并发 | 容量 | 占用时长（分钟） | 容量利用率 |",
                      "| --- | --- | --- | --- | --- |"])
        for item in checks["resource_capacity"]:
            lines.append(f"| {safe(item['resource_label'])} | {item['peak_concurrent']} | {item['capacity']} | "
                         f"{item['occupied_minutes']} | {item['capacity_utilization']} |")
        lines.extend(["", "容量利用率以整个网络历时 × 资源容量为分母。", "",
                      "| 维修耗材 | 库存 | 本次预留 | 计划余量 |",
                      "| --- | --- | --- | --- |"])
        for item in checks["supply_stock"]:
            lines.append(f"| {safe(item['supply_label'])} | {item['stock_units']} | {item['reserved_units']} | "
                         f"{item['remaining_units']} |")
        if not checks["supply_stock"]:
            lines.extend(["", "本次任务未领用维修耗材，没有耗材库存检查项。"])
    collaboration = report["collaboration"]
    if collaboration["unmatched_tasks"] or collaboration["rejected_combination_counts"]:
        lines.extend(["", "## 匹配与失败组合诊断", ""])
        for item in collaboration["unmatched_tasks"]:
            reason = REASONS.get(item["status"], item["status"])
            lines.append(f"- {safe(item['label'])}：{safe(reason)}（{safe(item['status'])}）。")
            for resource in item["rejected_resources"]:
                reasons = ", ".join(REASONS.get(reason, reason) for reason in resource["reasons"])
                missing = ", ".join(resource["missing_capabilities"])
                lines.append(f"  - {safe(resource['resource'])}：{safe(reasons)}。"
                             + (f"缺少能力：{safe(missing)}。" if missing else ""))
        for reason, count in collaboration["rejected_combination_counts"].items():
            lines.append(f"- {safe(REASONS.get(reason, reason))}：影响 {count} 个已评估组合"
                         f"（{safe(reason)}）；同一组合可能有多种原因，计数不可相加。")
        for index, example in enumerate(collaboration["rejection_examples"], 1):
            lines.extend(["", f"失败组合示例 {index}：", ""])
            lines.extend(f"- 分配 {safe(task)} → {safe(resource)}。"
                         for task, resource in sorted(example["assignment"].items()))
            for issue in example["issues"]:
                reason = issue["reason"]
                details = "; ".join(f"{key}={value}" for key, value in issue.items() if key != "reason")
                lines.append(f"- {safe(REASONS.get(reason, reason))}：{safe(details)}。")
        lines.extend(["", "最多展示 5 个失败组合示例；完整计数和未匹配原因保留在 JSON。"
                      "首个可行组合之后不会继续搜索。"])
    lines.extend(["", "## 流程关系", ""])
    flows = report["collaboration"]["workflows"]
    if flows:
        ids = {flow["request"]: f"W{index}" for index, flow in enumerate(flows, 1)}
        lines.extend(["```mermaid", "flowchart LR"])
        for flow in flows:
            node = ids[flow["request"]]
            lines.append(f'    {node}["维修流程 {node[1:]}"]')
        for relation in report["collaboration"]["relations"]:
            if relation["type"] == "SequentialRelation":
                lines.append(f"    {ids[relation['from']]} --> {ids[relation['to']]}")
            elif relation["type"] == "ParallelRelation":
                left, right = relation["participants"]
                lines.append(f'    {ids[left]} -. "时间并行" .- {ids[right]}')
        lines.extend(["```", ""])
        lines.extend(f"- {ids[flow['request']]}：{safe(flow['label'])}。" for flow in flows)
        lines.extend(["", "同一前置流程的多个直接后继表示全部执行的分支。图形展示输入依赖和已验证关系。"])
    if report["statistics"] is not None:
        time = report["statistics"]["time"]
        lines.extend(["", "## 统计与边界", "",
                      f"网络历时 {time['elapsed_minutes']} 分钟，任务工时 {time['task_minutes']} 分钟，"
                      f"峰值并发 {time['peak_concurrent_tasks']}。", "",
                      "容量和耗材已在生成前验证。未定义成本与质量，不产生评分或排名。"])
    else:
        lines.extend(["", "没有可行计划，不生成资源分配或成功统计。未匹配任务、拒绝计数及示例见 JSON 的 collaboration。"])
    lines.extend(["", "输入、模式、配置、规则与程序的 SHA-256 见 JSON 的 sources；"
                  "复现还需安装仓库 requirements.txt 中的依赖。", ""])
    return "\n".join(lines)


def run_case(input_path=BASE / "input.ttl", max_combinations=10000):
    result = collaborate(load_input(input_path), max_combinations)
    evidence = explain(result)
    statistics = summarize(result) if result.report["status"] == "feasible" else None
    report = {"schema_version": 1, "status": result.report["status"],
              "runtime": {"python": sys.version, "rdflib": importlib.metadata.version("rdflib")},
              "sources": source_manifest(input_path), "max_combinations": max_combinations,
              "collaboration": result.report, "evidence": evidence,
              "constraint_evidence": constraint_evidence(result, evidence, statistics),
              "statistics": statistics}
    return result, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=BASE / "input.ttl")
    parser.add_argument("--output-dir", type=Path, default=BASE / "generated" / "case")
    parser.add_argument("--max-combinations", type=int, default=10000)
    args = parser.parse_args(argv)
    paths = {name: args.output_dir / name for name in (
        "validated_workflows.ttl", "merged.ttl", "case_report.json", "explanation.md")}
    if args.input.resolve() in {path.resolve() for path in paths.values()}:
        parser.error("输出不能覆盖输入")
    try:
        result, report = run_case(args.input, args.max_combinations)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, graph in (("validated_workflows.ttl", result.workflows), ("merged.ttl", result.merged)):
            paths[name].write_text(graph.serialize(format="turtle").rstrip() + "\n", encoding="utf-8")
        paths["case_report.json"].write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["explanation.md"].write_text(markdown(report), encoding="utf-8")
    except (ValueError, OSError, SyntaxError, ParserError) as exc:
        print(f"案例运行失败：{exc}")
        return 2
    print(report["status"])
    print(f"案例结果：{args.output_dir.resolve()}")
    return int(report["status"] != "feasible")


if __name__ == "__main__":
    raise SystemExit(main())
