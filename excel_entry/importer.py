"""Read a deliberately small, explicit XLSX contract using only the stdlib.

No formulas/macros are executed. Source values remain original values. This
adapter checks entry types; the historical backend checks the source bundle.
"""
from __future__ import annotations

import io
import json
import math
import posixpath
import re
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

from .schema import TABLES, META_HEADERS, META_LABELS, FACT_HEADERS, ISSUE_HEADERS, INSTRUCTIONS

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'


class EntryError(ValueError):
    pass


def read_xlsx(data: bytes) -> dict:
    if len(data) > 10_000_000:
        raise EntryError('文件超过10MB，请只保留录入表格。')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if len(z.infolist()) > 500 or sum(x.file_size for x in z.infolist()) > 40_000_000:
                raise EntryError('工作簿内容过大。')
            if any(x.endswith('vbaProject.bin') for x in z.namelist()):
                raise EntryError('不支持含宏工作簿，请另存为.xlsx。')
            strings = []
            if 'xl/sharedStrings.xml' in z.namelist():
                strings = [''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si', NS)]
            rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
            result = {}
            for sheet in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet', NS):
                name = sheet.attrib['name']
                target = rels[sheet.attrib[REL]]
                path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
                if not path.startswith('xl/'):
                    raise EntryError('工作表引用无效。')
                rows = {}
                for c in ET.fromstring(z.read(path)).findall('s:sheetData/s:row/s:c', NS):
                    cell = c.attrib['r']
                    match = re.fullmatch(r'([A-Z]+)([1-9][0-9]*)', cell)
                    if not match:
                        raise EntryError(f'{name}：无效单元格。')
                    letters, row = match.groups()
                    col = 0
                    for ch in letters:
                        col = col * 26 + ord(ch) - 64
                    row = int(row)
                    if row > 5001 or col > 50:
                        raise EntryError(f'{name}!{cell}：只支持5000条数据、50列以内的表。')
                    if c.find('s:f', NS) is not None:
                        raise EntryError(f'{name}!{cell}：请填原始值，不要使用公式。')
                    typ = c.attrib.get('t', 'n')
                    v = c.findtext('s:v', default='', namespaces=NS)
                    if typ == 'inlineStr':
                        value = ''.join(c.find('s:is', NS).itertext())
                    elif typ == 's':
                        value = strings[int(v)]
                    elif typ in ('str', 'd'):
                        value = v
                    elif typ in ('b', 'e'):
                        raise EntryError(f'{name}!{cell}：不支持布尔值或Excel错误值。')
                    elif not v:
                        continue
                    else:
                        number = float(v)
                        if not math.isfinite(number):
                            raise EntryError(f'{name}!{cell}：数字必须有限。')
                        value = int(number) if number.is_integer() else number
                    if value != '':
                        rows.setdefault(row, {})[col] = value
                result[name] = rows
            return result
    except EntryError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError, ET.ParseError, zipfile.BadZipFile, RuntimeError, IndexError) as exc:
        raise EntryError('无法读取.xlsx，请使用提供的模板并在Excel/WPS中保存。') from exc


def records(sheets, name, headers):
    if name not in sheets:
        raise EntryError(f'缺少工作表：{name}')
    rows = sheets[name]
    if rows.get(1, {}) != {i + 1: h for i, h in enumerate(headers)}:
        raise EntryError(f'{name}第1行列名不符，请保留模板列名和顺序。')
    for rownum, cells in sorted(rows.items()):
        if rownum == 1 or not cells:
            continue
        if any(c > len(headers) for c in cells):
            raise EntryError(f'{name}第{rownum}行：存在额外列，请使用模板列。')
        yield rownum, [cells.get(i + 1) for i in range(len(headers))]


def text(value, where, optional=False):
    if optional and value is None:
        return ''
    if not isinstance(value, str) or not value.strip():
        raise EntryError(f'{where}：请填写文本，不能为空。')
    return value


def convert(sheets):
    """Convert all visible sheets, never borrow hidden data from the old case."""
    allowed = {x[0] for x in TABLES.values()} | {'案例说明', '事实', '疑点', '填写指南'}
    extra = set(sheets) - allowed
    if extra:
        raise EntryError('存在模板以外工作表：' + '、'.join(sorted(extra)))
    d = {'source_files': {}, 'units': {}, 'identity_mapping': {}, 'explicit_facts': [], 'issues': [], 'missing_facts': []}
    seen = set()
    groups = {'来源': 'source_files', '单位': 'units', '名称对应': 'identity_mapping'}
    for row, (kind, key, value) in records(sheets, '案例说明', META_HEADERS):
        where = f'案例说明第{row}行'
        kind, key, value = [text(v, where) for v in (kind, key, value)]
        key = {v: k for k, v in META_LABELS.get(kind, {}).items()}.get(key, key)
        if (kind, key) in seen:
            raise EntryError(f'{where}：类别和名称重复。')
        seen.add((kind, key))
        if kind in groups:
            d[groups[kind]][key] = value
        elif kind == '缺失事实':
            d['missing_facts'].append(value)
        elif kind == '基本信息' and key in ('checked_on', 'scope', 'source_alignment_policy'):
            d[key] = value
        else:
            raise EntryError(f'{where}：未知类别或名称。请参考示例。')
    for key in ('checked_on', 'scope'):
        if key not in d:
            raise EntryError(f'案例说明：缺少基本信息 {META_LABELS["基本信息"][key]}。')
    try:
        date.fromisoformat(d['checked_on'])
    except ValueError as exc:
        raise EntryError('核对日期请填YYYY-MM-DD文本，例如2026-10-27。') from exc
    for key in ('table7', 'table8_table9', 'table11', 'case_text'):
        if not d['source_files'].get(key):
            raise EntryError(f'案例说明：请填写「{META_LABELS["来源"][key]}」的实际资料文件名称或路径。')
    for key in META_LABELS['单位']:
        if key not in d['units']:
            raise EntryError(f'案例说明：缺少「{META_LABELS["单位"][key]}」单位说明。')
    for key, expected in {'altitude': 'ft', 'distance': 'n mile', 'speed': 'kn', 'attunement_probability': '%'}.items():
        if d['units'][key] != expected:
            raise EntryError(f'案例说明：「{META_LABELS["单位"][key]}」须使用模板单位 {expected}；本入口不自动换算单位。')
    index = {}
    for table, (name, identity, fields) in TABLES.items():
        out, ids = [], set()
        for row, values in records(sheets, name, [f[1] for f in fields]):
            record = {}
            for (key, label, typ), value in zip(fields, values):
                where = f'{name}第{row}行「{label}」'
                if typ in ('text', 'list'):
                    value = text(value, where)
                    if typ == 'list':
                        value = [x.strip() for x in re.split('[;；]', value) if x.strip()]
                        if not value:
                            raise EntryError(f'{where}：至少填写一个目标类型。')
                else:
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                        raise EntryError(f'{where}：请填写数字，空白不能当作0。')
                    if typ == 'int' and (value < 0 or int(value) != value):
                        raise EntryError(f'{where}：数量须为非负整数。')
                    if key == 'attunement_probability_percent' and not 0 <= value <= 100:
                        raise EntryError(f'{where}：概率须在0至100之间。')
                record[key] = value
            id_value = record[identity]
            if id_value in ids:
                raise EntryError(f'{name}第{row}行：编号重复 {id_value}。')
            if '::' in id_value or '\n' in id_value:
                raise EntryError(f'{name}第{row}行：编号不能含::或换行。')
            ids.add(id_value)
            path = table if table == 'table11' else f'{table}[{len(out)}]'
            for key, label, _ in fields:
                index[f'{name}::{id_value}::{label}'] = f'{path}.{key}'
            out.append(record)
        if not out:
            raise EntryError(f'{name}：至少填写一条记录。')
        if table == 'table11' and len(out) != 1:
            raise EntryError('任务：当前入口只支持一条任务记录。')
        d[table] = out[0] if table == 'table11' else out
        index[name] = table
    index.update({'事实': 'explicit_facts', '缺失事实': 'missing_facts'})
    for row, values in records(sheets, '事实', FACT_HEADERS):
        d['explicit_facts'].append(dict(zip(['subject', 'relation', 'object', 'source'], [text(v, f'事实第{row}行') for v in values])))
    for row, values in records(sheets, '疑点', ISSUE_HEADERS):
        values = [text(v, f'疑点第{row}行', optional=i == 4) for i, v in enumerate(values)]
        code, record, detail, status, alignment, filenames, refs = values
        paths = []
        for ref in refs.splitlines():
            if ref not in index:
                raise EntryError(f'疑点第{row}行：关联字段不存在 {ref}，请核对编号和列名。')
            paths.append(index[ref])
        files = filenames.splitlines()
        known = {f for val in d['source_files'].values() for f in val.split('、')}
        if any(f not in known for f in files):
            raise EntryError(f'疑点第{row}行：来源文件未在案例说明中登记。')
        issue = dict(code=code, record=record, detail=detail, status=status, source_files=files, field_paths=paths)
        if alignment:
            issue['source_alignment'] = alignment
        d['issues'].append(issue)
    return d


def workbook_spec(d, blank=False):
    """Builder input, with stable human-readable issue links instead of row offsets."""
    sheets = []
    reverse = {'explicit_facts': '事实', 'missing_facts': '缺失事实'}
    for table, (name, identity, fields) in TABLES.items():
        source = [d[table]] if table == 'table11' else d[table]
        rows = []
        for i, record in enumerate(source):
            path = table if table == 'table11' else f'{table}[{i}]'
            for key, label, _ in fields:
                reverse[f'{path}.{key}'] = f'{name}::{record[identity]}::{label}'
            rows.append([';'.join(record[k]) if typ == 'list' else record[k] for k, _, typ in fields])
        reverse[table] = name
        sheets.append({'name': name, 'rows': [[f[1] for f in fields]] + ([] if blank else rows)})
    meta = []
    for key in ('checked_on', 'scope', 'source_alignment_policy'):
        if key in d:
            value = d[key]
            if blank and key == 'source_alignment_policy':
                value = '按资料填写原始值，保留单位和待核对问题。'
            meta.append(['基本信息', META_LABELS['基本信息'][key], value])
    for kind, key in [('来源', 'source_files'), ('单位', 'units'), ('名称对应', 'identity_mapping')]:
        for k, v in d[key].items():
            if blank and kind == '名称对应':
                continue
            meta.append([kind, META_LABELS.get(kind, {}).get(k, k), None if blank and kind == '来源' else v])
    if not blank:
        meta += [['缺失事实', str(i+1), v] for i, v in enumerate(d['missing_facts'])]
    facts = [] if blank else [[v[k] for k in ('subject', 'relation', 'object', 'source')] for v in d['explicit_facts']]
    issues = [] if blank else [[v['code'], v['record'], v['detail'], v['status'], v.get('source_alignment', ''), '\n'.join(v['source_files']), '\n'.join(reverse[p] for p in v['field_paths'])] for v in d['issues']]
    sheets.extend([{'name': '案例说明', 'rows': [META_HEADERS] + meta}, {'name': '事实', 'rows': [FACT_HEADERS] + facts}, {'name': '疑点', 'rows': [ISSUE_HEADERS] + issues}, {'name': '填写指南', 'rows': INSTRUCTIONS}])
    return sheets


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    d = json.loads((root / 'data/article_source/case_records.json').read_text(encoding='utf-8'))
    out = root / 'tmp/excel-entry'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'spec.json').write_text(json.dumps({'example': workbook_spec(d), 'blank': workbook_spec(d, True)}, ensure_ascii=False), encoding='utf-8')
