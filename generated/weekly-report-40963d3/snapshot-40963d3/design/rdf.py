"""Export selected designs using existing core classes; trace metadata is separate."""
from urllib.parse import quote
import json

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

from .engine import DesignError, design
from .vocabulary import ROLES, KW, ARTIFACT_TYPES, NODE_TYPES, CAPABILITY_TYPES
from .plans import member_ids

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
    fingerprint = report.get('execution_sha256', report['input_sha256'])
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
            ordered = candidate.get('plan', {}).get('mode') == 'ordered_steps'
            steps = (candidate['plan']['steps'] if ordered else
                     [{'id': role, 'role': role, 'artifact': candidate['artifacts'][role],
                       'required_capabilities': task_result['required_capabilities'][role]} for role in ROLES])
            nodes = [entity("node", f"{candidate['id']}/{step['id']}") for step in steps]
            for i, step in enumerate(steps):
                role = step['role']
                artifact = entity("artifact", step['artifact'])
                graph.add((artifact, RDF.type, ARTIFACT_TYPES[role]))
                classification = next(item for item in report['ontology_inference']['artifacts']
                                      if item['artifact'] == step['artifact'])
                for class_uri in classification['inferred_types']:
                    graph.add((artifact, RDF.type, URIRef(class_uri)))
                graph.add((nodes[i], RDF.type, NODE_TYPES[role]))
                graph.add((chain, KW.hasChainNode, nodes[i]))
                graph.add((nodes[i], KW.nodeOrder, Literal(i + 1, datatype=XSD.integer)))
                graph.add((nodes[i], KW.nodeArtifact, artifact))
                if i < len(steps) - 1:
                    graph.add((nodes[i], KW.nextNode, nodes[i + 1]))
                if ordered:
                    from .sequences import step_id
                    step_mission = entity('step', step_id(task_result['task'], candidate['plan']['template'], step['id']))
                    graph.add((step_mission, RDF.type, KW.SequentialStepMission))
                    graph.add((step_mission, KW.stepTarget, target))
                    graph.add((mission, KW.hasSequentialStep, step_mission))
                    graph.add((step_mission, KW.stepActivity, Literal(step['activity'], datatype=XSD.string)))
                    graph.add((step_mission, KW.stepIndex, Literal(i + 1, datatype=XSD.integer)))
                    graph.add((nodes[i], DESIGN.forStepMission, step_mission))
                for cap in step['required_capabilities']:
                    binding = bindings[(role, cap)]
                    capability = (URIRef(binding['canonical_uri']) if binding['status'] == 'mapped' else
                                  entity("capability", f"{role}/{cap}"))
                    graph.add((capability, RDF.type, CAPABILITY_TYPES[role]))
                    graph.add((artifact, KW.hasCapability, capability))
                    graph.add((mission, KW.requiresCapability, capability))
                    if ordered:
                        graph.add((step_mission, KW.requiresCapability, capability))
                    evidence = entity("capability-evidence", json.dumps([candidate['id'], step['id'], cap]))
                    graph.add((evidence, DESIGN.forMission, mission))
                    graph.add((evidence, DESIGN.forChain, chain))
                    graph.add((evidence, DESIGN.forArtifact, artifact))
                    graph.add((evidence, DESIGN.concludes, capability))
                    graph.add((evidence, DESIGN.identityStatus, Literal(binding['status'])))
                    graph.add((evidence, DESIGN.identityReason, Literal(binding['reason'])))
                    graph.add((evidence, DESIGN.ruleEvidencePayload, Literal(json.dumps({
                        'task_rules': [r for r in task_result['inference'] if r['status'] == 'pass'
                                       and r['role'] == role and r['capability'] == cap],
                        'artifact_rules': [r for s in task_result['selection'] if s['artifact'] == step['artifact']
                                           and (not ordered or (s.get('step') == step['id'] and s.get('template') == candidate['plan']['template']))
                                           for r in s['rules'] if r['status'] == 'pass' and r['capability'] == cap],
                    }, ensure_ascii=False))))
            if ordered:
                for step in steps:
                    if step['activity'] == 'observe':
                        graph.add((entity('artifact', step['artifact']), KW.detects, target))
                for connection in candidate['plan']['connections']:
                    left, right = [entity('artifact', connection[key]) for key in ('from', 'to')]
                    graph.add((left, KW.communicatesWith, right))
                    if connection['relation'] == 'control':
                        graph.add((left, KW.orchestrates, right))
            else:
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
    selected_artifacts = {identifier for task in report['tasks'] for chain in task['chains']
                          if chain['id'] in chosen for identifier in member_ids(chain)}
    for fact in report['ontology_inference']['relations']['facts']:
        endpoints = [fact['subject'], fact['object']]
        if any(value['kind'] == 'artifact' and value['id'] not in selected_artifacts for value in endpoints):
            continue
        subject, object_ = [entity('artifact', value['id']) if value['kind'] == 'artifact'
                            else URIRef(value['id']) for value in endpoints]
        graph.add((subject, URIRef(fact['predicate']), object_))
    if 'owl_reasoning' in report:
        from .sequences import step_id
        selected_steps = {step_id(task['task'], chain['plan']['template'], step['id'])
                          for task in report['tasks'] for chain in task['chains'] if chain['id'] in chosen
                          and chain.get('plan', {}).get('mode') == 'ordered_steps' for step in chain['plan']['steps']}
        for uri, value in report['owl_reasoning']['subjects'].items():
            if value['kind'] == 'artifact' and value['id'] not in selected_artifacts:
                continue
            if value['kind'] == 'step' and value['id'] not in selected_steps:
                continue
            kind = 'mission' if value['kind'] == 'task' else value['kind']
            for cls in report['owl_reasoning']['types'].get(uri, []):
                graph.add((entity(kind, value['id']), RDF.type, URIRef(cls)))
    return graph
