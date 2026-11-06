"""Explainable named-class entailment used before artifact selection.

This implements named-class and selected object-property entailments, not full OWL reasoning.
Anonymous restrictions never manufacture named capabilities or numeric facts.
"""
from collections import deque
from copy import deepcopy
import hashlib

from rdflib import RDF, RDFS, URIRef
from rdflib.compare import to_isomorphic
from rdflib.namespace import OWL

from . import capability_identity
from .vocabulary import ARTIFACT_TYPES


def prepare_artifacts(document):
    from .engine import DesignError

    graph = capability_identity.load_capability_ontology()
    if any(graph.triples((None, OWL.imports, None))):
        raise DesignError('设计分类推导仅使用本地主本体，不支持 owl:imports')
    prepared = deepcopy(document)
    evidence = []
    for artifact in prepared['artifacts']:
        if 'ontology_types' in artifact['attributes']:
            raise DesignError('ontology_types 是推导保留字段，请使用法器顶层 ontology_types 声明类型')
        declared = artifact.get('ontology_types')
        explicit_role = artifact.get('role')
        seeds = [URIRef(value) for value in declared] if declared else [ARTIFACT_TYPES[explicit_role]]
        paths = {}
        for seed in sorted(seeds, key=str):
            if (seed, RDF.type, OWL.Class) not in graph:
                raise DesignError(f'{artifact["id"]} 的本体类型未声明为 owl:Class：{seed}')
            pending = deque([(seed, [str(seed)])])
            visited = set()
            while pending:
                node, path = pending.popleft()
                if node in visited:
                    continue
                visited.add(node)
                if str(node) not in paths or (len(path), path) < (len(paths[str(node)]), paths[str(node)]):
                    paths[str(node)] = path
                parents = sorted((parent for parent in graph.objects(node, RDFS.subClassOf)
                                  if isinstance(parent, URIRef)), key=str)
                pending.extend((parent, path + [str(parent)]) for parent in parents)
        roles = [role for role, cls in ARTIFACT_TYPES.items() if str(cls) in paths]
        if len(roles) != 1:
            raise DesignError(f'{artifact["id"]} 的本体类型必须推导出唯一角色，实际为：{roles}')
        role = roles[0]
        if explicit_role is not None and explicit_role != role:
            raise DesignError(f'{artifact["id"]} 的显式角色与本体推导角色冲突')
        artifact['role'] = role
        evidence.append({'artifact': artifact['id'], 'input_kind': 'ontology_types' if declared else 'explicit_role',
                         'asserted_types': sorted(map(str, seeds)), 'inferred_types': sorted(paths),
                         'role': role, 'role_path': paths[str(ARTIFACT_TYPES[role])],
                         'type_paths': {key: paths[key] for key in sorted(paths)},
                         'source_refs': list(artifact['source_refs'])})
    # The digest is invariant to Turtle order and blank-node identifiers.
    digest = hashlib.sha256(str(to_isomorphic(graph).graph_digest()).encode('ascii')).hexdigest()
    from .ontology_relations import prepare_relations
    relations = prepare_relations(prepared, graph, evidence)
    return prepared, {'engine': 'named-class-and-property-rules', 'status': 'complete',
                      'ontology_sha256': digest,
                      'scope': '本地主本体命名类继承及子属性、逆关系、对称关系推导；不等同完整OWL一致性检查，不推造能力或数值事实',
                      'artifacts': evidence, 'relations': relations}
