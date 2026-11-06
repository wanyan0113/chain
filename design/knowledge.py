"""Local, versioned knowledge inputs shared by OWL and explicit rule engines."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from rdflib import Graph, OWL

from . import capability_identity

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_ROOT = ROOT / 'knowledge'


def knowledge_path(name, suffix):
    from .engine import DesignError
    root = KNOWLEDGE_ROOT.resolve()
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root) or path.suffix.lower() != suffix:
        raise DesignError(f'知识文件必须是knowledge目录内的相对{suffix}路径：{name}')
    return path


@contextmanager
def knowledge_context(document):
    from .engine import DesignError
    if 'knowledge' not in document:
        yield document, None
        return
    prepared = deepcopy(document)
    config = document['knowledge']
    base = ROOT / 'arcane_ontology.ttl'
    snapshots = []

    def snapshot(path, name):
        data = path.read_bytes()
        snapshots.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(),
                          'encoding': 'utf-8-sig' if data.startswith(b'\xef\xbb\xbf') else 'utf-8',
                          'content': data.decode('utf-8-sig')})
        return snapshots[-1]['content']

    graph = Graph().parse(data=snapshot(base, 'arcane_ontology.ttl'), format='turtle')
    for name in config.get('ontology_files', []):
        path = knowledge_path(name, '.ttl')
        graph.parse(data=snapshot(path, 'knowledge/' + name), format='turtle', publicID=path.as_uri())
    if any(graph.triples((None, OWL.imports, None))):
        raise DesignError('外置知识不允许 owl:imports；请在ontology_files显式列出本地文件')
    if config.get('rules_file'):
        path = knowledge_path(config['rules_file'], '.json')
        content = snapshot(path, 'knowledge/' + config['rules_file'])
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise DesignError(f'知识规则文件包含重复JSON键：{key}')
                result[key] = value
            return result
        def invalid(value):
            raise DesignError(f'知识规则文件包含非有限数值：{value}')
        rules = json.loads(content, object_pairs_hook=pairs, parse_constant=invalid)
        if not isinstance(rules, dict) or set(rules) != {'task_rules', 'artifact_rules'}:
            raise DesignError('外置规则文件须且仅须包含task_rules和artifact_rules数组')
        for collection in ('task_rules', 'artifact_rules'):
            if not isinstance(rules[collection], list):
                raise DesignError('外置规则必须为数组')
            if collection in prepared:
                prepared[collection] = prepared[collection] + rules[collection]
    token = capability_identity.ONTOLOGY_CONTEXT.set(graph)
    try:
        # URI conclusions remove the need to add Python identity registrations
        # for new capabilities. Legacy labels remain supported outside this mode.
        for collection in ('task_rules', 'artifact_rules'):
            for rule in prepared.get(collection, []):
                if (isinstance(rule, dict) and isinstance(rule.get('role'), str)
                        and rule['role'] in capability_identity.CAPABILITY_TYPES and isinstance(rule.get('capability'), str)):
                    binding = capability_identity.capability_binding(rule['role'], rule['capability'])
                    if binding['status'] == 'mapped':
                        rule['capability'] = binding['canonical_uri']
        metadata = {'engine': 'HermiT + explicit-rules', 'files': snapshots,
                    'source_refs': list(config['source_refs']),
                    'sha256': hashlib.sha256(json.dumps(snapshots, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()}
        yield prepared, metadata
    finally:
        capability_identity.ONTOLOGY_CONTEXT.reset(token)


def ontology_for_document(document):
    with knowledge_context(document):
        graph = capability_identity.load_capability_ontology()
        return graph + Graph()


def knowledge_sources(document):
    if 'knowledge' not in document:
        return []
    config = document['knowledge']
    sources = [ROOT / 'arcane_ontology.ttl'] + [knowledge_path(name, '.ttl') for name in config['ontology_files']]
    if config.get('rules_file'):
        sources.append(knowledge_path(config['rules_file'], '.json'))
    return sources


def knowledge_outputs(document, report):
    """Save exact knowledge and named inference input/output for local review."""
    if 'knowledge' not in report:
        return {}
    from .owl_scene import build_scene_graph
    with knowledge_context(document) as (prepared, metadata):
        if metadata != report['knowledge']:
            from .engine import DesignError
            raise DesignError('知识文件已变化，请重新运行设计')
        graph, _ = build_scene_graph(prepared, capability_identity.load_capability_ontology())
    from rdflib import RDF, URIRef
    inferred = Graph()
    for subject, classes in report['owl_reasoning']['types'].items():
        for cls in classes:
            inferred.add((URIRef(subject), RDF.type, URIRef(cls)))
    for s, p, o in report['owl_reasoning']['facts']:
        inferred.add((URIRef(s), URIRef(p), URIRef(o)))
    return {'knowledge_snapshot.json': json.dumps(metadata, ensure_ascii=False, indent=2) + '\n',
            'reasoning_input.ttl': graph.serialize(format='turtle'),
            'reasoning_result.ttl': inferred.serialize(format='turtle')}
