"""Export selected designs using existing core classes; trace metadata is separate."""
from urllib.parse import quote
import hashlib
import json

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

from .engine import DesignError, ROLES

KW = Namespace("http://example.org/arcaneweb#")
DESIGN = Namespace("urn:arcane-design:")
ARTIFACT_TYPES = dict(zip(ROLES, (KW.InsightArtifact, KW.CoordinationArtifact, KW.ManifestationArtifact)))
NODE_TYPES = dict(zip(ROLES, (KW.InsightChainNode, KW.CoordinationChainNode, KW.ManifestationChainNode)))
CAPABILITY_TYPES = dict(zip(ROLES, (KW.InsightCapability, KW.CoordinationCapability, KW.ManifestationCapability)))


def design_to_rdf(doc, report):
    if report["status"] != "complete" or report["network"] is None:
        raise DesignError("只有完整通过约束检查的设计才导出业务实例")
    graph = Graph()
    graph.bind("kw", KW)
    graph.bind("design", DESIGN)
    fingerprint = hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
    def entity(kind, identifier):
        return URIRef(f"urn:arcane-design:run:{fingerprint}:{kind}:{quote(identifier, safe='')}")
    run = entity("run", "current")
    graph.add((run, RDFS.comment, Literal(doc["scope"])))
    # Keep full configuration/evidence outside the core business properties.
    graph.add((run, DESIGN.inputPayload, Literal(json.dumps(doc, ensure_ascii=False, allow_nan=False))))
    graph.add((run, DESIGN.reportPayload, Literal(json.dumps(report, ensure_ascii=False, allow_nan=False))))
    web = entity("web", report["network"]["id"])
    graph.add((web, RDF.type, KW.ArcaneWeb))
    chosen = set(report["network"]["chains"])
    for task_result in report["tasks"]:
        mission = entity("mission", task_result["task"])
        target = entity("target", task_result["target"])
        graph.add((mission, RDF.type, KW.ArcaneMission))
        graph.add((target, RDF.type, KW.Target))
        graph.add((mission, KW.hasTarget, target))
        graph.add((web, KW.supportsMission, mission))
        for candidate in task_result["chains"]:
            if candidate["id"] not in chosen:
                continue
            chain = entity("chain", candidate["id"])
            graph.add((chain, RDF.type, KW.SpellChain))
            graph.add((chain, KW.chainId, Literal(candidate["id"])))
            graph.add((chain, KW.chainTarget, target))
            graph.add((web, KW.hasChain, chain))
            graph.add((chain, DESIGN.forMission, mission))
            nodes = [entity("node", f"{candidate['id']}/{role}") for role in ROLES]
            for i, role in enumerate(ROLES):
                artifact = entity("artifact", candidate["artifacts"][role])
                graph.add((artifact, RDF.type, ARTIFACT_TYPES[role]))
                graph.add((nodes[i], RDF.type, NODE_TYPES[role]))
                graph.add((chain, KW.hasChainNode, nodes[i]))
                graph.add((nodes[i], KW.nodeOrder, Literal(i + 1, datatype=XSD.integer)))
                graph.add((nodes[i], KW.nodeArtifact, artifact))
                if i < 2:
                    graph.add((nodes[i], KW.nextNode, nodes[i + 1]))
                for cap in task_result["required_capabilities"][role]:
                    capability = entity("capability", f"{role}/{cap}")
                    graph.add((capability, RDF.type, CAPABILITY_TYPES[role]))
                    graph.add((artifact, KW.hasCapability, capability))
                    graph.add((mission, KW.requiresCapability, capability))
            insight, coordination, manifestation = [entity("artifact", candidate["artifacts"][r]) for r in ROLES]
            graph.add((insight, KW.detects, target))
            graph.add((insight, KW.insightToCoordinationCommunication, coordination))
            graph.add((coordination, KW.coordinationToManifestationCommunication, manifestation))
            graph.add((coordination, KW.orchestrates, manifestation))
            graph.add((chain, DESIGN.closedTarget, target))
    for i, relation in enumerate(report["network"]["relations"], 1):
        node = entity("relation", str(i))
        kind = relation["kind"]
        if kind == "resource_sharing":
            # Core ontology has no dedicated resource-sharing relation subclass.
            cls = KW.ChainRelation
        else:
            cls = {"serial": KW.SerialRelation, "parallel": KW.ParallelRelation, "branch": KW.BranchRelation}[kind]
        graph.add((node, RDF.type, cls))
        graph.add((node, DESIGN.relationKind, Literal(kind)))
        graph.add((web, KW.hasRelation, node))
        if kind in {"serial", "branch"}:
            destinations = relation["to"] if isinstance(relation["to"], list) else [relation["to"]]
            # A core directed relation requires exactly one destination.
            for j, destination in enumerate(destinations):
                edge = node if j == 0 else entity("relation", f"{i}/{j}")
                graph.add((edge, RDF.type, cls))
                graph.add((web, KW.hasRelation, edge))
                graph.add((edge, KW.fromChain, entity("chain", relation["from"])))
                graph.add((edge, KW.toChain, entity("chain", destination)))
                graph.add((edge, KW.relatedChain, entity("chain", relation["from"])))
                graph.add((edge, KW.relatedChain, entity("chain", destination)))
        else:
            for chain in relation["chains"]:
                graph.add((node, KW.relatedChain, entity("chain", chain)))
    return graph
