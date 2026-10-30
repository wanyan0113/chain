"""Shared checks and reversible normalization for the four-table case format."""
from copy import deepcopy
from decimal import Decimal
import math

from .common import ValidationError

FIELDS = {
    'table7': {'side': 'text', 'name': 'text', 'id': 'id', 'count': 'count'},
    'table8': {'platform': 'id', 'longitude_column_raw': 'text', 'latitude_column_raw': 'text',
               'altitude_ft': 'number', 'insight_artifact': 'id', 'target_types': 'list',
               'detection_range_nm': 'number', 'precision_raw': 'number'},
    'table9': {'platform': 'id', 'manifestation_artifact': 'id', 'target_types': 'list',
               'max_range_nm': 'number', 'min_range_nm': 'number', 'resonance_value': 'number',
               'max_target_speed_kn': 'number', 'min_target_altitude_ft': 'number',
               'max_target_altitude_ft': 'number', 'min_activation_altitude_ft': 'number',
               'max_activation_altitude_ft': 'number', 'attunement_probability_percent': 'probability'},
    'table11': {'mission': 'id', 'target_type': 'text', 'longitude_column_raw': 'text',
                'latitude_column_raw': 'text', 'target_altitude_ft': 'number',
                'target_speed_kn': 'number', 'target_impedance_threshold': 'number'},
}
IDENTITY = {'table7': 'id', 'table8': 'platform', 'table9': 'manifestation_artifact', 'table11': 'mission'}
# Decimal factors map declared input units to the ontology's canonical units.
UNIT_FACTORS = {
    'distance': {'n mile': Decimal(1), 'nmi': Decimal(1), 'km': Decimal(1) / Decimal('1.852'),
                 'm': Decimal(1) / Decimal(1852)},
    'altitude': {'ft': Decimal(1), 'm': Decimal(1) / Decimal('0.3048')},
    'speed': {'kn': Decimal(1), 'kt': Decimal(1), 'km/h': Decimal(1) / Decimal('1.852'),
              'm/s': Decimal(3600) / Decimal(1852)},
    'attunement_probability': {'%': Decimal('0.01'), '1': Decimal(1)},
}
CANONICAL_UNITS = {'distance': 'n mile', 'altitude': 'ft', 'speed': 'kn', 'attunement_probability': '1'}


def validate_case_document(document):
    """Both Excel and direct bundle mapping call this exact case contract."""
    if not isinstance(document, dict):
        raise ValidationError('案例必须为对象')
    units = document.get('units')
    if not isinstance(units, dict):
        raise ValidationError('案例缺少单位目录')
    for key, factors in UNIT_FACTORS.items():
        if not isinstance(units.get(key), str) or units[key] not in factors:
            raise ValidationError(f'不支持的{key}单位：{units.get(key)}；可用：{list(factors)}')
    probability_max = 100 if units['attunement_probability'] == '%' else 1
    for table, fields in FIELDS.items():
        rows = [document.get(table)] if table == 'table11' else document.get(table)
        if not isinstance(rows, list) or not rows:
            raise ValidationError(f'{table} 必须有记录')
        seen = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or set(row) != set(fields):
                raise ValidationError(f'{table}[{index}] 字段缺失或存在未定义字段')
            for key, kind in fields.items():
                value = row[key]
                where = f'{table}[{index}].{key}'
                if kind in ('text', 'id'):
                    if not isinstance(value, str) or not value.strip():
                        raise ValidationError(f'{where} 必须为非空文本')
                    if kind == 'id' and ('::' in value or '\n' in value or '\r' in value):
                        raise ValidationError(f'{where} 编号不能含::或换行')
                elif kind == 'list':
                    if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v.strip() for v in value):
                        raise ValidationError(f'{where} 必须为非空文本数组')
                else:
                    if type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)) or value < 0:
                        raise ValidationError(f'{where} 必须为非负有限数值')
                    if kind == 'count' and type(value) is not int:
                        raise ValidationError(f'{where} 数量必须为整数')
                    if kind == 'probability' and value > probability_max:
                        raise ValidationError(f'{where} 概率须在0至{probability_max}之间')
            identifier = row[IDENTITY[table]].strip()
            if identifier in seen:
                raise ValidationError(f'{table} 编号重复（忽略首尾空白）：{identifier}')
            seen.add(identifier)
    platforms = {row['platform'].strip() for row in document['table8']}
    resources = set()
    for table, key in [('table8', 'insight_artifact'), ('table9', 'manifestation_artifact')]:
        for row in document[table]:
            identifier = row[key].strip()
            if identifier in resources or identifier in platforms:
                raise ValidationError(f'资源编号重复或与平台冲突：{identifier}')
            resources.add(identifier)
            if row['platform'].strip() not in platforms:
                raise ValidationError(f'平台引用不存在：{row["platform"]}')


def prepare_case(document):
    """Keep original values untouched, return a derived view and cleaning log."""
    validate_case_document(document)
    result = deepcopy(document)
    changes, findings = [], []
    for table, fields in FIELDS.items():
        rows = [result[table]] if table == 'table11' else result[table]
        for i, row in enumerate(rows):
            prefix = table if table == 'table11' else f'{table}[{i}]'
            for key, kind in fields.items():
                value = row[key]
                if kind in ('id', 'text'):
                    cleaned = value.strip()
                elif kind == 'list':
                    cleaned = list(dict.fromkeys(v.strip() for v in value))
                else:
                    continue
                if value != cleaned:
                    changes.append({'path': f'{prefix}.{key}', 'original': value, 'normalized': cleaned,
                                    'policy': 'trim_text_and_deduplicate_list_v1'})
                    row[key] = cleaned
            for minimum, maximum in [('min_range_nm', 'max_range_nm'),
                                     ('min_target_altitude_ft', 'max_target_altitude_ft'),
                                     ('min_activation_altitude_ft', 'max_activation_altitude_ft')]:
                if minimum in row and row[minimum] > row[maximum]:
                    findings.append({'code': 'BOUND_ORDER', 'paths': [f'{prefix}.{minimum}', f'{prefix}.{maximum}'],
                                     'status': 'needs_review', 'action': 'preserve_original'})
            # Detect directional/header mismatch; never swap coordinate values.
            if row.get('longitude_column_raw', '').upper().startswith(('N', 'S')) or row.get('latitude_column_raw', '').upper().startswith(('E', 'W')):
                findings.append({'code': 'COORDINATE_DIRECTION', 'paths': [f'{prefix}.longitude_column_raw', f'{prefix}.latitude_column_raw'],
                                 'status': 'needs_review', 'action': 'preserve_original'})
    return result, {'changes': changes, 'findings': findings,
                    'policy': '原始输入不变；只规范化派生文本和列表；矛盾仅标记，不自动交换或补值。'}
