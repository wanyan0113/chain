"""Isolated task_flow -> real OWL -> complementary plans/chains/feasible sets.

No modification of the singleton entry points, no objective, no optimality
claim, no physical execution and no implicit team-quality model.
"""
import argparse
from collections import Counter
from copy import deepcopy
from itertools import combinations, product
import json
from pathlib import Path

from .contracts import prepare_problem, require, read_json
from .planner import SearchSession
from .design_bridge import compile_tuple
from .joint_identity import design_context, flow_identity, sha, canonical, new_run_id, require_nfc_source, task_context
from .team_contracts import (VERSION, PLAN_MODE, IDENTITY, prepare, internal_requests,
                             edge_requests, identity, reservations)
from design.engine import validate_document, infer_task_capabilities, evaluate
from design.knowledge import knowledge_context, knowledge_outputs
from design.capability_identity import load_capability_ontology
from design.owl_scene import reason_scene, task_requirements
from design.ontology_inference import prepare_artifacts
from design.sequences import step_requirements, connection
from design.plans import resource_conflicts


def contributions(task, step, caps, doc, inference, owl):
    """Per-capability evidence; no all-capabilities singleton prefilter."""
    type_evidence = {e['artifact']: e for e in inference['artifacts']}
    contexts = {(c['task'], c['artifact']): c for c in doc.get('contexts', [])}
    tf = {**task['attributes'], 'ontology_types': task_requirements(task, owl)['types']}
    rows = []
    for a in sorted(doc['artifacts'], key=lambda a: a['id']):
        if a['role'] != step['role']:
            continue
        context = contexts.get((task['id'], a['id']), {})
        facts = {**a['attributes'], 'ontology_types': type_evidence[a['id']]['inferred_types'],
                 'ontology_capabilities': inference['relations']['capabilities'][a['id']]}
        checks = [evaluate(r, dict(task=tf, artifact=facts, context=context.get('attributes', {})))
                  for r in doc['artifact_rules'] if r['role'] == step['role'] and r['capability'] in caps]
        level = a['attributes'].get('level_' + step['activity'])
        ls = ('unknown' if not isinstance(level, dict) or level.get('unit') != '1' or
              type(level.get('value')) not in (int, float) or not 0 <= level['value'] <= 1 else
              'pass' if level['value'] >= step['min_level'] else 'fail')
        ts = ('pass' if a['role'] == 'coordination' else
              'unknown' if not isinstance(task['attributes'].get('target_type'), str) or
              not isinstance(a['attributes'].get('target_types'), list) else
              'pass' if task['attributes']['target_type'] in a['attributes']['target_types'] else 'fail')
        issues = a['blocking_issue_ids'] + context.get('blocking_issue_ids', [])
        issues += ['owl-mapping:' + p['mapping'] for p in owl.get('data_projection', [])
                   if p['kind'] == 'artifact' and p['owner'] == a['id'] and p['required'] and p['status'] == 'unknown']
        common = ('fail' if not a['available'] or 'fail' in (ls, ts) else
                  'unknown' if issues or 'unknown' in (ls, ts) else 'pass')
        cap_states = {}
        for cap in caps:
            relevant = [c for c in checks if c['capability'] == cap]
            rule_state = ('pass' if any(c['status'] == 'pass' for c in relevant) else
                          'unknown' if not relevant or any(c['status'] == 'unknown' for c in relevant) else 'fail')
            cap_states[cap] = ('fail' if 'fail' in (common, rule_state) else
                               'unknown' if 'unknown' in (common, rule_state) else 'pass')
        rows.append(dict(artifact=a['id'], common_status=common, capabilities=cap_states,
                         level_check=ls, target_type_check=ts, rules=checks,
                         blocking_issue_ids=issues, ontology_evidence=type_evidence[a['id']]))
    return rows


class Budget:
    def __init__(self, limits):
        self.limits, self.used, self.hit = limits, Counter(), set()

    def take(self, name):
        if self.used[name] >= self.limits[name]:
            self.hit.add(name)
            return False
        self.used[name] += 1
        return True


def checked_edges(requests, links, model):
    evidence = []
    for left, right, relation in requests:
        status, reason, edge = connection(left, right, relation, links, model)
        evidence.append(dict(from_member=left, to_member=right, relation=relation,
                             status=status, reason=reason, evidence=edge))
    return evidence


def stage_plans(step, caps, rows, links, model, config, budget):
    domains = [[r['artifact'] for r in rows if r['capabilities'][cap] == 'pass'] for cap in caps]
    plans, rejects = [], []
    if not caps:
        return plans, [dict(status='unknown', reason='EMPTY_CAPABILITY_REQUIREMENT')]
    for providers in product(*domains):
        if not budget.take('team_nodes'):
            break
        members = sorted(set(providers))
        if len(members) > config['max_members']:
            continue
        for hub in members:
            plan = dict(step_instance_id=step['id'], activity=step['activity'], role=step['role'],
                        required_capabilities=caps, min_level=step['min_level'], members=members,
                        capability_assignments=dict(zip(caps, providers)), hub=hub)
            edges = checked_edges(internal_requests(plan), links, model)
            if any(e['status'] != 'pass' for e in edges):
                rejects.append(dict(members=members, hub=hub, connections=edges))
                continue
            if len(plans) >= config['budgets']['plans_per_stage']:
                budget.hit.add('plans_per_stage')
                return plans, rejects
            plan['internal_connections'] = edges
            plans.append(plan)
    return sorted(plans, key=canonical), rejects


def generate_chains(task, flow_id, template, domains, doc, config, context, budget, remaining):
    artifacts = {a['id']: a for a in doc['artifacts']}
    links = {(e['from'], e['to']): e for e in doc['links']}
    model = context['communication_model']
    schedule = config['schedules'].get(task['id'])
    if schedule is not None:
        require(len(schedule) == len(domains) and schedule[0]['start_s'] == task['start_s'],
                'explicit schedule must match every generated flow length and task start')
    chains, rejections = [], Counter()

    def walk(steps, edges):
        if len(steps) == len(domains):
            inventory = Counter(m for s in steps for m in s['members'] if artifacts[m]['consumable'])
            if any(n > artifacts[m]['inventory'] for m, n in inventory.items()):
                rejections['INVENTORY'] += 1
                return
            usage = reservations(steps, artifacts, schedule)
            chain = dict(task=task['id'], flow_id=flow_id, plan=dict(mode=PLAN_MODE, steps=deepcopy(steps),
                         connections=edges, resource_usage=usage), metrics=None, score=None,
                         resource_feasibility='passed' if schedule else 'timing_not_provided')
            if schedule:
                chain.update(start_s=schedule[0]['start_s'], end_s=schedule[-1]['end_s'])
                for conflict in resource_conflicts([chain], doc):
                    rejections[conflict['code']] += 1
                if resource_conflicts([chain], doc):
                    return
            if len(chains) >= remaining:
                budget.hit.add('chains_per_task')
                return
            chain['candidate_id'] = identity(task['id'], flow_id, steps, context)
            chains.append(chain)
            return
        for plan in domains[len(steps)]:
            if 'chains_per_task' in budget.hit or not budget.take('chain_nodes'):
                return
            members = set(plan['members'])
            if steps and members.intersection(steps[-1]['members']):
                rejections['ADJACENT_REUSE'] += 1
                continue
            if not template['allow_nonadjacent_reuse'] and any(members.intersection(s['members']) for s in steps):
                rejections['NONADJACENT_REUSE'] += 1
                continue
            crossing = checked_edges(edge_requests(steps[-1], plan, template['steps'][len(steps)]['incoming_relation']),
                                     links, model) if steps else []
            if any(e['status'] != 'pass' for e in crossing):
                for e in crossing:
                    if e['status'] != 'pass':
                        rejections[e['status'] + ':' + e['reason']] += 1
                continue
            walk(steps + [plan], edges + crossing)
    walk([], [])
    return chains, dict(rejections)


def run_teams(jobs, scene, mapping, config):
    jobs, scene, mapping = deepcopy(jobs), deepcopy(scene), deepcopy(mapping)
    legacy, config = prepare(mapping, config)
    for value in (jobs, scene, mapping, config):
        require_nfc_source(value)
    validate_document(scene)
    require('knowledge' in scene and not any(k in scene['knowledge'] for k in ('sequences_file', 'sequences_inline')),
            'team input needs real OWL knowledge, without prewritten routes')
    require(not scene['policies'].get('article_table10', False),
            'team phase one does not implement Table-10 parallel target loads; use an explicitly non-Table-10 study')
    require(type(jobs) is list and 0 < len(jobs) <= 50, 'expected 1..50 jobs')
    require(all(type(j) is dict and set(j) == {'task_id', 'catalog', 'request'} for j in jobs), 'invalid jobs')
    task_ids = {t['id'] for t in scene['tasks']}
    require(len({j['task_id'] for j in jobs}) == len(jobs) and {j['task_id'] for j in jobs} == task_ids,
            'jobs must cover tasks exactly once')
    require(set(config['schedules']) <= task_ids, 'schedule references unknown task')
    require(all('bridge_task_id' not in t['attributes'] for t in scene['tasks']), 'reserved task attribute collision')
    budget = Budget(config['budgets'])
    with knowledge_context(scene) as (_, base_knowledge):
        knowledge = deepcopy(base_knowledge)
    context = dict(identity_version=IDENTITY, design_context=design_context(scene),
                   knowledge_snapshot=knowledge['sha256'],
                   mapping=sha({**mapping, 'entries': sorted(mapping['entries'], key=canonical)}),
                   communication_model=mapping['communication_model'],
                   internal_policy=config['internal_policy'], control_policy=config['control_policy'],
                   schedules=config['schedules'])
    result = dict(format_version=VERSION, status='undetermined', mode=PLAN_MODE,
                  run_id=new_run_id(), identity_version=IDENTITY, context=context,
                  input_snapshot=dict(jobs=jobs, scene=scene, mapping=mapping, config=config),
                  input_hash=sha(dict(jobs=jobs, scene=scene, mapping=mapping, config=config)),
                  knowledge=knowledge, planning=[], analyses=[], candidates={}, feasible_networks=[],
                  evaluation='not_run', optimality='not_run', execution_confirmed=False,
                  domain_validity='pending', supported_exports=['json', 'markdown'])
    result['network_search_scope'] = 'enumeration_over_generated_candidates_only'
    complete = True
    for job in sorted(jobs, key=lambda j: j['task_id']):
        request = deepcopy(job['request'])
        request['search']['mode'] = 'enumerate_bounded'
        problem = prepare_problem(job['catalog'], request)
        search = SearchSession(problem).run(max_candidates=config['budgets']['flows_per_task'])
        result['planning'].append(dict(task_id=job['task_id'], search=search))
        complete &= search['search_status'] == 'exhausted' and not search['unknown_conditions_seen']
        owned = dict(task_id=job['task_id'], catalog=problem.catalog, request=problem.request)
        for candidate in search['candidates']:
            if not budget.take('flow_analyses'):
                break
            compiled, trace = compile_tuple([owned], [candidate], scene, legacy)
            require(compiled is not None, 'unsupported logical mapping: ' + str(trace))
            compiled['knowledge']['sequences_inline']['evaluation']['mode'] = 'feasibility_only'
            flow_id, payload = flow_identity(owned, candidate, legacy)
            task_source = next(t for t in scene['tasks'] if t['id'] == job['task_id'])
            context_for_chain = dict(context, task_request=task_context(task_source, owned), flow_semantics=sha(payload))
            with knowledge_context(compiled) as (effective, metadata):
                validate_document(effective)
                owl = reason_scene(effective, load_capability_ontology())
                doc, inference = prepare_artifacts(effective, owl_result=owl)
                task = next(t for t in doc['tasks'] if t['id'] == job['task_id'])
                template = compiled['knowledge']['sequences_inline']['templates'][0]
                base = infer_task_capabilities(task, doc['task_rules'], required_roles=(),
                                               ontology_requirements=task_requirements(task, owl))
                stages, domains, union = [], [], {r: set() for r in base['required_capabilities']}
                for step in template['steps']:
                    node, caps = step_requirements(task, template, step, owl)
                    if not caps:
                        complete = False
                    union[step['role']].update(caps)
                    rows = contributions(task, step, caps, doc, inference, owl)
                    plans, rejected = stage_plans(step, caps, rows, {(e['from'], e['to']): e for e in doc['links']},
                                                  mapping['communication_model'], config, budget)
                    stages.append(dict(step=step, step_uri=node, required_capabilities=caps,
                                       contributions=rows, plans=plans, rejected_connections=rejected))
                    domains.append(plans)
                mission_covered = all(set(caps) <= union[r] for r, caps in base['required_capabilities'].items())
                requirements_known = not base['blockers'] and mission_covered
                complete &= not base['blockers']
                remaining = config['budgets']['chains_per_task'] - sum(c['task'] == task['id'] for c in result['candidates'].values())
                chains, rejected = generate_chains(task, flow_id, template, domains, doc, config, context_for_chain,
                                                   budget, remaining) if requirements_known else ([], {})
                analysis = dict(task_id=task['id'], flow_id=flow_id, flow_identity_payload=payload,
                                chain_context=context_for_chain, flow_candidate=candidate, job=owned,
                                design_input=compiled, knowledge=metadata, owl_reasoning=owl, ontology_inference=inference,
                                task_requirements=base, mission_covered=mission_covered, stages=stages,
                                rejected_chains=rejected, candidate_ids=[c['candidate_id'] for c in chains])
                result['analyses'].append(analysis)
                for chain in chains:
                    result['candidates'][chain['candidate_id']] = chain
                # Unknown contributions/connections imply unresolved feasibility,
                # never a closed-world proof of infeasibility.
                if any(v == 'unknown' for s in stages for row in s['contributions'] for v in row['capabilities'].values()):
                    complete = False
                if any(e['status'] == 'unknown' for s in stages for r in s['rejected_connections'] for e in r.get('connections', [])):
                    complete = False
                if any(k.startswith('unknown:') for k in rejected):
                    complete = False
    result['candidate_search_complete'] = complete and not budget.hit
    all_timed = set(config['schedules']) == task_ids
    result['resource_scope'] = 'explicit_full_stage_intervals_and_cooldown' if all_timed else 'inventory_only_without_network_timing'
    result['network_rejections'] = []
    groups = [[c for c in result['candidates'].values() if c['task'] == t] for t in sorted(task_ids)]
    network_complete = all_timed
    if all_timed:
        # Fresh resource preparation supplies the real capacities; no fake group
        # equipment, score bounds or ranking are introduced.
        for chosen in product(*groups):
            if not budget.take('network_nodes'):
                network_complete = False
                break
            conflicts = resource_conflicts(chosen, scene)
            selected = {c['task']: c for c in chosen}
            conflicts += [dict(code='PRECEDENCE', **d) for d in scene.get('dependencies', [])
                          if selected[d['after']]['start_s'] < selected[d['before']]['end_s'] + d['gap_s']]
            if conflicts:
                result['network_rejections'].append(dict(candidate_ids=[c['candidate_id'] for c in chosen], conflicts=conflicts))
                continue
            if len(result['feasible_networks']) >= config['budgets']['networks']:
                budget.hit.add('networks')
                network_complete = False
                break
            result['feasible_networks'].append([c['candidate_id'] for c in chosen])
    result['network_search_complete'] = network_complete
    result['budget'] = dict(used=dict(budget.used), hit=sorted(budget.hit))
    if result['feasible_networks']:
        result['status'] = 'feasible_networks'
    elif not all_timed and all(groups):
        result['status'] = 'feasible_chains_timing_unverified'
    elif result['candidate_search_complete'] and (network_complete or not all_timed):
        result['status'] = 'no_feasible_network_in_bounds' if all_timed else 'no_feasible_chain_in_bounds'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('jobs', 'scene', 'mapping', 'config', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    require(not args.output_dir.exists(), 'output directory already exists')
    manifest = read_json(args.jobs)
    require(manifest['format_version'] == 'task-flow-design-jobs-1.0', 'unsupported jobs manifest')
    jobs = [dict(task_id=j['task_id'], catalog=read_json(args.jobs.parent / j['catalog_file']),
                 request=read_json(args.jobs.parent / j['request_file'])) for j in manifest['jobs']]
    result = run_teams(jobs, read_json(args.scene, allow_finite_floats=True),
                       read_json(args.mapping, allow_finite_floats=True), read_json(args.config, allow_finite_floats=True))
    from .team_verify import verify_teams
    result['independent_verification'] = verify_teams(result)
    require(result['independent_verification']['status'] == 'passed', 'independent team verification failed')
    args.output_dir.mkdir(parents=True)
    (args.output_dir / 'team_report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for i, analysis in enumerate(result['analyses'], 1):
        for name, content in knowledge_outputs(analysis['design_input'], analysis).items():
            (args.output_dir / (f'analysis-{i:03d}-' + name)).write_text(content, encoding='utf-8')
    lines = ['# Complementary team feasibility (phase one)', '', f"Status: {result['status']}",
             f"Chains: {len(result['candidates'])}; feasible networks: {len(result['feasible_networks'])}",
             f"Candidate coverage complete: {result['candidate_search_complete']}",
             f"Network enumeration complete: {result['network_search_complete']}",
             'No scores, optimum, RDF, observations or physical execution are claimed.', '']
    for chain in result['candidates'].values():
        lines.append(chain['task'] + ': ' + ' -> '.join('{' + ', '.join(s['members']) + '}' for s in chain['plan']['steps']))
    (args.output_dir / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(dict(status=result['status'], chains=len(result['candidates']), networks=len(result['feasible_networks']),
                          independent_verification=result['independent_verification']), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
