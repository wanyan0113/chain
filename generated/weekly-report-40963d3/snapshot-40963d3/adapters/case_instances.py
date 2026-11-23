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
from source_data.case_contract import CANONICAL_UNITS, UNIT_FACTORS, prepare_case, canonical_number
from source_data.common import resolve

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


def map_case_instances(bundle, *, bindings=None, reasoner=False):
    """Return a source-linked graph and report; never repair source claims."""
    checked = validate_bundle(bundle)
    if checked['counts']['ERROR']:
        raise ValidationError('; '.join(checked['errors']))
    prepared, cleaning = prepare_case(bundle['metadata']['raw_document'])
    tables = {t['id']: t for t in bundle['tables']}
    groups = {name: [] for name in ('table7', 'table8', 'table9', 'table11')}
    for record in bundle['records']:
        table = tables[record['table_id']]['input_path']
        if table not in groups:
            raise ValidationError(f'未支持的实例表：{table}')
        groups[table].append({**record, 'raw_object': resolve(prepared, record['input_path'])})
    if any(not rows for rows in groups.values()):
        raise ValidationError('实例映射需要四张非空案例表')
    if len(groups['table11']) != 1:
        raise ValidationError('实例入口目前只支持一条任务记录')
    bindings = bindings if bindings is not None else {'format_version': 1, 'tasks': []}
    if not isinstance(bindings, dict) or set(bindings) != {'format_version', 'tasks'} or bindings['format_version'] != 1 or not isinstance(bindings['tasks'], list):
        raise ValidationError('实例关系配置需要 format_version=1 和 tasks 数组')
    digest = hashlib.sha256(dumps({'version': 2, 'bundle': bundle, 'bindings': bindings}).encode()).hexdigest()
    prefix = f'urn:case-instance:{digest}:'
    graph = bundle_graph(bundle)
    graph.bind('kw', KW)
    graph.bind('case', META)
    graph.bind('prov', PROV)
    entities = {}
    claims = []
    identity_values = set()
    values = {(v['record_id'], v['input_path'].rsplit('.', 1)[1]): v for v in bundle['values']}

    def text(row, key):
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f'实例字段 {key} 必须为非空文本')
        return value.strip()

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

    def claim(node, prop, value, record, key, conversion=None):
        source_value = values[(record['id'], key)]
        graph.add((node, prop, value))
        statement = URIRef(prefix + 'claim:' + str(len(claims) + 1))
        graph.add((statement, RDF.type, RDF.Statement))
        graph.add((statement, RDF.subject, node))
        graph.add((statement, RDF.predicate, prop))
        graph.add((statement, RDF.object, value))
        graph.add((statement, PROV.wasDerivedFrom, URIRef(source_iri(source_value['id']))))
        entry = {'entity': str(node), 'property': str(prop), 'source_value': source_value['id'],
                 'normalized_value': value.toPython() if isinstance(value, Literal) else str(value)}
        if isinstance(entry['normalized_value'], Decimal):
            entry['normalized_value'] = str(entry['normalized_value'])
        if conversion:
            entry['conversion'] = conversion
            graph.add((statement, META.normalizationPolicy, Literal(dumps(conversion))))
        claims.append(entry)

    def numeric(row, key, unit=None):
        value = row.get(key)
        if type(value) not in (int, float) or not Decimal(str(value)).is_finite() or value < 0:
            raise ValidationError(f'实例字段 {key} 必须为非负有限数值')
        number = Decimal(str(value))
        if unit:
            declared = prepared['units'][unit]
            number = canonical_number(value, unit, declared)
        return Literal(number, datatype=XSD.decimal)

    def numeric_claim(node, prop, record, key, unit):
        declared = prepared['units'][unit]
        conversion = {'policy': 'declared_unit_conversion_v1', 'input_unit': declared,
                      'output_unit': CANONICAL_UNITS[unit], 'factor': str(UNIT_FACTORS[unit][declared])}
        claim(node, prop, numeric(record['raw_object'], key, unit), record, key, conversion)

    def properties(node, record, table):
        row = record['raw_object']
        for key, (prop, unit) in PROPERTIES.get(table, {}).items():
            numeric_claim(node, KW[prop], record, key, unit)
        if table == 'table9':
            numeric_claim(node, KW.attunementProbability, record, 'attunement_probability_percent', 'attunement_probability')
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
        identity_values.add(values[(record['id'], 'id')]['id'])
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
        identity_values.add(values[(record['id'], 'platform')]['id'])
        platform = entity('platform', code, KW.ArtifactPlatform)
        platforms[code] = platform
        link_source(platform, record)
        claim(platform, KW.artifactId, Literal(code, datatype=XSD.string), record, 'platform')
        numeric_claim(platform, KW.altitudeFt, record, 'altitude_ft', 'altitude')
    seen_resources = set()
    for table, id_key, cls in [('table8', 'insight_artifact', KW.InsightArtifact),
                               ('table9', 'manifestation_artifact', KW.ManifestationArtifact)]:
        for record in groups[table]:
            row = record['raw_object']
            code = text(row, id_key)
            identity_values.add(values[(record['id'], id_key)]['id'])
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
            # A named witness for the broad capability already entailed by the
            # resource class. This is classification, not suitability inference.
            cap_type = KW.InsightCapability if table == 'table8' else KW.ManifestationCapability
            cap = entity('capability', code + '/declared-role', cap_type)
            link_source(cap, record)
            claim(node, KW.hasCapability, cap, record, id_key,
                  {'policy': 'resource_role_classification_v1', 'table': table})
            properties(node, record, table)
    missions = {}
    for record in groups['table11']:
        row = record['raw_object']
        name = text(row, 'mission')
        identity_values.add(values[(record['id'], 'mission')]['id'])
        node = entity('mission', name, KW.ArcaneMission)
        missions[record['input_path']] = (node, record)
        link_source(node, record)
        claim(node, KW.missionName, Literal(name, datatype=XSD.string), record, 'mission')
        claim(node, KW.missionTargetType, Literal(text(row, 'target_type'), datatype=XSD.string), record, 'target_type')
        properties(node, record, 'table11')
    material_keys = bundle['metadata']['mapping']['materials_path']
    materials = {m['id'].split('/material/' + material_keys + '.', 1)[-1]: m for m in bundle['materials']}
    binding_status = []
    configured_tasks = set()
    def binding_sources(node, spec):
        keys = spec.get('source_keys')
        if not isinstance(keys, list) or not keys or any(not isinstance(k, str) or k not in materials for k in keys):
            raise ValidationError('显式实例关系必须引用已登记的 source_keys 材料键')
        for key in keys:
            graph.add((node, PROV.wasDerivedFrom, URIRef(source_iri(materials[key]['id']))))
    for task in bindings['tasks']:
        if not isinstance(task, dict) or set(task) != {'record_path', 'target', 'capabilities'}:
            raise ValidationError('任务关系配置字段不符')
        path = task['record_path']
        if not isinstance(path, str) or path not in missions or path in configured_tasks:
            raise ValidationError('任务关系配置引用不存在或重复的任务记录')
        configured_tasks.add(path)
        mission, record = missions[path]
        target = task['target']
        if target is not None:
            if not isinstance(target, dict) or set(target) != {'id', 'source_keys'}:
                raise ValidationError('目标配置需要 id 和 source_keys')
            target_node = entity('target', text(target, 'id'), KW.Target)
            link_source(target_node, record)
            binding_sources(target_node, target)
            claim(mission, KW.hasTarget, target_node, record, 'mission',
                  {'policy': 'explicit_task_binding_v1', 'source_keys': target['source_keys']})
        if not isinstance(task['capabilities'], list):
            raise ValidationError('任务能力配置必须为数组')
        seen_capabilities = set()
        for spec in task['capabilities']:
            if not isinstance(spec, dict) or set(spec) != {'id', 'type', 'source_keys'}:
                raise ValidationError('能力配置需要 id、type 和 source_keys')
            code = text(spec, 'id')
            if code in seen_capabilities or spec['type'] not in ('InsightCapability', 'CoordinationCapability', 'ManifestationCapability'):
                raise ValidationError('任务能力编号重复或类型不受支持')
            seen_capabilities.add(code)
            cap = entity('task-capability', path + '/' + code, KW[spec['type']])
            link_source(cap, record)
            binding_sources(cap, spec)
            claim(mission, KW.requiresCapability, cap, record, 'mission',
                  {'policy': 'explicit_task_binding_v1', 'source_keys': spec['source_keys']})
        binding_status.append({'record_path': path, 'target': 'mapped' if target else 'needs_confirmation',
                               'capabilities': 'mapped' if task['capabilities'] else 'needs_confirmation'})
    for path in missions.keys() - configured_tasks:
        binding_status.append({'record_path': path, 'target': 'needs_confirmation', 'capabilities': 'needs_confirmation'})
    # Static ontology compatibility, not OWL reasoning or business feasibility.
    ontology = Graph().parse(ROOT / 'arcane_ontology.ttl', format='turtle')
    review = analyze_graph(ontology + graph)
    if review.counts()['ERROR'] or review.counts()['WARN']:
        raise ValidationError(f'实例本体结构检查失败：{review.items}')
    reasoner_report = {'status': 'not_run', 'reason': '未请求HermiT检查'}
    if reasoner:
        from .owl_check import check_owl
        reasoner_report = check_owl(ontology + graph)
        if reasoner_report['status'] != 'passed':
            raise ValidationError(f'HermiT检查未通过或未执行：{dumps(reasoner_report)}')
    mapped_values = {c['source_value'] for c in claims}
    for item in entities.values():
        item['attributes'] = [
            {'property': c['property'].rsplit('#', 1)[-1], 'value': c['normalized_value'],
             'unit': c.get('conversion', {}).get('output_unit'),
             'source_value': c['source_value']}
            for c in claims if c['entity'] == item['iri'] and c['property'] not in {
                str(KW.hosts), str(KW.hasCapability), str(KW.requiresCapability), str(KW.hasTarget)}
        ]
        item['relations'] = [
            {'relation': '承载于', 'other': str(graph.value(p, RDFS.label))}
            for p in graph.subjects(KW.hosts, URIRef(item['iri']))
        ] + [
            {'relation': '承载', 'other': str(graph.value(p, RDFS.label))}
            for p in graph.objects(URIRef(item['iri']), KW.hosts)
        ]
        for prop, label in [(KW.hasCapability, '具备能力'), (KW.requiresCapability, '需要能力'), (KW.hasTarget, '关联目标')]:
            item['relations'] += [{'relation': label, 'other': str(graph.value(p, RDFS.label))}
                                  for p in graph.objects(URIRef(item['iri']), prop)]
    coverage = [{'id': v['id'], 'path': v['input_path'],
                 'status': 'identity' if v['id'] in identity_values else 'property' if v['id'] in mapped_values else 'source_only',
                 'reason': '原始坐标含方向疑点，待确认' if v['input_path'].endswith(('_column_raw')) else
                           '精度定义及标度待确认' if v['input_path'].endswith('precision_raw') else
                           '物理含义或量纲待确认' if v['id'] not in mapped_values | identity_values else '已使用'}
                for v in bundle['values']]
    unmapped = [v for v in coverage if v['status'] == 'source_only']
    report = {'status': 'valid', 'mapping_version': 2, 'entities': list(entities.values()),
              'counts': {'entities': len(entities), 'claims': len(claims), 'unmapped_values': len(unmapped),
                         'source_values': len(coverage), 'identity_values': len(identity_values),
                         'property_values': sum(v['status'] == 'property' for v in coverage)},
              'coverage': coverage, 'cleaning': cleaning,
              'task_bindings': binding_status,
              'bindings': bindings,
              'coverage_status': 'pending_confirmation' if unmapped or any(
                  x['target'] != 'mapped' or x['capabilities'] != 'mapped' for x in binding_status) else 'complete',
              'reasoner_report': reasoner_report,
              'validation_scope': {'shared_case_contract': 'passed', 'static_ontology': 'passed',
                                   'owl_reasoner': reasoner_report['status'], 'external_source_truth': 'not_verified'},
              'claims': claims, 'unmapped_values': unmapped,
              'source_issues': checked['counts']['open_issues'], 'source_gaps': checked['counts']['open_gaps'],
              'ontology_check': review.counts(),
              'notes': ['实例映射及结构检查通过；来源疑点仍保留。',
                        '坐标、精度、未确认量纲及正文关系仍在来源层，未自动解释。',
                        '数量行保存为集合描述，不按数量补造个体；不从任务名称猜测目标编号。',
                        '能力实例仅表示资源所属功能类别；任务关系只接受有来源的显式配置。',
                        '未进行能力适用性推理、方案设计或业务可行性判定。']}
    return graph, report


def instance_outputs(graph, report):
    """Both a data-only graph and a directly openable ontology document."""
    combined = Graph().parse(ROOT / 'arcane_ontology.ttl', format='turtle') + graph
    combined.parse(ROOT / 'source_data/ontology.ttl', format='turtle')
    return {
        'instances.ttl': graph.serialize(format='turtle'),
        'ontology_with_instances.ttl': combined.serialize(format='turtle'),
        'instance_report.json': json.dumps(report, ensure_ascii=False, indent=2) + '\n',
        'instance_summary.md': '\n'.join([
            '# 案例实例映射检查', '',
            f"映射状态：{report['status']}；字段覆盖状态：{report['coverage_status']}。",
            f"实例条目：{report['counts']['entities']}。",
            f"字段值：{report['counts']['source_values']}；编号使用：{report['counts']['identity_values']}；属性映射：{report['counts']['property_values']}；仅保留原值：{report['counts']['unmapped_values']}。",
            f"HermiT：{report['validation_scope']['owl_reasoner']}。",
            f"本次自动标记：{len(report['cleaning']['findings'])} 项；文本或列表规范化：{len(report['cleaning']['changes'])} 处。", '',
            '## 仍待确认的字段', '',
            *[f"- {v['path']}：{v['reason']}" for v in report['unmapped_values']], '',
            '## 任务关系', '',
            *[f"- {v['record_path']}：目标 {v['target']}；能力 {v['capabilities']}。" for v in report['task_bindings']], '',
            *report['notes'], '',
        ]),
    }


def main():
    import argparse
    from source_data.common import read_json, write_outputs
    parser = argparse.ArgumentParser(description='将案例来源包映射为可追溯的本体实例')
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--bindings', type=Path, help='有明确来源的任务—目标及任务—能力配置')
    parser.add_argument('--reasoner', action='store_true', help='同时执行HermiT检查，需要Java及可选依赖')
    args = parser.parse_args()
    try:
        graph, report = map_case_instances(read_json(args.bundle), bindings=read_json(args.bindings) if args.bindings else None, reasoner=args.reasoner)
        write_outputs(args.output_dir, instance_outputs(graph, report), protected=[args.bundle, *([args.bindings] if args.bindings else [])])
        print(json.dumps({'status': report['status'], 'counts': report['counts']}, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'invalid', 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
