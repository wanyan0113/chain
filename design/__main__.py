"""python -m design run INPUT --output-dir generated/design"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from check_ontology import analyze_graph
from rdflib import Graph
from source_data.common import read_json, write_outputs
from source_data.ingest import ingest
from source_data.validate import validate_bundle

from .engine import DesignError, SearchLimitError, design
from .rdf import design_to_rdf

ROOT = Path(__file__).resolve().parent.parent


def render_summary(result):
    lines = ["# 法术链设计报告", "", f"状态：{result['status']}", "", f"数据范围：{result['scope']}", ""]
    for task in result["tasks"]:
        lines += [f"## {task['task']}", "", f"目标：{task['target']}", "",
                  "| 候选链 | 探知 → 协调 → 具现 | 时间（秒） | 精度乘积 | 使用成本 | 评分 |", "| --- | --- | ---: | ---: | ---: | ---: |"]
        for chain in task["chains"]:
            metrics = chain["metrics"] or {}
            score = "未评分" if chain["score"] is None else f"{chain['score']:.6f}"
            values = [f"{metrics[key]:.8g}" if key in metrics else "缺失" for key in ("time", "accuracy", "cost")]
            lines.append(f"| {chain['id']} | {' → '.join(chain['artifacts'].values())} | {' | '.join(values)} | {score} |")
        lines += ["", f"阻塞项：{json.dumps(task['blockers'], ensure_ascii=False)}", ""]
    network = result["network"]
    if network:
        lines += ["## 选中奥术网", "", f"链：{', '.join(network['chains'])}", "",
                  f"目标函数：每个任务内归一化评分的算术平均；最优值 {network['score']:.6f}。", "",
                  f"计划跨度：{network['makespan_s']} 秒；按次使用成本合计：{network['cost_per_execution']} {network['cost_unit']}。", "",
                  "这是给定规则、资源和固定开始时间下的确定性计划；不表示动态场景仿真或实测效果。", ""]
    lines += ["完整规则命中、排除原因、资源冲突及来源记录见 design_report.json。", ""]
    return "\n".join(lines)


def audit_case(input_path, mapping_path):
    bundle = ingest(input_path, mapping_path)
    validation = validate_bundle(bundle, original_path=input_path)
    if validation["counts"]["ERROR"]:
        raise DesignError(str(validation["errors"]))
    return {"status": "blocked", "scope": "来源层就绪性检查；未将案例值自动转换为完整设计输入", "counts": validation["counts"],
            "issues": [{"id": issue["id"], "code": issue["code"], "description": issue["description"], "status": issue["status"]} for issue in bundle["issues"]],
            "gaps": bundle["gaps"], "network": None,
            "required_inputs": ["确认目标坐标与每个法器到目标的距离", "补充协调法器及明确的传讯、统筹边和传讯距离",
                                "确认每项能力的条件、阈值和单位", "补充三阶段耗时、[0,1]精度和同口径使用成本",
                                "提供任务相对开始时间、资源容量、库存和冷却时间", "将确认数据填写为 design/schema.json 契约；保留来源与阻塞疑点"],
            "corrections": [{"rule": "目标近 → 近程具现能力", "basis": "用户于2026-10-27明确更正原文笔误", "distance_threshold": None}]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("input", type=Path)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--overwrite", action="store_true")
    capabilities = sub.add_parser("infer-capabilities", help="独立执行任务→能力推理，不要求法器或评分数据")
    capabilities.add_argument("input", type=Path)
    capabilities.add_argument("--output-dir", type=Path, required=True)
    capabilities.add_argument("--overwrite", action="store_true")
    audit = sub.add_parser("audit-case")
    audit.add_argument("--input", type=Path, default=ROOT / "data/article_source/case_records.json")
    audit.add_argument("--mapping", type=Path, default=ROOT / "source_mappings/case_fields.json")
    audit.add_argument("--output-dir", type=Path)
    audit.add_argument("--overwrite", action="store_true")
    reference = sub.add_parser("reproduce-case", help="复算文章表13并导出正文明确给出的单条链")
    reference.add_argument("--output-dir", type=Path, required=True)
    reference.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "infer-capabilities":
            from .capabilities import infer_capabilities, capability_rdf, capability_summary
            doc = read_json(args.input)
            result = infer_capabilities(doc)
            outputs = {}
            if result["status"] == "complete":
                graph = capability_rdf(doc, result)
                review = analyze_graph(Graph().parse(ROOT / "arcane_ontology.ttl", format="turtle") + graph)
                if review.counts()["ERROR"] or review.counts()["WARN"]:
                    raise DesignError(f"能力实例未通过主本体静态检查：{review.items}")
                outputs["capabilities.ttl"] = graph.serialize(format="turtle")
            elif (args.output_dir / "capabilities.ttl").exists():
                raise DesignError("输出目录含既有 capabilities.ttl；请为未完成报告使用新目录，避免混淆旧结果")
            outputs["capability_report.json"] = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            outputs["capability_summary.md"] = capability_summary(result)
            write_outputs(args.output_dir, outputs, overwrite=args.overwrite, protected=[args.input])
        elif args.command == "reproduce-case":
            from .reference import REFERENCE_PATH, reference_summary, reference_to_rdf, reproduce_reference
            result = reproduce_reference()
            graph = reference_to_rdf(result)
            review = analyze_graph(Graph().parse(ROOT / "arcane_ontology.ttl", format="turtle") + graph)
            if review.counts()["ERROR"] or review.counts()["WARN"]:
                raise DesignError(f"文章方案结构检查失败：{review.items}")
            result["validation"]["rdf_structure"] = review.counts()
            write_outputs(args.output_dir, {
                "reference_chain.ttl": graph.serialize(format="turtle"),
                "reference_report.json": json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                "reference_chain.md": reference_summary(result)}, overwrite=args.overwrite, protected=[REFERENCE_PATH])
        elif args.command == "audit-case":
            result = audit_case(args.input, args.mapping)
            if args.output_dir:
                write_outputs(args.output_dir, {"case_readiness.json": json.dumps(result, ensure_ascii=False, indent=2) + "\n"},
                              overwrite=args.overwrite, protected=[args.input, args.mapping])
        else:
            doc = read_json(args.input)
            result = design(doc)
            result["input_sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()
            outputs = {}
            if result["status"] == "complete":
                graph = design_to_rdf(doc, result)
                combined = Graph().parse(ROOT / "arcane_ontology.ttl", format="turtle") + graph
                review = analyze_graph(combined)
                if review.counts()["ERROR"] or review.counts()["WARN"]:
                    raise DesignError(f"生成实例未通过主本体静态检查：{review.items}")
                result["rdf_validation"] = review.counts()
                outputs["design.ttl"] = graph.serialize(format="turtle")
            # An old successful RDF must never survive beside a blocked report.
            target = args.output_dir / "design.ttl"
            if result["status"] != "complete" and target.exists():
                raise DesignError("输出目录含既有 design.ttl；请为未完成报告使用新目录，避免混淆旧方案")
            outputs["design_report.json"] = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            outputs["design_summary.md"] = render_summary(result)
            write_outputs(args.output_dir, outputs, overwrite=args.overwrite, protected=[args.input])
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result["status"] in {"complete", "reference_reproduced"} else 1
    except (ValueError, OSError) as exc:
        result = {"status": "search_limit" if isinstance(exc, SearchLimitError) else "invalid", "error": str(exc), "network": None}
        print(json.dumps(result, ensure_ascii=False))
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
