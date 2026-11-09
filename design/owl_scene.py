"""Scene facts -> actual OWL entailments -> design input facts."""
from urllib.parse import quote

from rdflib import Graph, Literal, RDF, RDFS, URIRef
from rdflib.compare import to_isomorphic
from rdflib.namespace import OWL, XSD

from .vocabulary import KW, ARTIFACT_TYPES, CAPABILITY_TYPES


def scene_uri(kind, identifier):
    return URIRef(f'urn:arcane-scene:{kind}:{quote(identifier, safe="")}')


def build_scene_graph(document, ontology, *, projection_evidence=None):
    from .engine import DesignError
    graph = ontology + Graph()
    subjects = {}

    def typed(kind, identifier, classes):
        node = scene_uri(kind, identifier)
        subjects[str(node)] = {'kind': kind, 'id': identifier}
        graph.add((node, RDF.type, OWL.NamedIndividual))
        # Protégé's short-form renderer can display opaque URNs ambiguously.
        # Labels make scene instances selectable without changing OWL facts.
        label = {'task': '任务', 'target': '目标', 'artifact': '法器', 'step': '任务环节'}[kind]
        graph.add((node, RDFS.label, Literal(f'{label}：{identifier}', lang='zh')))
        for cls in classes:
            cls = URIRef(cls)
            if (cls, RDF.type, OWL.Class) not in ontology:
                raise DesignError(f'OWL场景类型未声明为类：{cls}')
            graph.add((node, RDF.type, cls))
        return node

    for task in document['tasks']:
        node = typed('task', task['id'], [str(KW.ArcaneMission)] + task.get('ontology_types', []))
        target = typed('target', task.get('target', task['id']), [str(KW.Target)] + task.get('target_ontology_types', []))
        graph.add((node, KW.hasTarget, target))
        target_type = task['attributes'].get('target_type')
        if isinstance(target_type, str) and not task['blocking_issue_ids']:
            graph.add((target, KW.targetType, Literal(target_type, datatype=XSD.string)))
    from .sequences import SEQUENCES, step_id
    sequences = SEQUENCES.get()
    if sequences:
        for task in document['tasks']:
            for template in sequences['templates']:
                for index, step in enumerate(template['steps'], 1):
                    identifier = step_id(task['id'], template['id'], step['id'])
                    node = typed('step', identifier, [str(KW.SequentialStepMission)] + step.get('ontology_types', []))
                    subjects[str(node)].update(task=task['id'], template=template['id'], step=step['id'])
                    graph.add((scene_uri('task', task['id']), KW.hasSequentialStep, node))
                    graph.add((node, KW.stepTarget, scene_uri('target', task.get('target', task['id']))))
                    graph.add((node, KW.stepActivity, Literal(step['activity'], datatype=XSD.string)))
                    graph.add((node, KW.stepIndex, Literal(index, datatype=XSD.integer)))
                    if not task['blocking_issue_ids'] and not template.get('blocking_issue_ids'):
                        for uri in step['capabilities']:
                            graph.add((node, KW.requiresCapability, URIRef(uri)))
    for artifact in document.get('artifacts', []):
        classes = artifact.get('ontology_types', [])
        if artifact.get('role'):
            classes = classes + [str(ARTIFACT_TYPES[artifact['role']])]
        typed('artifact', artifact['id'], classes)
    for assertion in document.get('ontology_relations', []):
        values = []
        for value in (assertion['subject'], assertion['object']):
            node = scene_uri('artifact', value['id']) if value['kind'] == 'artifact' else URIRef(value['id'])
            if value['kind'] == 'artifact' and str(node) not in subjects:
                raise DesignError('OWL关系引用不存在的法器')
            if value['kind'] == 'capability' and (node, RDF.type, OWL.NamedIndividual) not in ontology:
                raise DesignError('OWL关系能力必须已在知识库声明为具名个体')
            values.append(node)
        predicate = URIRef(assertion['predicate'])
        if (predicate, RDF.type, OWL.ObjectProperty) not in ontology:
            raise DesignError('OWL关系属性必须声明为对象属性')
        if not assertion['blocking_issue_ids']:
            graph.add((values[0], predicate, values[1]))
    from .fact_mapping import project_facts
    evidence = project_facts(document, graph, scene_uri)
    if projection_evidence is not None:
        projection_evidence.extend(evidence)
    return graph, subjects


def reason_scene(document, ontology):
    from adapters.owl_infer import infer_owl
    from .engine import DesignError
    projection = []
    graph, subjects = build_scene_graph(document, ontology, projection_evidence=projection)
    query_subjects = sorted(set(subjects) | {str(node) for node in graph.subjects(RDF.type, OWL.NamedIndividual)})
    result = infer_owl(graph, query_subjects,
                       list(map(str, (KW.requiresCapability, KW.hasCapability, KW.communicatesWith, KW.orchestrates))))
    if result['status'] != 'passed':
        raise DesignError(f"OWL推理未完成，不回退为普通规则：{result.get('reason', result['status'])}")
    result['input_graph_digest'] = str(to_isomorphic(graph).graph_digest())
    result['subjects'] = subjects
    result['data_projection'] = projection
    result['source_refs'] = list(document['knowledge']['source_refs'])
    result['scope'] = '实际HermiT分类及具名对象属性结果；保存输入、公理集和结论，不提供最小逻辑证明；不推造数值事实'
    # Make entailed capability types available to identity checks within this run.
    for subject, classes in result['types'].items():
        for cls in classes:
            ontology.add((URIRef(subject), RDF.type, URIRef(cls)))
    return result


def task_requirements(task, owl_result):
    from .engine import DesignError
    if owl_result is None:
        return None
    from .capability_identity import capability_binding
    requirements = {role: [] for role in CAPABILITY_TYPES}
    node = str(scene_uri('task', task['id']))
    facts = [fact for fact in owl_result['facts'] if fact[0] == node and fact[1] == str(KW.requiresCapability)]
    for _, _, uri in facts:
        roles = [role for role in CAPABILITY_TYPES if capability_binding(role, uri)['status'] == 'mapped']
        if len(roles) != 1:
            raise DesignError(f'OWL推导的能力必须是可确定唯一角色的具名能力：{uri}')
        requirements[roles[0]].append(uri)
    return {'required_capabilities': {role: sorted(set(values)) for role, values in requirements.items()},
            'task_uri': node, 'types': owl_result['types'].get(node, []), 'facts': facts,
            'projection_issues': [item for item in owl_result.get('data_projection', [])
                                  if item['kind'] in ('task', 'target') and item['owner'] == task['id']
                                  and item['required'] and item['status'] == 'unknown'],
            'source_refs': owl_result['source_refs'], 'input_graph_digest': owl_result['input_graph_digest']}


def artifact_relations(owl_result):
    if owl_result is None:
        return []
    subjects = owl_result['subjects']
    relations = []
    for s, p, o in owl_result['facts']:
        subject = subjects.get(s)
        if not subject or subject['kind'] != 'artifact':
            continue
        object_ = subjects.get(o) if p != str(KW.hasCapability) else {'kind': 'capability', 'id': o}
        if not object_ or object_['kind'] not in ('artifact', 'capability'):
            continue
        relations.append({'subject': subject, 'predicate': p, 'object': object_,
                          'source_refs': owl_result['source_refs'], 'blocking_issue_ids': []})
    return relations
