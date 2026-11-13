"""Record article data and reported topology without deriving design metrics."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

from source_data.common import read_json
from .engine import DesignError, ROLES
from .rdf import ARTIFACT_TYPES, KW, NODE_TYPES

REFERENCE_PATH = Path(__file__).resolve().parent.parent / "data/article_design/reference_evaluation.json"
REF = Namespace("urn:arcane-reference:")


def load_reference():
    """The stated best chain comes from the text, never a computed ranking."""
    doc = read_json(REFERENCE_PATH)
    chains = [{"id": row["id"], "metrics": None, "score": None,
               "reported_score": row["reported_score"], "raw": deepcopy(row)} for row in doc["rows"]]
    stated = [row for row in chains if row['id'] == doc['explicit_best_chain']['evaluation_row']]
    if len(stated) != 1:
        raise DesignError('正文所指表13行号必须唯一对应一条原文记录')
    winner = deepcopy(stated[0])
    winner["artifacts"] = deepcopy(doc["explicit_best_chain"])
    del winner["artifacts"]["evaluation_row"]
    winner["closed_path"] = [doc["target"], *[winner["artifacts"][r]["id"] for r in ROLES], doc["target"]]
    reported_topology = [{"id": "reported-eidolon-chain", "mission": doc["mission"], "target": doc["target"],
        "artifacts": deepcopy(winner["artifacts"]), "metrics": deepcopy(winner["metrics"]),
        "score": None, "reported_score": winner['reported_score'],
        "evaluation_row": winner["id"], "source_refs": deepcopy(doc["sources"])}]
    for statement in doc.get("additional_reported_chains", []):
        reported_topology.append({**deepcopy(statement), "metrics": None, "score": None,
                                  "reported_score": None, "evaluation_row": None})
    return {"status": "reference_recorded", "scope": doc["scope"], "mission": doc["mission"],
            "target": doc["target"], "sources": doc["sources"], "weights": doc["weights"],
            "metrics_basis": "仅记录原文字段；执行耗时、综合精度、使用成本和程序评分均未计算",
            "cost_basis": doc["cost_basis"],
            "input_sha256": hashlib.sha256(REFERENCE_PATH.read_bytes()).hexdigest(),
            "evaluation": chains, "chain": winner, "reported_topology": reported_topology,
            "validation": {"score_reproduction": "not_performed", "independent_feasibility": "not_verified", "simulation": "not_performed"},
            "unresolved": doc["unresolved"]}


def reference_to_rdf(result):
    """Export reported topology only, without asserting verified communication."""
    if result["status"] != "reference_recorded":
        raise DesignError("需要有效的文章原文记录")
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
    graph.add((chain, REF.validationStatus, Literal("reference_recorded; score_reproduction=not_performed; independent_feasibility=not_verified")))
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
    lines = ["# 文章案例原文记录", "", f"任务：{result['mission']}", "",
             "正文明确给出的链：", "",
             "**" + " → ".join(names) + "**", "",
             f"闭合路径：{result['target']} → " + " → ".join(names) + f" → {result['target']}", "",
             f"原文指向表13第{chain['id']}行，原文评分：{chain['reported_score']:.3f}。", "",
             "原文权重：" + json.dumps(result['weights'], ensure_ascii=False), "",
             result['metrics_basis'] + '。', "",
             "| 表13行号 | 距离原值 | 速度原值 | 探知精度原值（%） | 锁定率原值（%） | 附魔造价原值 | 原文评分 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in result["evaluation"]:
        raw = row['raw']
        lines.append(f"| {row['id']} | {raw['distance']} | {raw['speed']} | {raw['insight_percent']} | {raw['attunement_percent']} | {raw['cost_proxy']} | {row['reported_score']:.3f} |")
    lines += ["", "本页保留原文数据、分数与链结构；链身份来自正文，未计算指标或进行最优选择。尚未独立验证法器筛选及协同可行性。RDF中的节点顺序表示文章方案，不代表传讯边已验收。", "", "待核对：", ""]
    lines += ["- " + issue for issue in result["unresolved"]]
    return "\n".join(lines) + "\n"


def reference_view_html(result):
    """Offline report using the shared SVG renderer, with reference-only labels."""
    script = Path(__file__).with_name('network_view.mjs').read_text(encoding='utf-8')
    payload = json.dumps(result, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')
    template = Path(__file__).with_name('reference_view.html').read_text(encoding='utf-8')
    return template.replace('/* SHARED_GRAPH_MODULE */', script).replace('/* REFERENCE_RESULT */', payload)
