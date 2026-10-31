"""Shared ontology vocabulary without inference or exporter dependencies."""
from rdflib import Namespace

ROLES = ("insight", "coordination", "manifestation")
KW = Namespace("http://example.org/arcaneweb#")
ARTIFACT_TYPES = dict(zip(ROLES, (KW.InsightArtifact, KW.CoordinationArtifact, KW.ManifestationArtifact)))
NODE_TYPES = dict(zip(ROLES, (KW.InsightChainNode, KW.CoordinationChainNode, KW.ManifestationChainNode)))
CAPABILITY_TYPES = dict(zip(ROLES, (KW.InsightCapability, KW.CoordinationCapability, KW.ManifestationCapability)))
