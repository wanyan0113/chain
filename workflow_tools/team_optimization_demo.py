"""Synthetic explicit-quality phase-two acceptance; not empirical accuracy."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from .team_demo import inputs
from task_flow.team_feasibility import run_teams
from task_flow.team_evaluation import scaffold
from task_flow.team_optimization import run_team_optimization
from task_flow.team_optimization_verify import verify_team_optimization


def synthetic_registry(source):
    registry = scaffold(source)
    registry.update(quality_assumption='Synthetic stage-level success probabilities; serial multiplication is a declared unvalidated model assumption.',
                    source_refs=['synthetic-team-evaluation-experiment-v1'])
    for record in registry['records']:
        multi = len(record['members']) > 1
        for member, metric in record['member_metrics'].items():
            metric.update(duration_s=9 if multi else 10,
                          cost=dict(value=8 if member == 'O-Combined' else 2, unit=registry['cost_unit']),
                          source_refs=['synthetic-team-member-cost-and-time-v1'])
        record['fusion'].update(duration_s=1 if multi else 0, cost=dict(value=1 if multi else 0, unit=registry['cost_unit']),
                                source_refs=['synthetic-team-fusion-cost-and-time-v1'])
        members = set(record['members'])
        if members == {'O-Phase-Tuner', 'O-Safety-Auditor'}:
            quality = .995
        elif members == {'O-Combined'}:
            quality = .96
        elif members == {'O-Combined', 'O-Phase-Tuner'}:
            quality = .97
        elif members == {'O-Combined', 'O-Safety-Auditor'}:
            quality = .975
        else:
            quality = .99
        record['quality'].update(value=quality, origin='synthetic_assumption', source_refs=['synthetic-explicit-stage-quality-v1'])
    return registry


def run(output, source_path=None):
    if output.exists():
        raise ValueError('output directory already exists')
    source = json.loads(source_path.read_text(encoding='utf-8')) if source_path else run_teams(*inputs())
    registry = synthetic_registry(source)
    result = run_team_optimization(source, registry)
    verification = verify_team_optimization(result)
    if verification['status'] != 'passed' or not verification.get('optimality_verified'):
        raise AssertionError(verification)
    result['independent_verification'] = verification
    # Keep a meaningful budget regression: a budgeted result may happen to equal
    # the reference optimum, but cannot inherit the reference's proof status.
    budgeted = run_team_optimization(source, registry, max_network_nodes=2)
    budget_check = verify_team_optimization(budgeted)
    if budget_check['status'] != 'passed' or budgeted['optimality'] != 'not_proven':
        raise AssertionError(budget_check)
    output.mkdir(parents=True)
    for name, content in [('source-team-report', source), ('synthetic-metrics', registry),
                          ('team-optimization', result), ('budget-2-result', budgeted), ('budget-2-verification', budget_check)]:
        (output/(name+'.json')).write_text(json.dumps(content, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    summary = dict(status='passed', source_candidates=len(source['candidates']), evaluation_policy=result['policy'],
                   selected_candidate_ids=result['selected_candidate_ids'], network=result['network'],
                   optimality=result['optimality'], global_optimality=result['global_optimality'],
                   independent_verification=verification, budget_2_status=budgeted['status'],
                   budget_2_optimality=budgeted['optimality'], budget_2_verification=budget_check,
                   metrics_origin='synthetic_assumption', domain_validity='pending')
    (output/'verification.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--source-report', type=Path)
    args = parser.parse_args()
    run(args.output_dir, args.source_report)


if __name__ == '__main__':
    main()
