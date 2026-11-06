"""Bounded positive entailments from declared object-property axioms.

Only subproperty, inverse and symmetric-property rules are implemented. Missing
facts remain unknown. No transitive communication or numeric facts are invented.
"""
from collections import deque
from urllib.parse import quote

from rdflib import RDF, RDFS, URIRef
from rdflib.namespace import OWL

from .vocabulary import KW, CAPABILITY_TYPES

MAX_FACTS = 100000


def prepare_relations(document, ontology, classifications, *, owl_relations=None):
    from .engine import DesignError, SearchLimitError

    artifacts = {item['id']: item for item in document['artifacts']}
    types = {('artifact', item['artifact']): set(item['inferred_types']) for item in classifications}
    endpoints = {}

    def endpoint(value):
        kind, identifier = value['kind'], value['id']
        if kind == 'artifact':
            if identifier not in artifacts:
                raise DesignError(f'本体关系引用不存在的法器：{identifier}')
            node = URIRef('urn:arcane-input:artifact:' + quote(identifier, safe=''))
        else:
            node = URIRef(identifier)
            if (node, RDF.type, OWL.NamedIndividual) not in ontology:
                raise DesignError(f'本体关系能力必须是主本体中的具名个体：{identifier}')
            reachable = set(ontology.objects(node, RDF.type))
            pending = list(reachable)
            while pending:
                for parent in ontology.objects(pending.pop(), RDFS.subClassOf):
                    if isinstance(parent, URIRef) and parent not in reachable:
                        reachable.add(parent)
                        pending.append(parent)
            if not any(cls in reachable for cls in CAPABILITY_TYPES.values()):
                raise DesignError(f'本体关系客体不是已声明的能力个体：{identifier}')
            types[kind, identifier] = set(map(str, reachable))
        endpoints[node] = dict(value)
        return node

    inverse = {}
    for left, right in ontology.subject_objects(OWL.inverseOf):
        if isinstance(left, URIRef) and isinstance(right, URIRef):
            axiom = [str(left), str(OWL.inverseOf), str(right)]
            inverse.setdefault(left, {})[right] = axiom
            inverse.setdefault(right, {})[left] = axiom

    def expansions(triple):
        s, p, o = triple
        for parent in sorted(ontology.objects(p, RDFS.subPropertyOf), key=str):
            if isinstance(parent, URIRef):
                yield (s, parent, o), 'subproperty', [str(p), str(RDFS.subPropertyOf), str(parent)]
        for other in sorted(inverse.get(p, ()), key=str):
            yield (o, other, s), 'inverse', inverse[p][other]
        if (p, RDF.type, OWL.SymmetricProperty) in ontology:
            yield (o, p, s), 'symmetric', [str(p), str(RDF.type), str(OWL.SymmetricProperty)]

    def fact_json(triple):
        s, p, o = triple
        return {'subject': endpoints[s], 'predicate': str(p), 'object': endpoints[o]}

    facts, pending, assertions = {}, deque(), []
    declared = document.get('ontology_relations', [])
    for index, assertion in enumerate(declared + (owl_relations or []), 1):
        origin = 'input' if index <= len(declared) else 'HermiT'
        triple = endpoint(assertion['subject']), URIRef(assertion['predicate']), endpoint(assertion['object'])
        if (triple[1], RDF.type, OWL.ObjectProperty) not in ontology:
            raise DesignError(f'本体关系属性未声明为 owl:ObjectProperty：{triple[1]}')
        # Validate the supported relation vocabulary without evaluating uncertain facts.
        closure, queue = {triple}, deque([triple])
        while queue:
            for derived, _, _ in expansions(queue.popleft()):
                if derived not in closure:
                    closure.add(derived)
                    queue.append(derived)
                if len(closure) > MAX_FACTS:
                    raise SearchLimitError('本体关系推导超过事实数量上限')
        recognized = False
        for s, p, o in closure:
            left, right = endpoints[s], endpoints[o]
            if p == KW.hasCapability:
                recognized = True
                if left['kind'] != 'artifact' or right['kind'] != 'capability':
                    raise DesignError('hasCapability 必须连接法器与能力')
                role = artifacts[left['id']]['role']
                if str(CAPABILITY_TYPES[role]) not in types[right['kind'], right['id']]:
                    raise DesignError('法器角色与本体关系声明的能力类别冲突')
            elif p in (KW.communicatesWith, KW.orchestrates):
                recognized = True
                if left['kind'] != 'artifact' or right['kind'] != 'artifact' or s == o:
                    raise DesignError('传讯或统筹关系必须连接两个不同法器')
                if p == KW.orchestrates and artifacts[left['id']]['role'] != 'coordination':
                    raise DesignError('统筹关系主体必须是协调法器')
            # Named domain/range serve as endpoint checks here, not type generators.
            for node, property_ in ((s, RDFS.domain), (o, RDFS.range)):
                for expected in ontology.objects(p, property_):
                    if isinstance(expected, URIRef) and str(expected) not in types[endpoints[node]['kind'], endpoints[node]['id']]:
                        raise DesignError(f'本体关系端点类别不符合属性的定义域或值域：{p}')
        if not recognized:
            raise DesignError('本体关系须能推导为具备能力、传讯或统筹关系')
        active = not assertion['blocking_issue_ids']
        assertions.append({'index': index, **assertion, 'status': 'pass' if active else 'unknown', 'origin': origin})
        if active and triple not in facts:
            if len(facts) >= MAX_FACTS:
                raise SearchLimitError('本体关系推导超过事实数量上限')
            facts[triple] = {'rule': 'asserted' if origin == 'input' else 'owl_result', 'assertion': index,
                             'source_refs': list(assertion['source_refs'])}
            pending.append(triple)
    while pending:
        premise = pending.popleft()
        for triple, rule, axiom in expansions(premise):
            if triple in facts:
                continue
            if len(facts) >= MAX_FACTS:
                raise SearchLimitError('本体关系推导超过事实数量上限')
            facts[triple] = {'rule': rule, 'premise': fact_json(premise), 'axiom': axiom,
                             'source_refs': list(facts[premise]['source_refs'])}
            pending.append(triple)
    records = [{**fact_json(triple), 'derivation': facts[triple]}
               for triple in sorted(facts, key=lambda t: tuple(map(str, t)))]
    capabilities = {identifier: [] for identifier in artifacts}
    for (s, p, o), proof in facts.items():
        if p == KW.hasCapability:
            capabilities[endpoints[s]['id']].append(endpoints[o]['id'])
    # Link rows still supply explicit distances and limits. Inference fills only
    # requested truth values; a missing assertion is never a negative fact.
    link_evidence = []
    for link in document['links']:
        evidence = {'from': link['from'], 'to': link['to'], 'checks': []}
        for field, property_ in [('communicates', KW.communicatesWith), ('controls', KW.orchestrates)]:
            triple = (URIRef('urn:arcane-input:artifact:' + quote(link['from'], safe='')), property_,
                      URIRef('urn:arcane-input:artifact:' + quote(link['to'], safe='')))
            proof = facts.get(triple)
            value = link.get(field)
            if proof and value is False:
                raise DesignError(f'{link["from"]}→{link["to"]} 的显式{field}=false与本体推导冲突')
            if value is None and proof:
                link[field] = True
                link['source_refs'] = list(dict.fromkeys(link['source_refs'] + proof['source_refs']))
            elif value is None:
                link[field] = None
            evidence['checks'].append({'field': field, 'status': 'inferred' if value is None and proof else
                                       'unknown' if value is None else 'explicit',
                                       'value': link[field], 'fact': fact_json(triple) if proof else None})
        if declared or owl_relations or any(check['status'] == 'unknown' for check in evidence['checks']):
            link['ontology_evidence'] = evidence
            link_evidence.append(evidence)
    return {'assertions': assertions, 'facts': records, 'links': link_evidence,
            'capabilities': {identifier: sorted(set(values)) for identifier, values in capabilities.items()},
            'scope': '本地主本体子属性、逆关系、对称关系的正向事实闭包；无传递通信推断，无数值补造，缺少事实为未知'}
