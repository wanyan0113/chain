"""Editable presentation inputs executed by the unmodified historical modules."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import shutil

PACKAGE = Path(__file__).resolve().parent
SNAPSHOT = PACKAGE / 'snapshot-40963d3'
sys.path.insert(0, str(SNAPSHOT))
from fdna.model import load_model
from fdna.engine import evaluate
from fdna.extraction import extract_measurements
from fdna.structure import digest

def emit(message):
    print('@event ' + json.dumps({'message': message}, ensure_ascii=False), flush=True)

def save(folder, name, value):
    path = folder / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return path

def read(name):
    return json.loads((SNAPSHOT / 'data/fdna' / name).read_text(encoding='utf-8'))

def number(options, key, default, low=0, high=100):
    value = options.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{key}须在{low}至{high}之间')
    return value

def flag(options, key, default):
    value = options.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f'{key}须为开或关')
    return value

def sequence(options, folder):
    from design.engine import design
    import design.knowledge as knowledge
    document=json.loads((PACKAGE/'case_inputs/task_scene.json').read_text(encoding='utf-8'))
    config=json.loads((PACKAGE/'case_inputs/task_flows.json').read_text(encoding='utf-8'))
    task=document['tasks'][0]
    task['attributes']['confirmation_required']=flag(options,'confirmation_required',True)
    distance=number(options,'target_distance',5,.1,100)
    for context in document['contexts']:
        context['attributes']['target_distance']['value']=distance
    artifact_options=options.get('artifacts',{})
    if not isinstance(artifact_options,dict):raise ValueError('主体配置须为字段对象')
    for artifact in document['artifacts']:
        fields=artifact_options.get(artifact['id'],{})
        if not isinstance(fields,dict):raise ValueError('主体字段须为对象')
        artifact['available']=flag(fields,'available',artifact['available'])
        if artifact['id'] in ('demo-fast','demo-slow'):
            artifact['attributes']['max_range']['value']=number(fields,'max_range',10,.1,100)
    for link in document['links']:
        if link['from']=='demo-coordinator' and link['to']=='demo-fast':
            link['controls']=flag(options,'fast_link',True)
        if link['from']=='demo-coordinator' and link['to']=='demo-eye':
            link['communicates']=flag(options,'return_link',True)
    for name in ('demo-task-capabilities.ttl','demo-selection.rules.json'):
        shutil.copyfile(SNAPSHOT/'knowledge'/name,folder/name)
    save(folder,'sequences.json',config)
    save(folder, 'task_input.json', document)
    emit('读取任务：幽光法球；目标距离='+str(distance)+'；具现前复核='+str(task['attributes']['confirmation_required']))
    emit('读取四个主体和四条有向联系；调用工程本体推理、任务模板匹配及链生成')
    knowledge.KNOWLEDGE_ROOT=folder
    report = design(document)
    save(folder, 'sequence_full.json', report)
    names = {'observe': '探知', 'decide': '协调', 'act': '具现'}
    chosen = set(report.get('network', {}).get('chains', [])) if report.get('network') else set()
    tasks = []
    for task in report['tasks']:
        emit(f"{task['task']}：生成{len(task['chains'])}条可行候选链")
        chains = []
        for chain in task['chains']:
            plan = chain['plan']
            chains.append({'id': chain['id'], 'selected': chain['id'] in chosen,
                'steps': [{**s, 'label': names[s['activity']]} for s in plan['steps']],
                'metrics': chain['metrics'], 'connections': plan['connections'],
                'resource_usage': plan['resource_usage'], 'score': chain['score'],
                'normalized': chain.get('normalized', {})})
        tasks.append({'task': task['task'], 'chains': chains,
            'generation': task.get('sequence_generation'), 'blockers': task.get('blockers', []),
            'rejected': task.get('rejected_chains', []),'selection':task.get('selection',[])})
    steps=next(t['steps'] for t in config['templates'] if t['when'][0]['value']==document['tasks'][0]['attributes']['confirmation_required'])
    emit('链生成与资源检查已返回，保存完整计算报告')
    return {'case': 'sequence', 'status': report['status'], 'step_count': len(steps),
        'tasks': tasks, 'weights': document['weights'], 'network': report.get('network'),
        'input_steps': [s['activity'] for s in steps], 'task_input':document['tasks'][0],
        'contexts':document['contexts'],'flow_rules':config,'links':document['links'],'input_pool': [
            {'id': a['id'], 'available': a['available'], 'metrics': a['activity_metrics'],'attributes':a['attributes']} for a in document['artifacts']],
        'files': ['task_input.json', 'sequences.json', 'sequence_full.json', 'demo-task-capabilities.ttl', 'demo-selection.rules.json']}

def propagation(options, folder):
    values = options.get('s1_values', [0, 20, 50, 100])
    if not isinstance(values, list) or not 1 <= len(values) <= 21:
        raise ValueError('请输入1至21个S1取值')
    for value in values:
        number({'value': value}, 'value', 0)
    values = sorted(set(values))
    model = load_model(SNAPSHOT / 'data/fdna/arcane_case.json')
    save(folder, 'model.json', model)
    scenarios, reports, rows = [], [], []
    emit('读取11个节点与18条依赖边，使用基础式及表7参数')
    for value in values:
        scenario = read('intact.json')
        scenario['node_values']['S1'] = value
        for leaf in ('D2', 'S3', 'D4', 'S8', 'S10'):
            scenario['node_values'][leaf] = number(options, leaf, 100)
        scenarios.append(scenario)
        report = evaluate(model, scenario)
        reports.append(report)
        nodes = {n['id']: n for n in report['nodes']}
        rows.append({'S1': value, **{n: nodes[n]['value'] for n in ('S5', 'S6', 'F7', 'F9', 'F11')}})
        emit(f"S1={value}：依拓扑顺序重算，F7={nodes['F7']['value']:.5f}")
    save(folder, 'scenarios.json', scenarios)
    save(folder, 'propagation_full.json', reports)
    return {'case': 'propagation', 'rows': rows, 'reports': reports,
        'files': ['model.json', 'scenarios.json', 'propagation_full.json']}

def extraction(options, folder):
    model = load_model(SNAPSHOT / 'data/fdna/arcane_case.json')
    structure, plan, batch, config, calibration = [read(n) for n in (
        'arcane_structure.json', 'experiment_plan_check.json', 'indicator_records_check.json',
        'extraction_check.json', 'se_calibration_check.json')]
    values = {'S1': {'discovery_time': None if flag(options, 'missing_s1', False) else number(options, 's1_time', 25)},
        'D2': {'fusion_start': number(options, 'd2_start', 10), 'fusion_end': number(options, 'd2_end', 15)},
        'S3': {'discovery_count': number(options, 's3_count', 3, 0, 10)},
        'S5': {'tracking_start': number(options, 's5_start', 0), 'tracking_end': number(options, 's5_end', 80)}}
    if int(values['S3']['discovery_count']) != values['S3']['discovery_count']:
        raise ValueError('发现次数须为整数')
    for closure in batch['runs'][0]['closures']:
        for event in closure['events']:
            for fact in event['facts']:
                if fact['name'] in values.get(event['capability'], {}):
                    fact['value'] = values[event['capability']][fact['name']]
    calibration['rules']['S1']['upper'] = number(options, 's1_upper', 100, 1, 200)
    config['batch_sha256'] = digest(batch)
    for name, item in [('records.json', batch), ('extraction_rules.json', config), ('calibration.json', calibration),
                        ('experiment_plan.json', plan), ('structure.json', structure), ('model.json', model)]:
        save(folder, name, item)
    emit('按当前表单写入结构化记录与校准界限')
    emit('调用历史记录检查、指标提取与SE转换模块')
    result = extract_measurements(model, structure, plan, batch, config, calibration)
    save(folder, 'extraction_full.json', result)
    observations = result['measurements']['records'][0]['observations']
    normalized = {r['id']: r for r in result['normalization']['records'][0]['nodes']}
    traces = {r['node']: r for r in result['traces']}
    emit(f"提取结束：{result['counts']['known_indicators']}项已知，{result['counts']['unknown_indicators']}项未知")
    rows = [{'node': node, 'raw': raw, 'normalized': normalized[node], 'trace': traces[node],
        'calibration': calibration['rules'][node]} for node, raw in observations.items()]
    scenario={'id':'extracted_record_inputs','basis':'本次记录按人工校准规则提取的六个基础节点SE；其余节点由模型递推。',
        'node_values':{node:normalized[node]['se'] for node in ('S1','D2','S3','D4','S8','S10')}}
    prediction=evaluate(model,scenario)
    save(folder,'record_scenario.json',scenario)
    save(folder,'record_prediction.json',prediction)
    emit('将六个基础节点SE交给工程传播模块；中间和接受节点由模型计算，保留未知输入的阻断状态')
    return {'case': 'extraction', 'status': result['status'], 'counts': result['counts'], 'rows': rows,
        'scenario':scenario,'prediction':prediction,
        'facts': values, 'files': ['records.json', 'extraction_rules.json', 'calibration.json',
            'experiment_plan.json', 'structure.json', 'model.json', 'extraction_full.json','record_scenario.json','record_prediction.json']}

def network(options, folder):
    from design.engine import design
    import design.knowledge as knowledge
    document = json.loads((PACKAGE/'case_inputs/network_scene.json').read_text(encoding='utf-8'))
    tasks = options.get('tasks', {})
    artifacts = options.get('artifacts', {})
    if not isinstance(tasks, dict) or not isinstance(artifacts, dict):
        raise ValueError('任务和法器配置须为对象')
    enabled = []
    distances = {}
    for task in document['tasks']:
        fields = tasks.get(task['id'], {})
        if not isinstance(fields, dict): raise ValueError('任务字段须为对象')
        if not flag(fields, 'enabled', True): continue
        enabled.append(task)
        distances[task['id']] = number(fields, 'distance', task['demo_distance'], .1, 500)
        task.pop('demo_distance')
        task['start_s'] = number(fields, 'start', task['start_s'], 0, 1000)
        kind = fields.get('target_type', task['attributes']['target_type'])
        if kind not in ('幽光法球','飞行灵体'): raise ValueError('不支持的目标类型')
        task['attributes']['target_type'] = kind
        task['attributes']['target_speed']['value'] = number(fields, 'speed', 350, 0, 5000)
        task['attributes']['target_altitude']['value'] = number(fields, 'altitude', 36000, 0, 200000)
        task['attributes']['impedance']['value'] = number(fields, 'impedance', 10, 0, 200)
    if not enabled: raise ValueError('至少保留一项任务')
    document['tasks'] = enabled
    ids = {t['id'] for t in enabled}
    document['contexts'] = [c for c in document['contexts'] if c['task'] in ids]
    for context in document['contexts']:
        context['attributes']['target_distance']['value'] = distances[context['task']]
    for artifact in document['artifacts']:
        fields = artifacts.get(artifact['id'], {})
        if not isinstance(fields, dict): raise ValueError('法器字段须为对象')
        artifact['available'] = flag(fields, 'available', True)
        artifact['capacity'] = int(number(fields, 'capacity', artifact['capacity'], 1, 20))
        if artifact['capacity'] != fields.get('capacity', artifact['capacity']): raise ValueError('并行容量须为整数')
        for key in ('min_range','max_range'):
            if key in artifact['attributes']:
                artifact['attributes'][key]['value'] = number(fields, key, artifact['attributes'][key]['value'], 0, 500)
        attrs = artifact['attributes']
        if 'detection_range' in attrs:
            attrs['detection_range']['value'] = attrs['max_range']['value']
        if 'min_range' in attrs and attrs['min_range']['value'] >= attrs['max_range']['value']:
            raise ValueError(artifact['id']+'最小距离须小于最大距离')
        artifact['metrics']['duration_s'] = number(fields, 'duration', artifact['metrics']['duration_s'], .01, 1000)
        artifact['metrics']['accuracy'] = number(fields, 'accuracy', artifact['metrics']['accuracy'], 0, 1)
        artifact['metrics']['cost']['value'] = number(fields, 'cost', artifact['metrics']['cost']['value'], 0, 1000)
    weights = options.get('weights', document['weights'])
    if not isinstance(weights, dict): raise ValueError('权重须为对象')
    document['weights'] = {key:number(weights, key, document['weights'][key], 0, 1) for key in ('time','accuracy','cost')}
    for filename in ('network-capabilities.ttl','network-rules.json'):
        shutil.copyfile(PACKAGE/'case_inputs'/filename,folder/filename)
    knowledge.KNOWLEDGE_ROOT = folder
    save(folder,'network_input.json',document)
    emit('读取本次任务、法器属性、联系及评分权重')
    emit('调用工程本体推理与属性筛选，生成并评分每项任务的候选链')
    report = design(document)
    save(folder,'network_full.json',report)
    emit('按容量、一次性使用与协同规则搜索多任务组合')
    for task in report['tasks']:
        emit(task['task']+'：'+str(len(task['chains']))+'条候选链')
    emit('已得到可行网' if report.get('network') else '当前条件下没有完整可行网，保留检查依据')
    return {'case':'network','input':document,'report':report,
            'files':['network_input.json','network_full.json','network-capabilities.ttl','network-rules.json']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('case', choices=['sequence', 'propagation', 'extraction', 'network'])
    parser.add_argument('--request', required=True)
    parser.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    folder = Path(args.run_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    options = json.loads(Path(args.request).read_text(encoding='utf-8'))
    if not isinstance(options, dict):
        raise ValueError('输入须为字段对象')
    result = globals()[args.case](options, folder)
    result['files'] += ['request.json', 'provenance.json', 'result.json', 'process.log']
    save(folder, 'result.json', result)
    paths = ['design/sequences.py', 'design/engine.py', 'fdna/engine.py', 'fdna/extraction.py', 'fdna/self_effectiveness.py']
    save(folder, 'provenance.json', {'cutoff': json.loads((PACKAGE/'version.json').read_text())['cutoff'],
        'started_utc': datetime.now(timezone.utc).isoformat(), 'case': args.case,
        'modules': [{'path': p, 'sha256': hashlib.sha256((SNAPSHOT/p).read_bytes()).hexdigest()} for p in paths]})
    emit('已保存本次输入、计算依据与结果，可下载核对')

if __name__ == '__main__':
    main()
