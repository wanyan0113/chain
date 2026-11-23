"""Reproducible values for extending the source templates (no XLSX authoring)."""
from copy import deepcopy
import json
from pathlib import Path

from design.excel import GUIDE, SETTINGS
from .importer import read_xlsx, workbook_spec
from .schema import INSTRUCTIONS
from .unified import extension_headers

ROOT = Path(__file__).resolve().parents[1]
EXTRA_GUIDE = [
    ['综合使用', '同一文件可在“Excel 来源与参数”导入查询，也可在“链网设计”上传生成；设计表空白时只能查询。'],
    ['原始与设计任务', '任务表保留原始资料的一条任务；设计任务表填写实际计划，可多条。不会自动推断目标编号或时间。'],
    ['避免重复抄写', '来源取值表按来源编号引用属性，导入时读取当前原值和声明单位。不要在属性表重复登记同一属性。'],
    ['取值列', '对象类型填任务、法器或情境；对象编号引用设计任务或法器编号。来源字段使用原表完整列名。探测能力来源编号为平台编号。'],
    ['数值与疑点', '保留案例说明声明的单位，不自动换算。引用字段的人工疑点、自动边界矛盾和未知单位会阻塞设计。'],
    ['指标和资源', '法器表集中填写耗时、精度、成本、容量、库存和冷却；设计设置填写评价权重。不能直接将原作用值当作成本。'],
    ['仍需填写', '来源取值仅生成属性；规则、协同关系、任务时序、指标和资源不会从原文或原始表自动补全。'],
    ['表10原文校验', '设计设置增加“表10原文校验”，值为文本true时强制执行原文筛选和协同条件。专用表10示例已开启；目标数量target_count、最大标记数max_tracks、最大解析数max_processing_targets均填数值、单位1。'],
]


def matrix(rows):
    width = len(rows[1])
    return [[row.get(col) for col in range(1, width + 1)] for _, row in sorted(rows.items())]


def spec():
    headers = extension_headers()
    empty = {name: [columns] for name, columns in headers.items()}
    empty['设计设置'] += [[key, None] for key in SETTINGS]
    empty['设计指南'] += EXTRA_GUIDE + GUIDE[1:]
    # A fully synthetic source section, independent of article values.
    doc = json.loads((ROOT / 'design/examples/canonical_network_demo.json').read_text(encoding='utf-8'))
    source = dict(checked_on='2026-11-02', scope='综合Excel工程演示：全部数据均为虚构，不是文章案例。',
                  source_files={key: 'technical-fixture' for key in ('table7','table8_table9','table11','case_text')},
                  units=dict(coordinates='十进制度', altitude='ft', distance='nmi', speed='kn',
                             attunement_probability='1', insight_precision='1', resonance_value='demo-value',
                             impedance_threshold='demo-value'), identity_mapping={}, explicit_facts=[], issues=[], missing_facts=[])
    source['table7'] = [dict(side='演示方', name=a['id'], id=a['id'], count=2) for a in doc['artifacts']]
    source['table7'].append(dict(side='演示方', name='演示平台', id='demo-platform', count=1))
    source['table8'] = [dict(platform='demo-platform', longitude_column_raw='E0', latitude_column_raw='N0',
                            altitude_ft=0, insight_artifact='demo-eye', target_types=['幽光法球'],
                            detection_range_nm=100, precision_raw=.99)]
    source['table9'] = [dict(platform='demo-platform', manifestation_artifact=a['id'], target_types=['幽光法球'],
                            max_range_nm=a['attributes']['max_range']['value'], min_range_nm=a['attributes']['min_range']['value'],
                            resonance_value=1, max_target_speed_kn=100, min_target_altitude_ft=0,
                            max_target_altitude_ft=1000, min_activation_altitude_ft=0,
                            max_activation_altitude_ft=1000, attunement_probability_percent=a['metrics']['accuracy'])
                       for a in doc['artifacts'] if a['role']=='manifestation']
    source['table11'] = dict(mission='demo-source-task', target_type='幽光法球', longitude_column_raw='E0',
                             latitude_column_raw='N0', target_altitude_ft=10, target_speed_kn=1, target_impedance_threshold=1)
    design = read_xlsx((ROOT / 'excel_entry/templates/链网设计示例.xlsx').read_bytes())
    demo = {('设计任务' if name=='任务' else '设计指南' if name=='填写指南' else name): matrix(rows)
            for name, rows in design.items()}
    demo['设计指南'] = deepcopy(empty['设计指南'])
    bindings = [['任务', task['id'], None, 'target_type', '任务', 'demo-source-task', '目标类型'] for task in doc['tasks']]
    bindings += [['法器', 'demo-eye', None, field, '探测能力', 'demo-platform', label]
                 for field, label in [('target_types','目标类型（用分号分隔）'), ('max_range','探测距离（n mile）')]]
    for artifact in ('demo-fast','demo-slow'):
        bindings += [['法器',artifact,None,field,'作用能力',artifact,label]
                     for field,label in [('target_types','目标类型（用分号分隔）'),
                                         ('max_range','最大作用距离（n mile）'), ('min_range','最小作用距离（n mile）')]]
    demo['来源取值'] = [headers['来源取值']] + bindings
    bound = {(row[0],row[1],row[2],row[3]) for row in bindings}
    demo['属性'] = [demo['属性'][0]] + [row for row in demo['属性'][1:] if tuple(row[:4]) not in bound]
    article = deepcopy(demo)
    article['设计设置'].append(['表10原文校验','true'])
    article['设计指南'] += [['原文字段', '任务：impedance、target_speed、target_altitude、target_count。探知：detection_range、max_tracks。协调：max_processing_targets。具现：resonance、max_target_speed、min_target_altitude、max_target_altitude、min_activation_altitude、max_activation_altitude。情境：target_distance、platform_altitude。']]
    for task in doc['tasks']:
        article['属性'] += [['任务',task['id'],None,'target_count','数值',1,'1']]
        article['来源取值'] += [['任务',task['id'],None,field,'任务','demo-source-task',label]
            for field,label in [('impedance','目标阈值（单位待确认）'),('target_speed','目标速度（kn）'),('target_altitude','目标高度（ft）')]]
    article['属性'] += [['法器','demo-eye',None,'max_tracks','数值',3,'1'],
                       ['法器','demo-coordinator',None,'max_processing_targets','数值',3,'1']]
    article['来源取值'].append(['法器','demo-eye',None,'detection_range','探测能力','demo-platform','探测距离（n mile）'])
    for identifier in ('demo-fast','demo-slow'):
        article['来源取值'] += [['法器',identifier,None,field,'作用能力',identifier,label]
            for field,label in [('resonance','作用值（单位待确认）'),('max_target_speed','最大目标速度（kn）'),
                ('min_target_altitude','最小目标高度（ft）'),('max_target_altitude','最大目标高度（ft）'),
                ('min_activation_altitude','最小释放高度（ft）'),('max_activation_altitude','最大释放高度（ft）')]]
        for task in doc['tasks']:
            article['属性'].append(['情境',task['id'],identifier,'platform_altitude','数值',100,'ft'])
    article_source = deepcopy(source)
    article_source['scope'] = '表10集成演示：全部参数为虚构工程配置，规则条件转录自文章，不是文章真实案例复现。'
    for row in article_source['table9']:
        row['resonance_value'] = 2
    article['设计设置'][1][1] = article_source['scope']
    return {'guide': INSTRUCTIONS + EXTRA_GUIDE,
            'templates': [dict(filename='案例示例.xlsx', extension=empty),
                          dict(filename='空白模板.xlsx', extension=empty),
                          dict(filename='案例设计演示.xlsx', source=workbook_spec(source), extension=demo),
                          dict(filename='表10设计演示.xlsx', source=workbook_spec(article_source), extension=article)]}


if __name__ == '__main__':
    print(json.dumps(spec(), ensure_ascii=True))
