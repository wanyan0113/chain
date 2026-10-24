"""Verify maintenance workflow requests and select one feasible resource assignment."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
import hashlib
from itertools import combinations, product
import json
import math
from pathlib import Path

from rdflib import Graph, Literal, RDF, RDFS, XSD

from check_ontology import instances_of
from maintenance_demo.mapping.run_mapping import M, map_tasks, load_input as load_mapping_input
from validate_data import validate

BASE = Path(__file__).resolve().parent


@dataclass
class CollaborationResult:
    workflows: Graph
    merged: Graph
    report: dict


def load_input(path=BASE / "input.ttl"):
    graph = load_mapping_input(path)
    graph.parse(data=(BASE / "schema.ttl").read_bytes(), format="turtle")
    return graph


def identifier(kind, source):
    return M[f"generated/{kind}/{hashlib.sha256(str(source).encode()).hexdigest()[:16]}"]


def task_sequence(graph, request):
    tasks = set(graph.objects(request, M.hasTask))
    successors = defaultdict(list)
    roots = []
    for task in tasks:
        parents = list(graph.objects(task, M.dependsOn))
        if not parents:
            roots.append(task)
        elif len(parents) != 1 or parents[0] not in tasks:
            raise ValueError(f"流程内前置任务不合法：{task}")
        else:
            successors[parents[0]].append(task)
    if len(roots) != 1 or any(len(values) > 1 for values in successors.values()):
        raise ValueError(f"每个流程请求须是单条连通任务序列：{request}")
    ordered, current = [], roots[0]
    while current is not None and current not in ordered:
        ordered.append(current)
        following = successors[current]
        current = following[0] if following else None
    if current is not None or set(ordered) != tasks:
        raise ValueError(f"任务序列有环或不连通：{request}")
    return ordered


def windows(graph, tasks):
    result = {}
    for task in tasks:
        start = Decimal(str(graph.value(task, M.plannedStartMinutes)))
        duration = Decimal(str(graph.value(task, M.durationMinutes)))
        if duration <= 0:
            raise ValueError(f"任务持续时间必须大于零：{task}")
        result[task] = (start, start + duration)
    return result


def assignment_issues(graph, flows, spans, times, handoffs, assignment):
    issues = []

    def transition(before, after):
        if times[before][1] > times[after][0]:
            issues.append({"reason": "dependency_time_overlap", "before": str(before), "after": str(after)})
        sender, receiver = assignment[before], assignment[after]
        if sender != receiver and (sender, receiver) not in handoffs:
            issues.append({"reason": "handoff_not_feasible", "sender": str(sender), "receiver": str(receiver)})

    for request, tasks in flows.items():
        for before, after in zip(tasks, tasks[1:]):
            transition(before, after)
        for parent in graph.objects(request, M.afterWorkflow):
            if spans[parent][1] > spans[request][0]:
                issues.append({"reason": "workflow_time_overlap", "before": str(parent), "after": str(request)})
            transition(flows[parent][-1], tasks[0])

    events = defaultdict(list)
    for task, resource in assignment.items():
        start, end = times[task]
        events[resource].extend(((start, 1), (end, -1)))
    for resource, points in sorted(events.items(), key=lambda pair: str(pair[0])):
        # Half-open intervals [start, end): ending at t frees capacity at t.
        capacity, active = int(graph.value(resource, M.capacity)), 0
        for moment, change in sorted(points):
            active += change
            if active > capacity:
                issues.append({"reason": "resource_capacity_exceeded", "resource": str(resource),
                               "at_minutes": str(moment), "active": active, "capacity": capacity})
                break
    demand = Counter(supply for task in assignment for supply in graph.objects(task, M.usesSupply))
    for supply, required in sorted(demand.items(), key=lambda pair: str(pair[0])):
        stock = int(graph.value(supply, M.stockCount))
        if required > stock:
            issues.append({"reason": "supply_stock_exceeded", "supply": str(supply),
                           "required_units": required, "stock_units": stock})
    return issues


def export_network(graph, flows, spans, times, assignment):
    output = Graph()
    output.bind("m", M)
    network = M["generated/network"]
    output.add((network, RDF.type, M.WorkflowNetwork))
    output.add((network, RDFS.label, Literal("维修协作网络", lang="zh")))
    ids = {request: identifier("workflow", request) for request in flows}
    flow_report, relations = [], []
    for request, tasks in flows.items():
        flow = ids[request]
        output.add((network, M.hasWorkflow, flow))
        output.add((flow, RDF.type, M.Workflow))
        output.add((flow, M.sourceRequest, request))
        label = graph.value(request, RDFS.label) or Literal(str(request))
        output.add((flow, RDFS.label, label))
        records, previous = [], None
        for order, task in enumerate(tasks, 1):
            execution = identifier("execution", task)
            start, end = times[task]
            for triple in ((flow, M.hasExecution, execution), (execution, RDF.type, M.Execution),
                           (execution, M.executionTask, task), (execution, M.assignedResource, assignment[task]),
                           (execution, M.executionOrder, Literal(order, datatype=XSD.positiveInteger)),
                           (execution, M.startMinutes, Literal(start, datatype=XSD.decimal)),
                           (execution, M.endMinutes, Literal(end, datatype=XSD.decimal))):
                output.add(triple)
            if previous is not None:
                output.add((previous, M.nextExecution, execution))
            for supply in graph.objects(task, M.usesSupply):
                output.add((execution, M.reservedSupply, supply))
            previous = execution
            records.append({"task": str(task), "resource": str(assignment[task]), "order": order,
                            "start_minutes": str(start), "end_minutes": str(end)})
        flow_report.append({"request": str(request), "workflow": str(flow), "label": str(label), "tasks": records})

    def relation(kind, participants, before=None, after=None):
        key = kind + "|" + "|".join(map(str, participants))
        node = identifier("relation", key)
        output.add((network, M.hasWorkflowRelation, node))
        output.add((node, RDF.type, M[kind]))
        for request in participants:
            output.add((node, M.relatedWorkflow, ids[request]))
        if before is not None:
            output.add((node, M.fromWorkflow, ids[before]))
        if after is not None:
            output.add((node, M.toWorkflow, ids[after]))
        relations.append({"type": kind, "participants": list(map(str, participants)),
                          "from": str(before) if before is not None else None,
                          "to": str(after) if after is not None else None})

    children = defaultdict(list)
    for request in flows:
        for parent in sorted(graph.objects(request, M.afterWorkflow), key=str):
            relation("SequentialRelation", [parent, request], parent, request)
            children[parent].append(request)
    for left, right in combinations(flows, 2):
        if max(spans[left][0], spans[right][0]) < min(spans[left][1], spans[right][1]):
            relation("ParallelRelation", [left, right])
    for parent, following in sorted(children.items(), key=lambda pair: str(pair[0])):
        if len(following) > 1:
            relation("BranchRelation", [parent, *sorted(following, key=str)], before=parent)
    return output, flow_report, relations


def collaborate(facts, max_combinations=10000):
    if type(max_combinations) is not int or max_combinations <= 0:
        raise ValueError("max_combinations 必须为正整数")
    for kind in (M.Workflow, M.Execution, M.WorkflowNetwork, M.WorkflowRelation,
                 M.SequentialRelation, M.ParallelRelation, M.BranchRelation):
        if next(facts.subjects(RDF.type, kind), None) is not None:
            raise ValueError("输入不能包含已生成的流程或网络，请使用原始事实")
    for prop in (M.canHandoffTo, M.hasWorkflow, M.hasWorkflowRelation, M.sourceRequest,
                 M.hasExecution, M.executionTask, M.assignedResource, M.nextExecution,
                 M.executionOrder, M.startMinutes, M.endMinutes, M.fromWorkflow,
                 M.toWorkflow, M.relatedWorkflow, M.reservedSupply):
        if next(facts.triples((None, prop, None)), None) is not None:
            raise ValueError(f"输入不能预填生成关系：{prop}")
    profile = json.loads((BASE / "input_profile.json").read_text(encoding="utf-8"))
    checked = validate(facts, profile)
    if checked.counts()["ERROR"]:
        raise ValueError("协同输入校验失败：" + "; ".join(
            f"{code}: {message}" for level, code, message in checked.items if level == "ERROR"))
    requests = sorted(instances_of(facts, M.WorkflowRequest), key=str)
    membership = Counter(task for request in requests for task in facts.objects(request, M.hasTask))
    if set(membership) != instances_of(facts, M.MaintenanceTask) or any(count != 1 for count in membership.values()):
        raise ValueError("每个任务须且只能属于一个流程请求；共享资源通过不同参与记录表达")
    flows = {request: task_sequence(facts, request) for request in requests}
    tasks = sorted(membership, key=str)
    times = windows(facts, tasks)
    spans = {request: (min(times[t][0] for t in items), max(times[t][1] for t in items))
             for request, items in flows.items()}
    mapping = map_tasks(facts)
    merged = mapping.merged
    query = (BASE / "handoff_rules.rq").read_text(encoding="utf-8")
    inferred_handoffs = merged.query(query).graph
    merged += inferred_handoffs
    handoffs = set(inferred_handoffs.subject_objects(M.canHandoffTo))
    options = [sorted(merged.objects(task, M.candidateResource), key=str) for task in tasks]
    combination_count = math.prod(map(len, options))
    if combination_count > max_combinations:
        raise ValueError(f"资源组合数 {combination_count} 超过上限 {max_combinations}；未证明可行或不可行")
    evaluated, selected, rejection_counts, examples = 0, None, Counter(), []
    for values in product(*options):
        evaluated += 1
        assignment = dict(zip(tasks, values))
        issues = assignment_issues(merged, flows, spans, times, handoffs, assignment)
        if not issues:
            selected = assignment
            break
        rejection_counts.update({issue["reason"] for issue in issues})
        if len(examples) < 5:
            examples.append({"assignment": {str(k): str(v) for k, v in assignment.items()}, "issues": issues})
    output, flow_report, relations = Graph(), [], []
    if selected is not None:
        output, flow_report, relations = export_network(merged, flows, spans, times, selected)
        merged += output
    report = {
        "schema_version": 1, "status": "feasible" if selected is not None else "infeasible",
        "scope": "非武器维修；验证输入的流程依赖与固定时间窗口；选择首个可行资源组合，不作最优性声明",
        "summary": {"requested_workflows": len(flows), "tasks": len(tasks),
                    "resource_combinations": combination_count, "evaluated_combinations": evaluated,
                    "generated_workflows": len(flow_report), "generated_relations": len(relations)},
        "mapping_summary": mapping.report["summary"],
        "unmatched_tasks": [item for item in mapping.report["tasks"] if item["status"] != "matched"],
        "handoff_rule_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "inferred_handoffs": [{"from": str(a), "to": str(b)} for a, b in sorted(handoffs, key=lambda p: tuple(map(str, p)))],
        "rejected_combination_counts": dict(sorted(rejection_counts.items())), "rejection_examples": examples,
        "workflows": flow_report, "relations": relations,
        "supplies": [{"supply": str(supply), "stock_units": int(merged.value(supply, M.stockCount)),
                      "required_units": sum((task, M.usesSupply, supply) in merged for task in tasks),
                      "reserved_units": sum((task, M.usesSupply, supply) in merged for task in tasks) if selected is not None else 0}
                     for supply in sorted(instances_of(merged, M.Supply), key=str)],
    }
    return CollaborationResult(output, merged, report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=BASE / "input.ttl")
    parser.add_argument("--output-dir", type=Path, default=BASE / "generated")
    parser.add_argument("--max-combinations", type=int, default=10000)
    args = parser.parse_args(argv)
    paths = {name: args.output_dir / name for name in ("validated_workflows.ttl", "merged.ttl", "collaboration_report.json")}
    protected = {p.resolve() for p in (args.input, BASE / "schema.ttl", BASE / "input_profile.json", BASE / "handoff_rules.rq")}
    if any(p.resolve() in protected for p in paths.values()):
        parser.error("输出不能覆盖输入、模式或规则文件")
    try:
        result = collaborate(load_input(args.input), args.max_combinations)
        result.report["input"] = str(args.input.resolve())
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, graph in (("validated_workflows.ttl", result.workflows), ("merged.ttl", result.merged)):
            paths[name].write_text(graph.serialize(format="turtle").rstrip() + "\n", encoding="utf-8")
        paths["collaboration_report.json"].write_text(json.dumps(result.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError) as exc:
        print(f"运行失败：{exc}")
        return 2
    print(result.report["status"])
    print(json.dumps(result.report["summary"], ensure_ascii=False))
    for flow in result.report["workflows"]:
        print(flow["label"] + ": " + " -> ".join(item["resource"].split("#")[-1] for item in flow["tasks"]))
    print(f"结果：{args.output_dir.resolve()}")
    return int(result.report["status"] != "feasible")


if __name__ == "__main__":
    raise SystemExit(main())
