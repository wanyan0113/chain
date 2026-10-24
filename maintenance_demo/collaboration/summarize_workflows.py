"""Describe a validated maintenance plan's time, resource use and supplies."""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path

from rdflib import RDF

from maintenance_demo.collaboration.run_collaboration import BASE, M, collaborate, load_input


def interval_statistics(intervals):
    """Return occupied union minutes and peak use for half-open intervals."""
    events = defaultdict(int)
    for start, end in intervals:
        if not start.is_finite() or not end.is_finite() or end <= start:
            raise ValueError("时间区间必须为有限数，且结束时间大于开始时间")
        events[start] += 1
        events[end] -= 1
    active = peak = 0
    previous = None
    occupied = Decimal(0)
    for moment, change in sorted(events.items()):
        if previous is not None and active:
            occupied += moment - previous
        active += change
        peak = max(peak, active)
        previous = moment
    return occupied, peak


def summarize(result):
    """Consume a successful CollaborationResult, never an unverified plan file."""
    if result.report["status"] != "feasible":
        raise ValueError("只能统计已验证可行的维修计划")
    graph = result.workflows
    intervals = {}
    by_resource = defaultdict(list)
    for execution in graph.subjects(RDF.type, M.Execution):
        start = Decimal(str(graph.value(execution, M.startMinutes)))
        end = Decimal(str(graph.value(execution, M.endMinutes)))
        intervals[execution] = (start, end)
        by_resource[graph.value(execution, M.assignedResource)].append((start, end))
    if not intervals:
        raise ValueError("维修计划中没有参与记录")
    occupied, peak = interval_statistics(intervals.values())
    start = min(a for a, b in intervals.values())
    end = max(b for a, b in intervals.values())
    elapsed = end - start
    task_minutes = sum((b - a for a, b in intervals.values()), Decimal(0))
    workflows = []
    for flow in sorted(graph.subjects(RDF.type, M.Workflow), key=str):
        windows = [intervals[node] for node in graph.objects(flow, M.hasExecution)]
        flow_elapsed = max(b for a, b in windows) - min(a for a, b in windows)
        flow_work = sum((b - a for a, b in windows), Decimal(0))
        workflows.append({"workflow": str(flow),
                          "request": str(graph.value(flow, M.sourceRequest)),
                          "elapsed_minutes": str(flow_elapsed),
                          "task_minutes": str(flow_work),
                          "idle_gap_minutes": str(flow_elapsed - flow_work)})
    resources = []
    for resource, windows in sorted(by_resource.items(), key=lambda pair: str(pair[0])):
        busy, resource_peak = interval_statistics(windows)
        work = sum((b - a for a, b in windows), Decimal(0))
        capacity = int(result.merged.value(resource, M.capacity))
        resources.append({"resource": str(resource), "capacity": capacity,
                          "participations": len(windows), "occupied_minutes": str(busy),
                          "task_minutes": str(work), "peak_concurrent": resource_peak,
                          "capacity_utilization": str(work / (elapsed * capacity))})
    return {
        "schema_version": 1,
        "scope": "已验证维修计划的描述性统计；时间单位为分钟；不比较或优化方案",
        "time": {"start_minutes": str(start), "end_minutes": str(end),
                 "elapsed_minutes": str(elapsed), "task_minutes": str(task_minutes),
                 "occupied_minutes": str(occupied), "idle_minutes": str(elapsed - occupied),
                 "peak_concurrent_tasks": peak},
        "workflows": workflows,
        "resources": resources,
        "supplies": [dict(item, remaining_units=item["stock_units"] - item["reserved_units"])
                     for item in result.report["supplies"]],
        "unavailable_metrics": {
            "cost": "缺少明确的币种、费用率和耗材单价，不把时长当作成本",
            "quality": "缺少维修质量观测和合成口径，不由能力或可用标记推导精度",
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=BASE / "input.ttl")
    parser.add_argument("--output", type=Path, default=BASE / "generated" / "maintenance_statistics.json")
    parser.add_argument("--max-combinations", type=int, default=10000)
    args = parser.parse_args(argv)
    # A JSON output must not overwrite facts, schemas, rules or other source files.
    if args.output.suffix.lower() != ".json" or args.output.resolve() == args.input.resolve():
        parser.error("输出须为独立 JSON 文件，不能覆盖输入")
    if args.output.exists() and args.output.name != "maintenance_statistics.json":
        parser.error("已有文件仅允许覆盖同名 maintenance_statistics.json 统计报告")
    try:
        result = collaborate(load_input(args.input), args.max_combinations)
        if result.report["status"] != "feasible":
            print("维修请求不可行，未生成统计；请运行 run_collaboration 查看诊断")
            return 1
        report = summarize(result)
        report["input"] = str(args.input.resolve())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"统计失败：{exc}")
        return 2
    print(json.dumps(report["time"], ensure_ascii=False))
    print(f"结果：{args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
