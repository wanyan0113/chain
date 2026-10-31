"""Export selected designs using existing core classes; trace metadata is separate."""
from urllib.parse import quote
import json

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

from .engine import DesignError, design
from .vocabulary import ROLES, KW, ARTIFACT_TYPES, NODE_TYPES, CAPABILITY_TYPES

DESIGN = Namespace("urn:arcane-design:")


def design_to_rdf(doc, report, *, require_canonical=False):
    if report["status"] != "complete" or report["network"] is None:
        raise DesignError("只有完整通过约束检查的设计才导出业务实例")
    expected = design(doc)
    # CLI-only file/check annotations are not part of the reasoning result and
    # cannot be verified from a parsed JSON document. Never emit them as evidence.
    core_report = {key: value for key, value in report.items()
                   if key not in ('input_file_sha256', 'rdf_validation')}
    if core_report != expected:
        raise DesignError("链网报告与当前输入、推理证据或本体映射不一致，请重新运行设计")
    report = expected
    if require_canonical and not report['canonical_export_ready']:
        raise DesignError("链网设计含未映射能力，不能按规范URI模式导出")
    graph = Graph()
    graph.bind("kw", KW)
    graph.bind("design", DESIGN)
    fingerprint = report['input_sha256']
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
        bindings = {(b['role'], b['capability']): b for b in task_result['ontology_bindings']}
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
                    binding = bindings[(role, cap)]
                    capability = (URIRef(binding['canonical_uri']) if binding['status'] == 'mapped' else
                                  entity("capability", f"{role}/{cap}"))
                    graph.add((capability, RDF.type, CAPABILITY_TYPES[role]))
                    graph.add((artifact, KW.hasCapability, capability))
                    graph.add((mission, KW.requiresCapability, capability))
                    evidence = entity("capability-evidence", json.dumps([candidate['id'], role, cap]))
                    graph.add((evidence, DESIGN.forMission, mission))
                    graph.add((evidence, DESIGN.forChain, chain))
                    graph.add((evidence, DESIGN.forArtifact, artifact))
                    graph.add((evidence, DESIGN.concludes, capability))
                    graph.add((evidence, DESIGN.identityStatus, Literal(binding['status'])))
                    graph.add((evidence, DESIGN.identityReason, Literal(binding['reason'])))
                    graph.add((evidence, DESIGN.ruleEvidencePayload, Literal(json.dumps({
                        'task_rules': [r for r in task_result['inference'] if r['status'] == 'pass'
                                       and r['role'] == role and r['capability'] == cap],
                        'artifact_rules': [r for s in task_result['selection'] if s['artifact'] == candidate['artifacts'][role]
                                           for r in s['rules'] if r['status'] == 'pass' and r['capability'] == cap],
                    }, ensure_ascii=False))))
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
