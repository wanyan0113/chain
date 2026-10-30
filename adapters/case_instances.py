"""Map source records to descriptive ontology instances, without design rules."""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

from check_ontology import analyze_graph
from source_data.common import ValidationError, dumps, source_iri
from source_data.load import bundle_graph
from source_data.validate import validate_bundle

ROOT = Path(__file__).resolve().parents[1]
KW = Namespace('http://example.org/arcaneweb#')
META = Namespace('urn:case-instance-mapping:')
PROV = Namespace('http://www.w3.org/ns/prov#')

# Only direct, unambiguous properties. Coordinates and precision stay in the
# source layer; their interpretation has not been confirmed.
PROPERTIES = {
    'table8': {'detection_range_nm': ('detectionRangeNm', 'distance')},
    'table9': {
        'max_range_nm': ('maxRangeNm', 'distance'),
        'min_range_nm': ('minRangeNm', 'distance'),
        'max_target_speed_kn': ('maxTargetSpeedKn', 'speed'),
        'min_target_altitude_ft': ('minTargetAltitudeFt', 'altitude'),
        'max_target_altitude_ft': ('maxTargetAltitudeFt', 'altitude'),
        'min_activation_altitude_ft': ('minActivationAltitudeFt', 'altitude'),
        'max_activation_altitude_ft': ('maxActivationAltitudeFt', 'altitude'),
    },
    'table11': {
        'target_altitude_ft': ('missionTargetAltitudeFt', 'altitude'),
        'target_speed_kn': ('missionTargetSpeedKn', 'speed'),
    },
}
UNITS = {'distance': 'n mile', 'altitude': 'ft', 'speed': 'kn'}


def map_case_instances(bundle):
    """Return a source-linked graph and report; never repair source claims."""
    checked = validate_bundle(bundle)
    if checked['counts']['ERROR']:
        raise ValidationError('; '.join(checked['errors']))
    tables = {t['id']: t for t in bundle['tables']}
    groups = {name: [] for name in ('table7', 'table8', 'table9', 'table11')}
    for record in bundle['records']:
        table = tables[record['table_id']]['input_path']
        if table not in groups:
            raise ValidationError(f'未支持的实例表：{table}')
        groups[table].append(record)
    if any(not rows for rows in groups.values()):
        raise ValidationError('实例映射需要四张非空案例表')
    if len(groups['table11']) != 1:
        raise ValidationError('实例入口目前只支持一条任务记录')
    digest = hashlib.sha256(dumps({'version': 1, 'bundle': bundle}).encode()).hexdigest()
    prefix = f'urn:case-instance:{digest}:'
    graph = bundle_graph(bundle)
    graph.bind('kw', KW)
    graph.bind('case', META)
    graph.bind('prov', PROV)
    entities = {}
    claims = []
    values = {(v['record_id'], v['input_path'].rsplit('.', 1)[1]): v for v in bundle['values']}

    def text(row, key):
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f'实例字段 {key} 必须为非空文本')
        return value

    def entity(kind, code, cls):
        key = kind, code
        if key not in entities:
            node = URIRef(prefix + kind + ':' + quote(code, safe=''))
            graph.add((node, RDF.type, cls))
            graph.add((node, RDFS.label, Literal(code)))
            entities[key] = {'iri': str(node), 'code': code, 'kind': kind,
                             'type': str(cls), 'source_records': []}
        return URIRef(entities[key]['iri'])

    def link_source(node, record):
        ref = URIRef(source_iri(record['id']))
        graph.add((node, PROV.wasDerivedFrom, ref))
        item = next(e for e in entities.values() if e['iri'] == str(node))
        if record['id'] not in item['source_records']:
            item['source_records'].append(record['id'])
        for material in tables[record['table_id']]['material_ids']:
            graph.add((node, RDFS.seeAlso, URIRef(source_iri(material))))
        for issue in bundle['issues']:
            if record['id'] in issue['record_ids'] or record['table_id'] in issue['table_ids']:
                graph.add((node, META.registeredIssue, URIRef(source_iri(issue['id']))))

    def claim(node, prop, value, record, key):
        source_value = values[(record['id'], key)]
        graph.add((node, prop, value))
        statement = URIRef(prefix + 'claim:' + str(len(claims) + 1))
        graph.add((statement, RDF.type, RDF.Statement))
        graph.add((statement, RDF.subject, node))
        graph.add((statement, RDF.predicate, prop))
        graph.add((statement, RDF.object, value))
        graph.add((statement, PROV.wasDerivedFrom, URIRef(source_iri(source_value['id']))))
        claims.append({'entity': str(node), 'property': str(prop), 'source_value': source_value['id']})

    def numeric(row, key):
        value = row.get(key)
        if type(value) not in (int, float) or not Decimal(str(value)).is_finite() or value < 0:
            raise ValidationError(f'实例字段 {key} 必须为非负有限数值')
        return Literal(Decimal(str(value)), datatype=XSD.decimal)

    def properties(node, record, table):
        row = record['raw_object']
        for key, (prop, unit) in PROPERTIES.get(table, {}).items():
            if bundle['metadata']['raw_document']['units'].get(unit) != UNITS[unit]:
                raise ValidationError(f'{key} 单位不符；当前实例映射要求 {UNITS[unit]}')
            claim(node, KW[prop], numeric(row, key), record, key)
        if table in ('table8', 'table9'):
            types = row.get('target_types')
            if not isinstance(types, list) or not types or any(not isinstance(t, str) or not t.strip() for t in types):
                raise ValidationError('target_types 必须为非空文本数组')
            prop = KW.insightTargetType if table == 'table8' else KW.manifestationTargetType
            for value in types:
                claim(node, prop, Literal(value, datatype=XSD.string), record, 'target_types')

    # A quantity row is a collection description, not N invented individuals.
    for record in groups['table7']:
        row = record['raw_object']
        code = text(row, 'id')
        node = entity('collection', code, META.ObjectCollection)
        count = row.get('count')
        if type(count) is not int or count < 0:
            raise ValidationError('对象数量必须为非负整数')
        link_source(node, record)
        claim(node, META.listedCount, Literal(count, datatype=XSD.integer), record, 'count')
        claim(node, RDFS.label, Literal(text(row, 'name')), record, 'name')
        claim(node, META.side, Literal(text(row, 'side')), record, 'side')

    # Table8 explicitly declares platforms. Table9 must reference one of them.
    platforms = {}
    for record in groups['table8']:
        row = record['raw_object']
        code = text(row, 'platform')
        platform = entity('platform', code, KW.ArtifactPlatform)
        platforms[code] = platform
        link_source(platform, record)
        claim(platform, KW.artifactId, Literal(code, datatype=XSD.string), record, 'platform')
        claim(platform, KW.altitudeFt, numeric(row, 'altitude_ft'), record, 'altitude_ft')
    seen_resources = set()
    for table, id_key, cls in [('table8', 'insight_artifact', KW.InsightArtifact),
                               ('table9', 'manifestation_artifact', KW.ManifestationArtifact)]:
        for record in groups[table]:
            row = record['raw_object']
            code = text(row, id_key)
            if code in seen_resources or code in platforms:
                raise ValidationError(f'资源编号重复或与平台冲突：{code}')
            seen_resources.add(code)
            platform_code = text(row, 'platform')
            if platform_code not in platforms:
                raise ValidationError(f'平台引用不存在：{platform_code}（应在探测能力表登记）')
            node = entity('resource', code, cls)
            link_source(node, record)
            claim(node, KW.artifactId, Literal(code, datatype=XSD.string), record, id_key)
            # hosts expresses containment, without inventing detachable mounting.
            claim(platforms[platform_code], KW.hosts, node, record, 'platform')
            properties(node, record, table)
    for record in groups['table11']:
        row = record['raw_object']
        name = text(row, 'mission')
        node = entity('mission', name, KW.ArcaneMission)
        link_source(node, record)
        claim(node, KW.missionName, Literal(name, datatype=XSD.string), record, 'mission')
        claim(node, KW.missionTargetType, Literal(text(row, 'target_type'), datatype=XSD.string), record, 'target_type')
        properties(node, record, 'table11')
    # Static ontology compatibility, not OWL reasoning or business feasibility.
    ontology = Graph().parse(ROOT / 'arcane_ontology.ttl', format='turtle')
    review = analyze_graph(ontology + graph)
    if review.counts()['ERROR'] or review.counts()['WARN']:
        raise ValidationError(f'实例本体结构检查失败：{review.items}')
    mapped_values = {c['source_value'] for c in claims}
    for item in entities.values():
        item['relations'] = [
            {'relation': '承载于', 'other': str(graph.value(p, RDFS.label))}
            for p in graph.subjects(KW.hosts, URIRef(item['iri']))
        ] + [
            {'relation': '承载', 'other': str(graph.value(p, RDFS.label))}
            for p in graph.objects(URIRef(item['iri']), KW.hosts)
        ]
    unmapped = [{'id': v['id'], 'path': v['input_path'], 'reason': '保留在来源层；本次未建立业务属性映射'}
                for v in bundle['values'] if v['id'] not in mapped_values]
    report = {'status': 'valid', 'mapping_version': 1, 'entities': list(entities.values()),
              'counts': {'entities': len(entities), 'claims': len(claims), 'unmapped_values': len(unmapped)},
              'claims': claims, 'unmapped_values': unmapped,
              'source_issues': checked['counts']['open_issues'], 'source_gaps': checked['counts']['open_gaps'],
              'ontology_check': review.counts(),
              'notes': ['实例映射及结构检查通过；来源疑点仍保留。',
                        '坐标、精度、未确认量纲及正文关系仍在来源层，未自动解释。',
                        '数量行保存为集合描述，不按数量补造个体；不从任务名称猜测目标编号。',
                        '未进行能力推理、方案设计或业务可行性判定。']}
    return graph, report


def instance_outputs(graph, report):
    """Both a data-only graph and a directly openable ontology document."""
    combined = Graph().parse(ROOT / 'arcane_ontology.ttl', format='turtle') + graph
    combined.parse(ROOT / 'source_data/ontology.ttl', format='turtle')
    return {
        'instances.ttl': graph.serialize(format='turtle'),
        'ontology_with_instances.ttl': combined.serialize(format='turtle'),
        'instance_report.json': json.dumps(report, ensure_ascii=False, indent=2) + '\n',
    }


def main():
    import argparse
    from source_data.common import read_json, write_outputs
    parser = argparse.ArgumentParser(description='将案例来源包映射为可追溯的本体实例')
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        graph, report = map_case_instances(read_json(args.bundle))
        write_outputs(args.output_dir, instance_outputs(graph, report), protected=[args.bundle])
        print(json.dumps({'status': report['status'], 'counts': report['counts']}, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'invalid', 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
