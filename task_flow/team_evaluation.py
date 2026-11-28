"""Explicit stage-level team metrics under fixed externally supplied intervals.

Member accuracies are never combined. A group quality value and its provenance
must be supplied for each execution plan, including singleton plans.
"""
from copy import deepcopy
import math
import json
from pathlib import Path
from jsonschema import Draft202012Validator

from .contracts import require
from .joint_identity import sha, canonical
from .team_contracts import VERSION

POLICY = 'team-fixed-interval-explicit-quality-1.0'


def plan_key(chain, stage):
    return 'team-stage-' + sha(dict(task_id=chain['task'], flow_id=chain['flow_id'],
        step_instance_id=stage['step_instance_id'], activity=stage['activity'], role=stage['role'],
        members=sorted(stage['members']), assignments=stage['capability_assignments'], hub=stage['hub']))


def source_binding(report):
    return dict(source_input_hash=report['input_hash'], source_context_hash=sha(report['context']),
                candidate_set_hash=sha(sorted(report['candidates'])))


def scaffold(report):
    require(report.get('format_version') == VERSION, 'expected phase-one team report')
    records = {}
    for chain in report['candidates'].values():
        for stage in chain['plan']['steps']:
            key = plan_key(chain, stage)
            records[key] = dict(plan_key=key, task_id=chain['task'], flow_id=chain['flow_id'],
                step_instance_id=stage['step_instance_id'], members=sorted(stage['members']),
                capability_assignments=deepcopy(stage['capability_assignments']), hub=stage['hub'],
                member_metrics={m: dict(duration_s=None, cost=None, source_refs=[]) for m in sorted(stage['members'])},
                fusion=dict(duration_s=None, cost=None, source_refs=[]),
                quality=dict(value=None, origin='UNASSIGNED', source_refs=[]))
    return dict(format_version='task-flow-team-evaluation-1.0', policy=POLICY, policy_version='1.0',
                binding=source_binding(report), duration_policy='max_member_plus_explicit_fusion_v1',
                reservation_policy='full_explicit_stage_interval_plus_member_cooldown_v1',
                chain_quality_policy='product_of_explicit_stage_quality_v1',
                quality_assumption='UNASSIGNED', cost_unit=report['input_snapshot']['scene']['policies']['cost_unit'],
                source_refs=[], records=sorted(records.values(), key=lambda r:r['plan_key']))


def _refs(values):
    return (type(values) is list and bool(values) and all(type(v) is str and v.strip() for v in values)
            and len(set(values)) == len(values))


def _number(v, low=0, high=None):
    return type(v) in (int, float) and math.isfinite(v) and v >= low and (high is None or v <= high)


def validate_registry(report, registry):
    schema = json.loads(Path(__file__).with_name('team_evaluation.schema.json').read_text(encoding='utf-8'))
    errors = list(Draft202012Validator(schema).iter_errors(registry))
    require(not errors, 'invalid explicit team metrics: ' + '; '.join(e.message for e in errors[:3]))
    expected = scaffold(report)
    fields = set(expected)
    require(type(registry) is dict and set(registry) == fields, 'invalid team evaluation fields')
    for key in ('format_version', 'policy', 'policy_version', 'binding', 'duration_policy', 'reservation_policy',
                'chain_quality_policy', 'cost_unit'):
        require(registry[key] == expected[key], 'team evaluation binding/policy mismatch: ' + key)
    require(type(registry['records']) is list, 'expected explicit stage metric records')
    records = registry['records']
    require(all(type(r) is dict and isinstance(r.get('plan_key'), str) for r in records), 'invalid metric record')
    ids = [r['plan_key'] for r in records]
    require(len(set(ids)) == len(ids), 'duplicate stage metric record')
    actual = {r['plan_key']: r for r in records}
    target = {r['plan_key']: r for r in expected['records']}
    require(set(actual) <= set(target), 'foreign stage metric record')
    missing = [dict(plan_key=k, reason='missing_record') for k in sorted(set(target) - set(actual))]
    for key, record in actual.items():
        template = target[key]
        require(set(record) == set(template), 'invalid stage metric fields')
        for name in ('task_id', 'flow_id', 'step_instance_id', 'members', 'capability_assignments', 'hub'):
            require(record[name] == template[name], 'metric plan binding changed: ' + name)
        require(type(record['member_metrics']) is dict and set(record['member_metrics']) == set(record['members']),
                'each actual member needs its own metric record')
        pending = False
        for metric in [*record['member_metrics'].values(), record['fusion']]:
            require(type(metric) is dict and set(metric) == {'duration_s', 'cost', 'source_refs'}, 'invalid member/fusion fields')
            if metric['duration_s'] is None or metric['cost'] is None or not metric['source_refs']:
                pending = True
                continue
            require(_number(metric['duration_s']) and _refs(metric['source_refs']), 'invalid duration or metric sources')
            cost = metric['cost']
            require(type(cost) is dict and set(cost) == {'value', 'unit'} and _number(cost['value'])
                    and cost['unit'] == registry['cost_unit'], 'invalid or inconsistent team cost unit')
        quality = record['quality']
        require(all(m['duration_s'] is None or not _number(m['duration_s']) or m['duration_s'] > 0
                    for m in record['member_metrics'].values()), 'actual member duration must be positive')
        require(type(quality) is dict and set(quality) == {'value', 'origin', 'source_refs'}, 'invalid quality fields')
        if quality['value'] is None or quality['origin'] == 'UNASSIGNED' or not quality['source_refs']:
            pending = True
        else:
            require(_number(quality['value'], high=1) and quality['origin'] in ('synthetic_assumption', 'expert_assumption', 'measured')
                    and _refs(quality['source_refs']), 'invalid explicit stage quality')
        if pending:
            missing.append(dict(plan_key=key, reason='unassigned_metric_or_provenance'))
    if registry['quality_assumption'] in ('', 'UNASSIGNED') or not registry['source_refs']:
        missing.append(dict(plan_key=None, reason='policy_assumptions_or_sources_unassigned'))
    else:
        require(type(registry['quality_assumption']) is str and _refs(registry['source_refs']), 'invalid policy provenance')
    return actual, missing


def raw_metrics(report, registry):
    """All candidates must be evaluable; never silently drop unscored teams."""
    records, missing = validate_registry(report, registry)
    if missing:
        return {}, missing
    result = {}
    config = report['input_snapshot']['config']
    for cid, chain in report['candidates'].items():
        schedule = config['schedules'].get(chain['task'])
        if schedule is None:
            missing.append(dict(candidate_id=cid, reason='explicit_time_intervals_required'))
            continue
        stages = []
        for stage, interval in zip(chain['plan']['steps'], schedule):
            record = records[plan_key(chain, stage)]
            duration = max(m['duration_s'] for m in record['member_metrics'].values()) + record['fusion']['duration_s']
            require(duration > 0 and math.isfinite(duration), 'team stage duration must be positive and finite')
            # This first scored policy validates a fixed schedule, it does not
            # silently resize intervals or insert waits when a team changes.
            require(duration == interval['end_s'] - interval['start_s'],
                    'team duration differs from explicit stage interval; no automatic scheduling')
            cost = sum(m['cost']['value'] for m in record['member_metrics'].values()) + record['fusion']['cost']['value']
            require(math.isfinite(cost), 'team cost overflow')
            stages.append(dict(plan_key=record['plan_key'], time=duration, accuracy=record['quality']['value'],
                               cost=cost, cost_unit=registry['cost_unit']))
        metrics = dict(time=sum(s['time'] for s in stages), accuracy=math.prod(s['accuracy'] for s in stages),
                       cost=sum(s['cost'] for s in stages), cost_unit=registry['cost_unit'])
        require(all(math.isfinite(metrics[k]) for k in ('time', 'accuracy', 'cost')), 'chain metrics overflow')
        result[cid] = dict(metrics=metrics, stages=stages)
    return result, missing


def freeze_and_score(report, raw, weights):
    require(set(weights) == {'time', 'accuracy', 'cost'} and all(_number(v, high=1) for v in weights.values()) and
            math.isclose(sum(weights.values()), 1, abs_tol=1e-9, rel_tol=0), 'invalid team weights')
    require(set(raw) == set(report['candidates']), 'cannot freeze a partial metric pool')
    pools, values = [], {}
    for task in sorted(j['task_id'] for j in report['input_snapshot']['jobs']):
        ids = sorted(cid for cid, c in report['candidates'].items() if c['task'] == task)
        require(bool(ids), 'cannot score an empty task pool')
        bounds = {k: dict(min=min(raw[c]['metrics'][k] for c in ids), max=max(raw[c]['metrics'][k] for c in ids))
                  for k in ('time', 'accuracy', 'cost')}
        for cid in ids:
            normalized = {}
            for key, bound in bounds.items():
                low, high = bound['min'], bound['max']
                score = .5 if low == high else (raw[cid]['metrics'][key] - low) / (high - low)
                normalized[key] = score if low == high or key == 'accuracy' else 1-score
            values[cid] = dict(normalized=normalized, score=sum(weights[k]*normalized[k] for k in weights))
        pools.append(dict(task_id=task, candidate_ids=ids, extrema=bounds))
    frozen = dict(policy=POLICY, normalization='per_task_cross_flow_frozen_minmax_v1', equal_metric_score=.5,
                  objective='mean_of_per_task_normalized_chain_scores', weights=deepcopy(weights), pools=pools,
                  metrics_hash=sha(raw), source=source_binding(report))
    return dict(**frozen, evaluation_context_hash=sha(frozen), candidates=values)
