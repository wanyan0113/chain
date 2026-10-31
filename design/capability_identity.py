"""Explicit identity registry; labels alone never establish OWL identity."""
from pathlib import Path

from rdflib import Graph, Literal, RDF, RDFS
from rdflib.namespace import OWL

from .vocabulary import KW, CAPABILITY_TYPES

# Reviewed against the named capability individuals in arcane_ontology.ttl.
# New conclusions require an explicit entry; there is no fuzzy label matching.
CANONICAL_CAPABILITIES = {
    'insight': {
        '空中灵视探知能力': 'AirInsightCapability',
        '地面灵视探知能力': 'GroundInsightCapability',
        '星界灵视探知能力': 'AstralInsightCapability',
        '水下灵视探知能力': 'UnderwaterInsightCapability',
        '远程感知能力': 'LongRangeInsightCapability',
        '中程感知能力': 'MediumRangeInsightCapability',
        '近程感知能力': 'ShortRangeInsightCapability',
    },
    'coordination': {
        '抗共鸣扰动传讯能力': 'InterferenceResistantTransmissionCapability',
        '魔法信息中继能力': 'InformationRelayCapability',
        '多法器协同统筹能力': 'MultiArtifactOrchestrationCapability',
        '快速响应能力': 'RapidResponseCoordinationCapability',
        '多目标解析能力': 'MultiTargetEvaluationCapability',
        '快速解析能力': 'RapidEvaluationCapability',
    },
    'manifestation': {
        '对空具现能力': 'AirManifestationCapability',
        '对地具现能力': 'GroundManifestationCapability',
        '对大型灵兽具现能力': 'BeastManifestationCapability',
        '对深水灵兽具现能力': 'DeepCreatureManifestationCapability',
        '星界具现能力': 'AstralManifestationCapability',
        '高空具现能力': 'HighAltitudeManifestationCapability',
        '低空具现能力': 'LowAltitudeManifestationCapability',
        '水下具现能力': 'UnderwaterManifestationCapability',
        '远程具现能力': 'LongRangeManifestationCapability',
        '中程具现能力': 'MediumRangeManifestationCapability',
        '近程具现能力': 'ShortRangeManifestationCapability',
        '单体具现能力': 'SinglePointManifestationCapability',
        '范围具现能力': 'AreaManifestationCapability',
    },
}


def load_capability_ontology():
    """Read only the local ontology; never follow owl:imports."""
    return Graph().parse(Path(__file__).resolve().parents[1] / 'arcane_ontology.ttl', format='turtle')


def capability_binding(role, label, *, ontology=None):
    local = CANONICAL_CAPABILITIES.get(role, {}).get(label)
    reason = ('已登记角色与结论的显式映射' if local else
              '结论名称已登记在其他角色下，未建立身份对应' if any(
                  label in entries for entries in CANONICAL_CAPABILITIES.values()) else
              '未登记规范能力映射，不按名称自动合并')
    if local:
        graph = load_capability_ontology() if ontology is None else ontology
        node = KW[local]
        types = set(graph.objects(node, RDF.type))
        pending = list(types)
        while pending:
            for parent in graph.objects(pending.pop(), RDFS.subClassOf):
                if parent not in types:
                    types.add(parent)
                    pending.append(parent)
        if (node, RDF.type, OWL.NamedIndividual) not in graph:
            reason = '规范URI未在当前本体声明为具名个体'
            local = None
        elif (node, RDFS.label, Literal(label, lang='zh')) not in graph:
            reason = '规范URI的中文标签与显式映射登记不一致，需重新核对'
            local = None
        elif CAPABILITY_TYPES[role] not in types:
            reason = '规范URI的所属能力类别与登记角色不一致，需重新核对'
            local = None
    return {'role': role, 'capability': label, 'status': 'mapped' if local else 'unmapped',
            'canonical_uri': str(KW[local]) if local else None, 'reason': reason}


def mapping_summary(bindings):
    """Count task-capability bindings separately from inference completeness."""
    total = len(bindings)
    mapped = sum(b['status'] == 'mapped' for b in bindings)
    return {'status': 'not_run' if not total else 'complete' if mapped == total else 'incomplete',
            'total': total, 'mapped': mapped, 'unmapped': total - mapped}


def annotate_design_mapping(report):
    """Attach current ontology identities; keep planning feasibility independent.

    The caller owns report. No resource, rule or source facts are inferred here.
    """
    ontology = load_capability_ontology()
    for task in report['tasks']:
        bindings = [capability_binding(role, label, ontology=ontology)
                    for role, labels in task['required_capabilities'].items() for label in labels]
        task['ontology_bindings'] = bindings
        task['ontology_mapping'] = mapping_summary(bindings)
    report['ontology_mapping'] = mapping_summary(
        [b for task in report['tasks'] for b in task['ontology_bindings']])
    report['canonical_export_ready'] = (report['status'] == 'complete' and
                                        report['ontology_mapping']['status'] == 'complete')
    return report
