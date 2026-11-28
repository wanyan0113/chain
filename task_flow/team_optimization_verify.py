"""Independent metric arithmetic, score and bounded exhaustive network reference."""
from collections import Counter, defaultdict
from itertools import product, combinations
import math

from .contracts import require
from .joint_identity import sha
from .team_verify import verify_teams
from .team_evaluation import POLICY, plan_key, source_binding, validate_registry
from .team_quality_semantics import interpretation


def _feasible(ids, source):
    scene = source['input_snapshot']['scene']
    artifacts = {a['id']: a for a in scene['artifacts']}
    events, inventory = defaultdict(list), Counter()
    chosen = {source['candidates'][cid]['task']: source['candidates'][cid] for cid in ids}
    for cid in ids:
        chain = source['candidates'][cid]
        for stage, interval in zip(chain['plan']['steps'], source['input_snapshot']['config']['schedules'][chain['task']]):
            for member in stage['members']:
                events[member] += [(interval['start_s'], 1), (interval['end_s'] + artifacts[member]['cooldown_s'], -1)]
                inventory[member] += int(artifacts[member]['consumable'])
    for member, count in inventory.items():
        if count > artifacts[member]['inventory']:
            return False
    checks = [(events[m], a['capacity']) for m, a in artifacts.items()]
    checks += [([event for m in g['artifacts'] for event in events[m]], g['capacity']) for g in scene.get('resource_groups', [])]
    for sequence, capacity in checks:
        active = 0
        for _, delta in sorted(sequence):
            active += delta
            if active > capacity:
                return False
    return all(chosen[d['after']]['start_s'] >= chosen[d['before']]['end_s'] + d['gap_s'] for d in scene.get('dependencies', []))


def verify_team_optimization(report, *, max_reference_combinations=100000):
    try:
        return _verify(report, max_reference_combinations)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        return dict(status='failed', reason=str(exc))


def _verify(report, reference_limit):
    require(type(reference_limit) is int and reference_limit > 0, 'invalid independent reference budget')
    require(report['format_version'] in ('task-flow-team-optimization-1.0', 'task-flow-team-optimization-1.1') and report['policy'] == POLICY,
            'unsupported scored-team report')
    require(report['mode'] == 'complementary_team_v1' and report['global_optimality'] == 'not_proven' and
            report['domain_validity'] == 'pending' and report['execution_confirmed'] is False, 'unsupported proof/domain claims')
    source, registry = report['source_report'], report['registry']
    check = verify_teams(source)
    require(check['status'] == 'passed' and report['source_verification'] == check, 'source verification failed or changed')
    require(report['source_binding'] == source_binding(source) and report['registry_hash'] == sha(registry), 'source/metric binding changed')
    require(report['source_candidate_search_complete'] == source['candidate_search_complete'] and
            report['source_network_search_complete'] == source['network_search_complete'], 'source coverage claims changed')
    from copy import deepcopy
    scene = deepcopy(source['input_snapshot']['scene'])
    actual_scene = report['search_scene']
    limit = actual_scene['policies'].get('max_network_search_nodes', 1000000)
    require(type(limit) is int and 1 <= limit <= 1000000, 'invalid search budget')
    if 'max_network_search_nodes' in actual_scene['policies']:
        scene['policies']['max_network_search_nodes'] = limit
    require(actual_scene == scene, 'search changed source resource constraints')
    legacy_report = report['format_version'] == 'task-flow-team-optimization-1.0'
    require(not legacy_report or (registry['format_version'] == 'task-flow-team-evaluation-1.0' and
                                 'quality_interpretation' not in report), 'cannot downgrade a new quality contract to legacy replay')
    if not legacy_report:
        require(report['quality_interpretation'] == interpretation(source, registry), 'quality meaning/probability/length statement changed')
    records, missing = validate_registry(source, registry, legacy_replay=legacy_report)
    missing_timing = any(c['task'] not in source['input_snapshot']['config']['schedules'] for c in source['candidates'].values())
    task_ids = sorted(j['task_id'] for j in source['input_snapshot']['jobs'])
    empty_task = any(not any(c['task'] == task for c in source['candidates'].values()) for task in task_ids)
    if missing or missing_timing or empty_task:
        require(report['status'] == 'blocked' and report['optimality'] == 'not_run' and
                not report['selected_candidate_ids'] and report['network'] is None and
                report['blocked_reasons'] and 'evaluation' not in report, 'missing metrics were silently scored')
        return dict(status='passed', scope='blocked_without_fabricated_metrics', optimality_verified=False)
    require(not report['blocked_reasons'], 'unexpected blocked reasons')
    raw = {}
    for cid, chain in source['candidates'].items():
        stages = []
        schedule = source['input_snapshot']['config']['schedules'][chain['task']]
        for stage, interval in zip(chain['plan']['steps'], schedule):
            r = records[plan_key(chain, stage)]
            durations = [m['duration_s'] for m in r['member_metrics'].values()]
            duration = max(durations) + r['fusion']['duration_s']
            require(duration > 0 and duration == interval['end_s'] - interval['start_s'], 'stage timing mismatch')
            cost = sum(m['cost']['value'] for m in r['member_metrics'].values()) + r['fusion']['cost']['value']
            stages.append(dict(plan_key=r['plan_key'], time=duration, accuracy=r['quality']['value'],
                               cost=cost, cost_unit=registry['cost_unit']))
        accuracy = 1
        for stage in stages:
            accuracy *= stage['accuracy']
        raw[cid] = dict(stages=stages, metrics=dict(time=sum(s['time'] for s in stages), accuracy=accuracy,
                       cost=sum(s['cost'] for s in stages), cost_unit=registry['cost_unit']))
    require(report['raw_metrics'] == raw, 'raw stage/chain metrics changed')
    evaluation, weights = report['evaluation'], report['weights']
    require(set(weights) == {'time', 'accuracy', 'cost'} and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in weights.values())
            and math.isclose(sum(weights.values()), 1, rel_tol=0, abs_tol=1e-9), 'invalid weights')
    pools, expected_scores = [], {}
    for task in task_ids:
        ids = sorted(cid for cid, c in source['candidates'].items() if c['task'] == task)
        extrema = {k: dict(min=min(raw[c]['metrics'][k] for c in ids), max=max(raw[c]['metrics'][k] for c in ids)) for k in weights}
        pools.append(dict(task_id=task, candidate_ids=ids, extrema=extrema))
        for cid in ids:
            n = {}
            for key in weights:
                low, high = extrema[key]['min'], extrema[key]['max']
                n[key] = .5 if low == high else ((raw[cid]['metrics'][key] - low) / (high-low) if key == 'accuracy'
                                               else 1-(raw[cid]['metrics'][key]-low)/(high-low))
            expected_scores[cid] = dict(normalized=n, score=sum(weights[k]*n[k] for k in weights))
    expected_context = dict(policy=POLICY, normalization='per_task_cross_flow_frozen_minmax_v1', equal_metric_score=.5,
                            objective='mean_of_per_task_normalized_chain_scores', weights=weights, pools=pools,
                            metrics_hash=sha(raw), source=source_binding(source), registry_hash=sha(registry))
    require(evaluation == dict(**expected_context, evaluation_context_hash=sha(expected_context), candidates=expected_scores),
            'frozen evaluation context/scores changed')
    stats = report['network_search']
    proven = stats['optimality'] in ('proven', 'proven_infeasible')
    require(not proven or (stats['search_complete'] and stats['termination_reason'] == 'search_exhausted' and stats['nodes'] <= limit),
            'exhausted budget incorrectly upgraded to a proof')
    selected = report['selected_candidate_ids']
    require(report['optimality'] == (('candidate_set_optimum' if selected else 'candidate_set_infeasible') if proven else 'not_proven'),
            'optimizer proof state changed')
    score = None
    if selected:
        require(len(selected) == len(task_ids) and [source['candidates'][c]['task'] for c in selected] == task_ids and
                _feasible(selected, source), 'selected network not resource feasible')
        score = sum(expected_scores[c]['score'] for c in selected) / len(selected)
        network = report['network']
        require(network['chains'] == selected and network['score'] == score and
                network['cost_per_execution'] == sum(raw[c]['metrics']['cost'] for c in selected) and
                network['cost_unit'] == registry['cost_unit'] and network['objective'] == 'mean_of_per_task_normalized_chain_scores',
                'network objective/total changed')
        require(network['makespan_s'] == max(source['candidates'][c]['end_s'] for c in selected) -
                min(source['candidates'][c]['start_s'] for c in selected), 'network makespan changed')
        relations = []
        chains = [source['candidates'][c] for c in selected]
        dependencies = {(d['before'], d['after']) for d in scene.get('dependencies', [])}
        for left, right in combinations(chains, 2):
            a, b = left['candidate_id'], right['candidate_id']
            if (left['task'], right['task']) in dependencies or (right['task'], left['task']) in dependencies:
                first, last = (a, b) if (left['task'], right['task']) in dependencies else (b, a)
                relations.append(dict(kind='serial', **{'from': first, 'to': last}))
            elif max(left['start_s'], right['start_s']) < min(left['end_s'], right['end_s']):
                relations.append(dict(kind='parallel', chains=[a, b]))
            lm = {m for s in left['plan']['steps'] for m in s['members']}
            rm = {m for s in right['plan']['steps'] for m in s['members']}
            if lm & rm:
                relations.append(dict(kind='resource_sharing', chains=[a, b], artifacts=sorted(lm & rm)))
        by_task = {c['task']: c['candidate_id'] for c in chains}
        for task in by_task:
            successors = sorted(b for a, b in dependencies if a == task)
            if len(successors) > 1:
                relations.append(dict(kind='branch', **{'from': by_task[task], 'to': [by_task[t] for t in successors]}))
        require(network['id'] == 'joint-web-1' and network['relations'] == relations, 'network relations changed')
        require(report['status'] == ('selected' if proven else 'feasible'), 'selection state changed')
    else:
        require(report['network'] is None and report['status'] == ('no_feasible_network' if proven else 'undetermined'), 'empty-result state changed')
    reference_best, visited, feasible_count, complete = None, 0, 0, True
    for ids in product(*(p['candidate_ids'] for p in pools)):
        if visited >= reference_limit:
            complete = False
            break
        visited += 1
        if not _feasible(ids, source):
            continue
        feasible_count += 1
        candidate_score = sum(expected_scores[c]['score'] for c in ids) / len(ids)
        key = (-candidate_score, tuple(ids))
        if reference_best is None or key < reference_best:
            reference_best = key
    if complete and proven:
        require((not selected and reference_best is None) or
                (selected and reference_best == (-score, tuple(selected))), 'optimizer disagrees with independent exhaustive reference')
    return dict(status='passed', scope='metrics_scores_and_resource_feasibility_with_bounded_independent_reference',
                reference_complete=complete, reference_combinations=visited, reference_feasible=feasible_count,
                reference_best_score=-reference_best[0] if reference_best else None,
                selected_matches_reference=bool(selected) and reference_best == (-score, tuple(selected)),
                optimality_verified=complete and proven, production_optimality=report['optimality'])
