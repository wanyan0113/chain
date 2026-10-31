"""Explicit identity registry; labels alone never establish OWL identity."""
from .rdf import KW

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


def capability_binding(role, label):
    local = CANONICAL_CAPABILITIES.get(role, {}).get(label)
    reason = ('已登记角色与结论的显式映射' if local else
              '结论名称已登记在其他角色下，未建立身份对应' if any(
                  label in entries for entries in CANONICAL_CAPABILITIES.values()) else
              '未登记规范能力映射，不按名称自动合并')
    return {'role': role, 'capability': label, 'status': 'mapped' if local else 'unmapped',
            'canonical_uri': str(KW[local]) if local else None, 'reason': reason}
