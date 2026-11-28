"""Phase two: frozen cross-flow team metrics and resource-constrained selection."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from .contracts import require, read_json
from .joint_identity import sha, new_run_id
from .team_verify import verify_teams
from .team_evaluation import POLICY, source_binding, raw_metrics, freeze_and_score, scaffold
from .joint_network import optimize, network

VERSION = 'task-flow-team-optimization-1.0'


def execution_view(chain, raw, scored):
    view = deepcopy(chain)
    view.update(id=chain['candidate_id'], metrics=deepcopy(raw['metrics']), **deepcopy(scored))
    roles = {s['role'] for s in chain['plan']['steps']}
    view['plan']['members'] = {r: sorted({m for s in chain['plan']['steps'] if s['role'] == r for m in s['members']}) for r in roles}
    return view


def run_team_optimization(source, registry, *, weights=None, max_network_nodes=None):
    source, registry = deepcopy(source), deepcopy(registry)
    check = verify_teams(source)
    require(check['status'] == 'passed', 'source team feasibility verification failed: ' + check.get('reason', ''))
    scene = deepcopy(source['input_snapshot']['scene'])
    if weights is None:
        weights = deepcopy(scene['weights'])
    else:
        weights = deepcopy(weights)
    if max_network_nodes is not None:
        require(type(max_network_nodes) is int and 1 <= max_network_nodes <= 1000000, 'invalid scored-network node budget')
        scene['policies']['max_network_search_nodes'] = max_network_nodes
    result = dict(format_version=VERSION, mode='complementary_team_v1', run_id=new_run_id(),
                  status='blocked', policy=POLICY, domain_validity='pending', execution_confirmed=False,
                  optimality='not_run', optimality_scope='generated_candidate_set_only',
                  global_optimality='not_proven', selected_candidate_ids=[], network=None,
                  source_report=source, source_verification=check, registry=registry,
                  source_binding=source_binding(source), weights=weights, search_scene=scene,
                  registry_hash=sha(registry), source_candidate_search_complete=source['candidate_search_complete'],
                  source_network_search_complete=source['network_search_complete'],
                  supported_exports=['json', 'markdown'], blocked_reasons=[])
    raw, missing = raw_metrics(source, registry)
    if missing:
        result['blocked_reasons'] = missing
        return result
    if any(not any(c['task'] == j['task_id'] for c in source['candidates'].values()) for j in source['input_snapshot']['jobs']):
        result['blocked_reasons'] = [dict(reason='source_has_empty_task_candidate_pool')]
        return result
    evaluation = freeze_and_score(source, raw, weights)
    evaluation['registry_hash'] = sha(registry)
    evaluation['evaluation_context_hash'] = sha({k:v for k,v in evaluation.items() if k not in ('evaluation_context_hash', 'candidates')})
    result.update(raw_metrics=raw, evaluation=evaluation)
    groups = [[execution_view(source['candidates'][cid], raw[cid], evaluation['candidates'][cid]) for cid in pool['candidate_ids']]
              for pool in evaluation['pools']]
    best, stats = optimize(groups, scene, source['input_snapshot']['mapping'])
    result['network_search'] = stats
    result['optimality'] = ('candidate_set_optimum' if best is not None else 'candidate_set_infeasible') if stats['optimality'] in ('proven', 'proven_infeasible') else 'not_proven'
    if best is None:
        result['status'] = 'no_feasible_network' if stats['optimality'] == 'proven_infeasible' else 'undetermined'
    else:
        _, chosen, score, _ = best
        result.update(status='selected' if stats['optimality'] == 'proven' else 'feasible',
                      selected_candidate_ids=[c['id'] for c in chosen], network=network(chosen, score, scene))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-report', type=Path, required=True)
    parser.add_argument('--metrics', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--max-network-nodes', type=int)
    args = parser.parse_args(argv)
    require(not args.output_dir.exists(), 'output directory already exists')
    source = read_json(args.source_report, allow_finite_floats=True)
    if args.metrics is None:
        check = verify_teams(source)
        require(check['status'] == 'passed', 'source team report failed verification')
        registry = scaffold(source)
        args.output_dir.mkdir(parents=True)
        (args.output_dir/'metrics-to-fill.json').write_text(json.dumps(registry, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print('Generated empty metric/provenance scaffold; no scores or optimum produced.')
        return 0
    result = run_team_optimization(source, read_json(args.metrics, allow_finite_floats=True), max_network_nodes=args.max_network_nodes)
    from .team_optimization_verify import verify_team_optimization
    result['independent_verification'] = verify_team_optimization(result)
    require(result['independent_verification']['status'] == 'passed', 'independent scored-team verification failed')
    args.output_dir.mkdir(parents=True)
    (args.output_dir/'team_optimization.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (args.output_dir/'summary.md').write_text('\n'.join([
        '# Complementary-team optimization under explicit fixed intervals', '',
        f"Status: {result['status']}; optimality: {result['optimality']}",
        'Scope: generated candidate set only; domain validity remains pending.',
        f"Selected: {result['selected_candidate_ids']}", f"Network: {result['network']}",
        f"Blocked: {result['blocked_reasons']}", '']) , encoding='utf-8')
    print(json.dumps(dict(status=result['status'], optimality=result['optimality'], network=result['network'],
                          independent_verification=result['independent_verification']), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
