"""One workbook for original source queries and explicit design configuration.

Bindings use stable source identities, preserve declared units and carry source
issues into the consuming owner. They never infer policies from article prose.
"""
from copy import deepcopy
import re

from .schema import TABLES
from .importer import EntryError, records

SOURCE_SHEETS = {item[0] for item in TABLES.values()} | {'案例说明', '事实', '疑点', '填写指南'}
BINDING_HEADERS = ['对象类型', '对象编号', '情境法器编号', '属性字段', '来源工作表', '来源编号', '来源字段']


def extension_headers():
    from design.excel import HEADERS
    return {('设计任务' if name == '任务' else '设计指南' if name == '填写指南' else name): headers
            for name, headers in HEADERS.items()} | {'来源取值': BINDING_HEADERS}


def source_sheets(sheets):
    """Validate extension structure before allowing the original query path."""
    extra = set(sheets) - SOURCE_SHEETS
    if extra:
        headers = extension_headers()
        if extra != set(headers):
            raise EntryError('综合模板设计工作表缺失或含未知表；请使用新版案例模板，保留所有工作表')
        for name, columns in headers.items():
            list(records(sheets, name, columns))
    return {name: rows for name, rows in sheets.items() if name in SOURCE_SHEETS}


def convert_design(sheets):
    from design.excel import convert
    from .importer import convert as convert_source
    from source_data.case_contract import NUMERIC_UNITS, prepare_case
    if not (set(sheets) - SOURCE_SHEETS):
        raise EntryError('这是旧版来源Excel，尚无设计配置。请使用新版案例模板补充设计设置、规则、协同关系和资源等工作表')
    original = convert_source(source_sheets(sheets))
    source, cleaning = prepare_case(original)
    names = {name: ('任务' if name == '设计任务' else '填写指南' if name == '设计指南' else name)
             for name in extension_headers() if name != '来源取值'}
    design_sheets = {target: deepcopy(sheets[name]) for name, target in names.items()}
    if len(design_sheets['任务']) == 1:
        raise EntryError('来源数据可查询，但设计配置尚未填写：请先填写设计设置、设计任务、法器、规则、协同关系等表；不要将未知数据补为0')
    # Build binding rows before conversion so duplicate manual/mapped attributes
    # are rejected by the same converter as ordinary duplicate attributes.
    index = {}
    for table, (name, identity, fields) in TABLES.items():
        values = [source[table]] if table == 'table11' else source[table]
        for i, record in enumerate(values):
            path = table if table == 'table11' else f'{table}[{i}]'
            for key, label, typ in fields:
                original_record = original[table] if table == 'table11' else original[table][i]
                index[name, record[identity], label] = (key, typ, record[key], path, table, original_record[key])
    bindings = []
    other_units = {'precision_raw': 'insight_precision', 'resonance_value': 'resonance_value',
                   'target_impedance_threshold': 'impedance_threshold'}
    source_keys = {'table7': 'table7', 'table8': 'table8_table9', 'table9': 'table8_table9', 'table11': 'table11'}
    for row, (kind, owner, context, field, sheet, identifier, label) in records(sheets, '来源取值', BINDING_HEADERS):
        where = f'来源取值第{row}行'
        if any(not isinstance(v, str) or not v.strip() for v in (kind, owner, field, sheet, identifier, label)):
            raise EntryError(f'{where}：对象、属性字段和来源定位不能为空')
        kind, owner, field, sheet, identifier, label = [v.strip() for v in (kind, owner, field, sheet, identifier, label)]
        if isinstance(context, str):
            context = context.strip()
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', field):
            raise EntryError(f'{where}：属性字段无效')
        item = index.get((sheet, identifier.strip(), label))
        if item is None:
            raise EntryError(f'{where}：来源记录或字段不存在，请按唯一编号引用，不按行号引用')
        key, typ, value, path, table, raw = item
        unit = None
        if typ in ('number', 'int'):
            unit_key = NUMERIC_UNITS.get(key) or other_units.get(key)
            unit = source['units'][unit_key] if unit_key else '1'
            typ = '数值'
        elif typ == 'list':
            value, typ = ';'.join(value), '列表'
        else:
            typ = '文本'
        rows = design_sheets['属性']
        rows[max(rows, default=1) + 1] = dict(enumerate([kind, owner, context, field, typ, value, unit], 1))
        field_path = f'{path}.{key}'
        issues = [issue['code'] for issue in source['issues']
                  if any(field_path == p or field_path.startswith(p + '.') or field_path.startswith(p + '[')
                         for p in issue['field_paths'])]
        # Automatic cleaning findings also remain blocking for a consumed value.
        issues += [f"source-{finding['code']}:{field_path}" for finding in cleaning['findings']
                   if field_path in finding['paths']]
        if unit and any(word in unit.lower() for word in ('未', '待', '不明', 'unknown', 'unconfirmed', '?')):
            issues.append(f'source-unit-unconfirmed:{field_path}')
        bindings.append(dict(kind=kind, owner=owner, context=context, field=field, source_sheet=sheet,
                             source_id=identifier, source_field=label, source_path=field_path,
                             raw_value=deepcopy(raw), unit=unit,
                             source_refs=[f'Excel:{sheet}::{identifier}::{label}', source['source_files'][source_keys[table]]],
                             blocking_issue_ids=list(dict.fromkeys(issues))))
    doc = convert(design_sheets)
    owners = {('任务', t['id'], None): t for t in doc['tasks']}
    owners.update({('法器', a['id'], None): a for a in doc['artifacts']})
    owners.update({('情境', c['task'], c['artifact']): c for c in doc['contexts']})
    for binding in bindings:
        owner = owners[binding['kind'], binding['owner'], binding['context']]
        for key in ('source_refs', 'blocking_issue_ids'):
            owner[key] = list(dict.fromkeys(owner[key] + binding[key]))
    return doc, {'format': 'combined-source-design-v1', 'bindings': bindings,
                 'source_case': original,
                 'scope': '仅显式来源取值参与属性转换；规则、关系、指标与资源由设计表提供，不自动从正文补全'}
