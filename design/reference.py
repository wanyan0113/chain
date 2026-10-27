"""Reproduce reported candidate scores without fabricating feasibility facts."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import quote

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

from source_data.common import read_json
from .engine import DesignError, ROLES, rank_chains
from .rdf import ARTIFACT_TYPES, KW, NODE_TYPES

REFERENCE_PATH = Path(__file__).resolve().parent.parent / "data/article_design/reference_evaluation.json"
REF = Namespace("urn:arcane-reference:")


def reproduce_reference():
    doc = read_json(REFERENCE_PATH)
    chains = []
    for row in doc["rows"]:
        chains.append({"id": row["id"], "metrics": {
            # Source units retained by the existing case: nmi / knots = hours.
            "time": row["distance"] / row["speed"] * 3600,
            "accuracy": row["insight_percent"] / 100 * row["attunement_percent"] / 100,
            "cost": row["cost_proxy"]}, "reported_score": row["reported_score"], "raw": deepcopy(row)})
    rank_chains(chains, doc["weights"], doc["equal_metric_score"])
    for chain in chains:
        chain["matches_reported_rounding"] = math.isclose(chain["score"], chain["reported_score"], abs_tol=0.0005, rel_tol=0)
        if not chain["matches_reported_rounding"]:
            raise DesignError(f"第{chain['id']}行复算与表13不一致")
    if chains[0]["id"] != doc["explicit_best_chain"]["evaluation_row"]:
        raise DesignError("计算最优行与正文明确给出的链不一致")
    winner = deepcopy(chains[0])
    winner["artifacts"] = deepcopy(doc["explicit_best_chain"])
    del winner["artifacts"]["evaluation_row"]
    winner["closed_path"] = [doc["target"], *[winner["artifacts"][r]["id"] for r in ROLES], doc["target"]]
    return {"status": "reference_reproduced", "scope": doc["scope"], "mission": doc["mission"],
            "target": doc["target"], "sources": doc["sources"], "weights": doc["weights"],
            "time_unit": "s", "time_basis": "表13距离(n mile)/速度(kn)得到小时，再乘3600换算秒；未补造三阶段耗时",
            "accuracy_basis": "表13探知精度百分比×锁定率百分比，分别除以100后相乘",
            "cost_basis": doc["cost_basis"], "cost_unit": "article_evaluation_proxy",
            "input_sha256": hashlib.sha256(REFERENCE_PATH.read_bytes()).hexdigest(),
            "evaluation": chains, "chain": winner,
            "validation": {"score_reproduction": "passed", "independent_feasibility": "not_verified", "simulation": "not_performed"},
            "unresolved": doc["unresolved"]}


def reference_to_rdf(result):
    """Export reported topology only, without asserting verified communication."""
    if result["status"] != "reference_reproduced":
        raise DesignError("需要有效的文章复现结果")
    prefix = f"urn:arcane-reference:{result['input_sha256']}:"
    def term(kind, identifier):
        return URIRef(prefix + kind + ":" + quote(identifier, safe=""))
    graph = Graph()
    graph.bind("kw", KW)
    graph.bind("ref", REF)
    chain = term("chain", result["chain"]["id"])
    target = term("target", result["target"])
    mission = term("mission", result["mission"])
    graph.add((chain, RDF.type, KW.SpellChain))
    graph.add((chain, KW.chainId, Literal("article-reference-" + result["chain"]["id"])))
    graph.add((chain, KW.chainTarget, target))
    graph.add((target, RDF.type, KW.Target))
    graph.add((mission, RDF.type, KW.ArcaneMission))
    graph.add((mission, KW.hasTarget, target))
    graph.add((chain, REF.forMission, mission))
    graph.add((chain, REF.validationStatus, Literal("reference_reproduced; independent_feasibility=not_verified")))
    graph.add((chain, RDFS.comment, Literal(result["scope"])))
    graph.add((chain, REF.reportPayload, Literal(json.dumps(result, ensure_ascii=False, allow_nan=False))))
    for source in result["sources"]:
        graph.add((chain, REF.sourceLocation, Literal(source)))
    nodes = [term("node", role) for role in ROLES]
    for i, role in enumerate(ROLES):
        info = result["chain"]["artifacts"][role]
        artifact = term("artifact", info["id"])
        platform = term("platform", info["platform"])
        graph.add((artifact, RDF.type, ARTIFACT_TYPES[role]))
        graph.add((artifact, RDFS.label, Literal(info["id"])))
        graph.add((platform, RDF.type, KW.ArtifactPlatform))
        graph.add((platform, RDFS.label, Literal(info["platform"])))
        graph.add((artifact, REF.reportedPlatform, platform))
        graph.add((nodes[i], RDF.type, NODE_TYPES[role]))
        graph.add((nodes[i], KW.nodeArtifact, artifact))
        graph.add((nodes[i], KW.nodeOrder, Literal(i + 1, datatype=XSD.integer)))
        graph.add((chain, KW.hasChainNode, nodes[i]))
        if i < 2:
            graph.add((nodes[i], KW.nextNode, nodes[i + 1]))
    return graph


def reference_summary(result):
    chain = result["chain"]
    names = [f"{chain['artifacts'][r]['id']}（{chain['artifacts'][r]['platform']}）" for r in ROLES]
    lines = ["# 单条法术链：文章案例复现", "", f"任务：{result['mission']}", "",
             "**" + " → ".join(names) + "**", "",
             f"闭合路径：{result['target']} → " + " → ".join(names) + f" → {result['target']}", "",
             f"时间：190 ÷ 3450 小时 ≈ {chain['metrics']['time']:.3f} 秒。", "",
             f"精度指标：90% × 85% = {chain['metrics']['accuracy']:.3%}；评价代理成本：21。", "",
             f"权重：时间0.3、精度0.3、成本0.4；评分：**{chain['score']:.6f}**（表13为0.896）。", "",
             "| 表13行号 | 复算评分 | 原文评分 |", "| --- | ---: | ---: |"]
    for row in sorted(result["evaluation"], key=lambda c: c["id"]):
        lines.append(f"| {row['id']} | {row['score']:.6f} | {row['reported_score']:.3f} |")
    lines += ["", "本结果从文章给出的4组评价数据中计算最优行，并输出正文明确标识的链。尚未独立验证法器筛选及协同可行性。RDF中的节点顺序表示文章方案，不代表传讯边已验收。", "", "待核对：", ""]
    lines += ["- " + issue for issue in result["unresolved"]]
    return "\n".join(lines) + "\n"
