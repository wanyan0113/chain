"""Configurable scene attributes -> typed OWL data facts, without unit guessing."""
from contextvars import ContextVar
from decimal import Decimal

from jsonschema import Draft202012Validator
from rdflib import Literal, RDF, URIRef
from rdflib.namespace import OWL, XSD

from .vocabulary import KW

SCENE_MAPPINGS = ContextVar('arcane_scene_mappings', default=())
NUMERIC_TYPES = ('decimal', 'integer', 'double')
SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['mappings'],
    'properties': {'mappings': {'type': 'array', 'maxItems': 200, 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['id', 'kind', 'field', 'property', 'datatype', 'source_refs'],
        'properties': {
            'id': {'type': 'string', 'minLength': 1, 'pattern': r'\S'},
            'kind': {'enum': ['task', 'target', 'artifact']},
            'field': {'type': 'string', 'pattern': '^[A-Za-z][A-Za-z0-9_]*$'},
            'property': {'type': 'string', 'pattern': '^[A-Za-z][A-Za-z0-9+.-]*:'},
            'datatype': {'enum': ['string', 'boolean', *NUMERIC_TYPES]},
            'required': {'type': 'boolean'},
            'unit': {'type': 'string', 'minLength': 1, 'pattern': r'\S'},
            'source_refs': {'type': 'array', 'minItems': 1, 'uniqueItems': True,
                            'items': {'type': 'string', 'minLength': 1, 'pattern': r'\S'}}},
        'allOf': [{'if': {'properties': {'datatype': {'enum': list(NUMERIC_TYPES)}}},
                   'then': {'required': ['unit']}, 'else': {'not': {'required': ['unit']}}}]
    }}}
}


def validate_mappings(document, ontology):
    from .engine import DesignError
    errors = list(Draft202012Validator(SCHEMA).iter_errors(document))
    if errors:
        raise DesignError('本体事实映射配置无效：' + '; '.join(e.message for e in errors[:3]))
    identifiers, bindings = set(), set()
    for mapping in document['mappings']:
        key = mapping['kind'], mapping['field'], mapping['property']
        if mapping['id'] in identifiers or key in bindings:
            raise DesignError('本体事实映射编号或映射项重复')
        identifiers.add(mapping['id'])
        bindings.add(key)
        property_ = URIRef(mapping['property'])
        if (property_, RDF.type, OWL.DatatypeProperty) not in ontology:
            raise DesignError(f'映射属性须在当前本体声明为数据属性：{property_}')
        known_units = {str(unit) for unit in ontology.objects(property_, KW.unitSymbol)}
        if known_units and mapping.get('unit') not in known_units:
            raise DesignError(f'映射单位与本体unitSymbol不符：{property_}')
    return document['mappings']


def mapped_literal(mapping, value):
    """Unknown input stays absent; never convert units or fabricate a value."""
    if value is None:
        return None, '字段缺失或值为null'
    datatype = mapping['datatype']
    if datatype in NUMERIC_TYPES:
        if not isinstance(value, dict) or value.get('unit') != mapping['unit']:
            return None, '数值单位缺失或不一致，不自动换算'
        value = value.get('value')
        if type(value) not in (int, float) or (datatype == 'integer' and int(value) != value):
            return None, '数值类型不符合映射约定'
        value = Decimal(str(value)) if datatype == 'decimal' else int(value) if datatype == 'integer' else float(value)
    elif type(value) is not (str if datatype == 'string' else bool):
        return None, '属性类型不符合映射约定'
    return Literal(value, datatype=XSD[datatype]), '已按声明类型映射，未执行单位换算'


def project_facts(document, graph, scene_uri):
    evidence = []
    for mapping in SCENE_MAPPINGS.get():
        kind = mapping['kind']
        owners = document.get('artifacts', []) if kind == 'artifact' else document['tasks']
        for owner in owners:
            identifier = owner.get('target', owner['id']) if kind == 'target' else owner['id']
            node = scene_uri(kind, identifier)
            value = owner['attributes'].get(mapping['field'])
            literal, reason = mapped_literal(mapping, value)
            if owner['blocking_issue_ids']:
                literal, reason = None, '对象存在阻塞疑点，不断言该属性事实'
            record = {'mapping': mapping['id'], 'kind': kind, 'owner': owner['id'],
                      'subject': str(node), 'field': mapping['field'], 'property': mapping['property'],
                      'datatype': mapping['datatype'], 'unit': mapping.get('unit'), 'value': value,
                      'required': mapping.get('required', True),
                      'status': 'pass' if literal is not None else 'unknown', 'reason': reason,
                      'source_refs': mapping['source_refs'] + owner['source_refs']}
            if literal is not None:
                graph.add((node, URIRef(mapping['property']), literal))
            evidence.append(record)
    return evidence
