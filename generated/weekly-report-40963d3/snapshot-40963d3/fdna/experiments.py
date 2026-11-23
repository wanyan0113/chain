"""Section 4.2 experiment registration and lossless closure record checks."""
from copy import deepcopy
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from .model import ModelError, inspect_model
from .structure import digest, members, validate_structure


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ModelError('实验记录不得包含非有限数值')
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def inspect_experiments(model, structure, plan, batch):
    validate_structure(model, structure)
    schema = json.loads(Path(__file__).with_name('experiment_schema.json').read_text(encoding='utf-8'))
    for kind, value in (('plan', plan), ('batch', batch)):
        _finite(value)
        # Validate the selected contract, not the other branch of the union.
        selected = {'$schema': schema['$schema'], '$defs': schema['$defs'], '$ref': '#/$defs/' + kind}
        errors = list(Draft202012Validator(selected).iter_errors(value))
        if errors:
            raise ModelError('实验方案或闭合记录不符合数据契约：' + errors[0].message)
    if plan['case_sha256'] != digest(model) or plan['structure_sha256'] != digest(structure):
        raise ModelError('实验方案与案例或活动结构版本不一致')
    if batch['plan_sha256'] != digest(plan):
        raise ModelError('记录批次与实验方案版本不一致')
    variables = {v['id']: v for v in plan['variables']}
    if len(variables) != len(plan['variables']):
        raise ModelError('实验变量标识重复')
    sets = {'subject': {s['id'] for s in model['subjects']},
            'target': {t['id'] for t in model['targets']},
            'capability': set(inspect_model(model)['exogenous_nodes'])}
    refs = {f"{s['id']}:{loc}" for s in model['sources'] for loc in s['locators']}
    refs.update(s['source'] + ':' + s['id'] for s in structure['source_locators'])
    for variable in variables.values():
        if not set(variable['targets']) <= sets[variable['target_kind']]:
            raise ModelError('变量对象不存在；能力变量只可指向无入边节点')
        if not set(variable['source_refs']) <= refs:
            raise ModelError('实验变量引用未登记的来源位置')
    tasks = {t['id']: t for t in structure['tasks']}
    activities = {a['id']: a for a in structure['activities']}
    nodes = {n['id']: n for n in model['nodes']}
    ids = {'run': set(), 'closure': set(), 'event': set()}
    def unique(kind, identifier):
        if identifier in ids[kind]:
            raise ModelError(kind + '记录标识重复，不自动丢弃或合并')
        ids[kind].add(identifier)
    def interval(item):
        start, end = item['start'], item['end']
        if start is not None and end is not None and end < start:
            raise ModelError('记录结束刻度早于开始刻度')
        return start is None or end is None
    rows, runs = [], []
    for run in batch['runs']:
        unique('run', run['id'])
        assignments = run['assignments']
        if set(assignments) - set(variables):
            raise ModelError('记录引用未登记实验变量')
        run_reasons = []
        if not run['closures']:
            run_reasons.append('MISSING_CLOSURES')
        for identifier, variable in variables.items():
            assignment = assignments.get(identifier)
            if variable['unit'] is None:
                run_reasons.append('UNKNOWN_VARIABLE_UNIT:' + identifier)
            if assignment is None or assignment['value'] is None:
                run_reasons.append('MISSING_VARIABLE_VALUE:' + identifier)
            if assignment and variable['unit'] is not None and assignment['unit'] != variable['unit']:
                raise ModelError('实验变量单位与方案不一致')
        runs.append({'id': run['id'], 'blockers': run_reasons})
        attempts = set()
        for closure in run['closures']:
            unique('closure', closure['id'])
            task = tasks.get(closure['task'])
            if task is None or task['target'] != closure['target']:
                raise ModelError('闭合记录的任务或目标不对应')
            key = closure['task'], closure['target'], closure['attempt']
            if key in attempts:
                raise ModelError('同一推演、任务及尝试出现重复闭合记录')
            attempts.add(key)
            reasons = list(run_reasons)
            if interval(closure):
                reasons.append('MISSING_CLOSURE_TIME')
            if closure['status'] != 'closed':
                reasons.append('CLOSURE_NOT_CONFIRMED')
            covered = set()
            event_ids = []
            for event in closure['events']:
                unique('event', event['id'])
                event_ids.append(event['id'])
                activity = activities.get(event['activity'])
                if (activity is None or activity['task'] != task['id'] or
                        event['capability'] not in set(members(activity['capability_group']))):
                    raise ModelError('活动记录与闭合任务或能力映射不一致')
                if event['subject'] != nodes[event['capability']]['subject']:
                    raise ModelError('活动执行主体与能力所属主体不一致')
                if event['time_unit'] != batch['time_unit']:
                    raise ModelError('活动时间单位与批次不一致')
                if interval(event):
                    reasons.append('MISSING_EVENT_TIME:' + event['id'])
                for bound in ('start', 'end'):
                    time = event[bound]
                    if time is not None and ((closure['start'] is not None and time < closure['start']) or
                                             (closure['end'] is not None and time > closure['end'])):
                        raise ModelError('活动刻度超出闭合记录区间')
                if event['outcome'] != 'completed':
                    reasons.append('EVENT_NOT_COMPLETED:' + event['id'])
                names = [f['name'] for f in event['facts']]
                if len(set(names)) != len(names):
                    raise ModelError('同一活动记录的事实名称重复')
                if not names or any(f['value'] is None for f in event['facts']):
                    reasons.append('MISSING_EVENT_FACT:' + event['id'])
                covered.add((event['activity'], event['capability']))
            required = {(aid, cap) for aid in task['activity_order']
                        for cap in members(activities[aid]['capability_group'])}
            for aid, cap in sorted(required - covered):
                reasons.append('MISSING_ACTIVITY_CAPABILITY:' + aid + ':' + cap)
            rows.append({'run': run['id'], 'closure': closure['id'], 'task': task['id'],
                         'target': closure['target'], 'events': event_ids,
                         'status': 'needs_review' if reasons else 'structurally_complete', 'blockers': reasons})
    complete = sum(row['status'] == 'structurally_complete' for row in rows)
    return {'format_version': '1.0', 'case': model['id'],
            'status': ('no_records' if not batch['runs'] else 'needs_review'
                       if complete != len(rows) or any(r['blockers'] for r in runs) or not rows else 'validated'),
            'scope': '第4.2节实验变量与闭合记录契约核查；尚未提取表5指标或转换SE。',
            'data_kind': batch['data_kind'],
            'input_sha256': digest({'case': model, 'structure': structure, 'plan': plan, 'batch': batch}),
            'counts': {'variables': len(variables), 'runs': len(runs), 'closures': len(rows),
                       'events': len(ids['event']), 'structurally_complete_closures': complete,
                       'needs_review_closures': len(rows) - complete},
            'runs': runs, 'closures': rows, 'plan_snapshot': deepcopy(plan),
            'batch_snapshot': deepcopy(batch), 'structure_snapshot': deepcopy(structure),
            'source_snapshot': deepcopy(model['sources']), 'case_reproduction_ready': False,
            'policies': {'missing_values': 'retain_and_flag', 'duplicates': 'reject_without_merging',
                         'time_conversion': 'none', 'provenance': 'declared_only',
                         'activity_partition': 'interpretation', 'metric_extraction': 'not_implemented'},
            'limits': ['结构完整不代表推演真实、法术链实际闭合或已具备完整指标',
                       '不推定组内并行或先后，不凭活动时长代替表5指标',
                       '表6未给出的变量单位及取值保持未知，人工核验配置不作为原文配置']}
