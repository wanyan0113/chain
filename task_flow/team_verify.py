"""Independent structural/feasibility checker, never calls team enumeration.

Shares rule/OWL semantics and resource primitives with the production contract;
independently checks coverage, actual providers, topology and reservations.
Passing certifies the listed feasible outputs, not exhaustive-search coverage.
"""
from copy import deepcopy

from .contracts import require, prepare_problem
from .validator import verify_candidate
from .design_bridge import compile_tuple
from .joint_identity import sha, flow_identity, design_context, canonical
from .team_contracts import VERSION, PLAN_MODE, IDENTITY, prepare, identity
from design.engine import evaluate, infer_task_capabilities, validate_document
from design.knowledge import knowledge_context
from design.capability_identity import load_capability_ontology
from design.owl_scene import reason_scene, task_requirements
from design.ontology_inference import prepare_artifacts
from design.sequences import step_requirements
from design.plans import resource_conflicts


def _contribution_rows(stage, task, doc, inference, owl):
    """Audit diagnostic pass/fail/unknown rows independently of enumeration."""
    artifacts = {a['id']: a for a in doc['artifacts'] if a['role'] == stage['step']['role']}
    require([r['artifact'] for r in stage['contributions']] == sorted(artifacts), 'contribution row coverage changed')
    contexts = {(c['task'], c['artifact']): c for c in doc.get('contexts', [])}
    evidence = {r['artifact']: r for r in inference['artifacts']}
    for row in stage['contributions']:
        a = artifacts[row['artifact']]
        context = contexts.get((task['id'], a['id']), {})
        level = a['attributes'].get('level_' + stage['step']['activity'])
        ls = ('unknown' if not isinstance(level, dict) or level.get('unit') != '1' or
              type(level.get('value')) not in (int, float) or not 0 <= level['value'] <= 1 else
              'pass' if level['value'] >= stage['step']['min_level'] else 'fail')
        ts = ('pass' if a['role'] == 'coordination' else
              'unknown' if not isinstance(task['attributes'].get('target_type'), str) or
              not isinstance(a['attributes'].get('target_types'), list) else
              'pass' if task['attributes']['target_type'] in a['attributes']['target_types'] else 'fail')
        issues = a['blocking_issue_ids'] + context.get('blocking_issue_ids', [])
        issues += ['owl-mapping:' + p['mapping'] for p in owl.get('data_projection', [])
                   if p['kind'] == 'artifact' and p['owner'] == a['id'] and p['required'] and p['status'] == 'unknown']
        common = ('fail' if not a['available'] or ls == 'fail' or ts == 'fail' else
                  'unknown' if issues or ls == 'unknown' or ts == 'unknown' else 'pass')
        facts = {**a['attributes'], 'ontology_types': evidence[a['id']]['inferred_types'],
                 'ontology_capabilities': inference['relations']['capabilities'][a['id']]}
        scopes = dict(task={**task['attributes'], 'ontology_types': task_requirements(task, owl)['types']},
                      artifact=facts, context=context.get('attributes', {}))
        checks = [evaluate(r, scopes) for r in doc['artifact_rules'] if r['role'] == a['role'] and
                  r['capability'] in stage['required_capabilities']]
        states = {}
        for cap in stage['required_capabilities']:
            statuses = [r['status'] for r in checks if r['capability'] == cap]
            rule = 'pass' if 'pass' in statuses else 'unknown' if not statuses or 'unknown' in statuses else 'fail'
            states[cap] = 'fail' if 'fail' in (rule, common) else 'unknown' if 'unknown' in (rule, common) else 'pass'
        expected = dict(artifact=a['id'], common_status=common, capabilities=states, level_check=ls,
                        target_type_check=ts, rules=checks, blocking_issue_ids=issues, ontology_evidence=evidence[a['id']])
        require(row == expected, 'contribution evidence changed')


def _edge(left, right, relation, doc, model):
    # Deliberately do not use the generator's connection/request construction.
    links = {(e['from'], e['to']): e for e in doc['links']}
    edge, reverse = links.get((left, right)), False
    if edge is None and model == 'undirected' and relation == 'communication':
        edge = links.get((right, left))
        reverse = edge is not None
    require(edge is not None, 'missing necessary direct edge')
    require(edge['communicates'] is True and not edge['blocking_issue_ids'] and
            edge['distance']['value'] < edge['max_distance']['value'], 'unverified communication')
    require(relation != 'control' or edge['controls'] is True, 'unverified direct control')
    return dict(from_member=left, to_member=right, relation=relation, status='pass', reason='CONNECTED',
                evidence={**edge, 'used_from': left, 'used_to': right, 'reversed_physical_link': reverse})


def _plan(plan, step, caps, task, doc, inference, owl, config, model):
    require(set(plan) == {'step_instance_id', 'activity', 'role', 'required_capabilities', 'min_level',
                         'members', 'capability_assignments', 'hub', 'internal_connections'}, 'invalid plan fields')
    require(plan['step_instance_id'] == step['id'] and plan['activity'] == step['activity'] and
            plan['role'] == step['role'] and plan['min_level'] == step['min_level'] and
            plan['required_capabilities'] == caps, 'stage semantics changed')
    members = plan['members']
    require(members == sorted(set(members)) and 1 <= len(members) <= config['max_members'], 'invalid members')
    assignment = plan['capability_assignments']
    require(caps and set(assignment) == set(caps) and set(assignment.values()) == set(members),
            'missing capability, foreign provider or noncontributing member')
    require(plan['hub'] in members, 'hub is not a member')
    artifacts = {a['id']: a for a in doc['artifacts']}
    contexts = {(c['task'], c['artifact']): c for c in doc.get('contexts', [])}
    ev = {e['artifact']: e for e in inference['artifacts']}
    tf = {**task['attributes'], 'ontology_types': task_requirements(task, owl)['types']}
    for member in members:
        a = artifacts[member]
        context = contexts.get((task['id'], member), {})
        require(a['role'] == step['role'] and a['available'] and not a['blocking_issue_ids'] and
                not context.get('blocking_issue_ids'), 'unavailable, blocked or wrong-role member')
        require(not any(p['kind'] == 'artifact' and p['owner'] == member and p['required'] and p['status'] == 'unknown'
                        for p in owl.get('data_projection', [])), 'required artifact projection unknown')
        level = a['attributes'].get('level_' + step['activity'])
        require(type(level) is dict and level.get('unit') == '1' and type(level.get('value')) in (int, float)
                and step['min_level'] <= level['value'] <= 1, 'member level not proven')
        require(a['role'] == 'coordination' or
                (isinstance(task['attributes'].get('target_type'), str) and
                 isinstance(a['attributes'].get('target_types'), list) and
                 task['attributes']['target_type'] in a['attributes']['target_types']), 'member target not proven')
        facts = {**a['attributes'], 'ontology_types': ev[member]['inferred_types'],
                 'ontology_capabilities': inference['relations']['capabilities'][member]}
        for cap, provider in assignment.items():
            if provider != member:
                continue
            checks = [evaluate(r, dict(task=tf, artifact=facts, context=context.get('attributes', {})))
                      for r in doc['artifact_rules'] if r['role'] == step['role'] and r['capability'] == cap]
            require(any(c['status'] == 'pass' for c in checks), 'responsible member capability not proven')
    expected = [_edge(m, plan['hub'], 'communication', doc, model) for m in members if m != plan['hub']]
    require(plan['internal_connections'] == expected, 'internal edge evidence changed')


def verify_teams(report, *, expected_input_hash=None):
    try:
        _verify(report, expected_input_hash)
        return dict(status='passed', scope='listed_plans_chains_and_networks_with_fresh_OWL_and_flow_replay',
                    search_coverage_certified=False, optimality='not_run')
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        return dict(status='failed', reason=str(exc))


def _verify(report, expected_input_hash):
    require(report['format_version'] == VERSION and report['mode'] == PLAN_MODE and
            report['identity_version'] == IDENTITY, 'unsupported team report')
    require(report['evaluation'] == 'not_run' and report['optimality'] == 'not_run' and
            report['execution_confirmed'] is False and report['domain_validity'] == 'pending', 'unsupported claims')
    snapshot = report['input_snapshot']
    require(report['input_hash'] == sha(snapshot), 'source binding changed')
    require(expected_input_hash is None or report['input_hash'] == expected_input_hash, 'external source binding changed')
    scene, mapping, config = snapshot['scene'], snapshot['mapping'], snapshot['config']
    legacy, config = prepare(mapping, config)
    validate_document(scene)
    require(not scene['policies'].get('article_table10', False), 'unsupported Table-10 team contract')
    require(not any(k in scene['knowledge'] for k in ('sequences_file', 'sequences_inline')), 'prewritten source route')
    with knowledge_context(scene) as (_, knowledge):
        require(report['knowledge'] == knowledge, 'base knowledge changed')
    expected_context = dict(identity_version=IDENTITY, design_context=design_context(scene),
                            knowledge_snapshot=knowledge['sha256'],
                            mapping=sha({**mapping, 'entries': sorted(mapping['entries'], key=canonical)}),
                            communication_model=mapping['communication_model'], internal_policy=config['internal_policy'],
                            control_policy=config['control_policy'], schedules=config['schedules'])
    require(report['context'] == expected_context, 'execution context changed')
    jobs = {j['task_id']: j for j in snapshot['jobs']}
    seen = set()
    for analysis in report['analyses']:
        owned = deepcopy(jobs[analysis['task_id']])
        owned['request']['search']['mode'] = 'enumerate_bounded'
        problem = prepare_problem(owned['catalog'], owned['request'])
        owned.update(catalog=problem.catalog, request=problem.request)
        require(analysis['job'] == owned, 'flow source job changed')
        candidate = analysis['flow_candidate']
        require(verify_candidate(owned['catalog'], owned['request'], candidate)['verification_status'] == 'passed',
                'logical conditions/effects or final goal failed replay')
        fid, payload = flow_identity(owned, candidate, legacy)
        require(analysis['flow_id'] == fid and analysis['flow_identity_payload'] == payload, 'flow identity changed')
        compiled, trace = compile_tuple([owned], [candidate], scene, legacy)
        require(compiled is not None, 'cannot compile replayed flow')
        compiled['knowledge']['sequences_inline']['evaluation']['mode'] = 'feasibility_only'
        require(compiled == analysis['design_input'], 'compiled source changed')
        from .joint_identity import task_context
        task_source = next(t for t in scene['tasks'] if t['id'] == analysis['task_id'])
        context = dict(expected_context, task_request=task_context(task_source, owned), flow_semantics=sha(payload))
        require(context == analysis['chain_context'], 'chain context changed')
        with knowledge_context(compiled) as (effective, metadata):
            require(metadata == analysis['knowledge'], 'compiled knowledge changed')
            owl = reason_scene(effective, load_capability_ontology())
            for key in ('facts', 'types', 'input_graph_digest', 'data_projection'):
                require(owl[key] == analysis['owl_reasoning'][key], 'OWL evidence changed: ' + key)
            doc, inference = prepare_artifacts(effective, owl_result=owl)
            require(inference == analysis['ontology_inference'], 'artifact inference changed')
            task = next(t for t in doc['tasks'] if t['id'] == analysis['task_id'])
            template = compiled['knowledge']['sequences_inline']['templates'][0]
            base = infer_task_capabilities(task, doc['task_rules'], required_roles=(),
                                           ontology_requirements=task_requirements(task, owl))
            require(base == analysis['task_requirements'], 'mission requirements changed')
            required = [step_requirements(task, template, s, owl)[1] for s in template['steps']]
            union = {r: set() for r in base['required_capabilities']}
            for step, caps in zip(template['steps'], required):
                union[step['role']].update(caps)
            covered = all(set(caps) <= union[r] for r, caps in base['required_capabilities'].items())
            require(analysis['mission_covered'] == covered, 'mission coverage changed')
            require(len(analysis['stages']) == len(required), 'stage count changed')
            for stage, step, caps in zip(analysis['stages'], template['steps'], required):
                require(stage['step'] == step and stage['required_capabilities'] == caps, 'stage requirements changed')
                require(stage['step_uri'] == step_requirements(task, template, step, owl)[0], 'stage URI changed')
                _contribution_rows(stage, task, doc, inference, owl)
                for plan in stage['plans']:
                    _plan(plan, step, caps, task, doc, inference, owl, config, mapping['communication_model'])
            for cid in analysis['candidate_ids']:
                require(not base['blockers'] and covered, 'unresolved or uncovered mission')
                chain = report['candidates'][cid]
                require(chain['candidate_id'] == cid and chain['flow_id'] == fid and chain['task'] == task['id'], 'chain provenance changed')
                require(chain['metrics'] is None and chain['score'] is None and chain['plan']['mode'] == PLAN_MODE, 'unexpected team score')
                steps = chain['plan']['steps']
                require(len(steps) == len(required), 'chain stage count changed')
                edges, previous_members, used_members = [], set(), set()
                for i, (plan, step, caps) in enumerate(zip(steps, template['steps'], required)):
                    _plan(plan, step, caps, task, doc, inference, owl, config, mapping['communication_model'])
                    require(plan in analysis['stages'][i]['plans'], 'chain plan not bound to stage evidence')
                    members = set(plan['members'])
                    require(not members.intersection(previous_members), 'adjacent member reuse')
                    require(template['allow_nonadjacent_reuse'] or not members.intersection(used_members), 'nonadjacent member reuse')
                    if i:
                        requests = {(steps[i-1]['hub'], plan['hub'], 'communication')}
                        if step['incoming_relation'] == 'control':
                            requests |= {(a, b, 'control') for a in steps[i-1]['members'] for b in members}
                        edges.extend(_edge(a, b, r, doc, mapping['communication_model']) for a, b, r in sorted(requests))
                    previous_members = members
                    used_members |= members
                require(chain['plan']['connections'] == edges, 'handoff/control evidence changed')
                require(cid == identity(task['id'], fid, steps, context), 'candidate identity changed')
                artifacts = {a['id']: a for a in doc['artifacts']}
                from collections import Counter
                inventory = Counter(m for s in steps for m in s['members'] if artifacts[m]['consumable'])
                require(all(n <= artifacts[m]['inventory'] for m, n in inventory.items()), 'inventory conflict')
                schedule = config['schedules'].get(task['id'])
                usage = []
                if schedule:
                    require(len(schedule) == len(steps) and schedule[0]['start_s'] == task['start_s'], 'schedule mismatch')
                    for p, interval in zip(steps, schedule):
                        for member in p['members']:
                            a = artifacts[member]
                            usage.append(dict(artifact=member, capacity=1, inventory=int(a['consumable']),
                                              start_s=interval['start_s'], end_s=interval['end_s'] + a['cooldown_s']))
                    require(chain['start_s'] == schedule[0]['start_s'] and chain['end_s'] == schedule[-1]['end_s'], 'chain timing changed')
                    require(not resource_conflicts([chain], doc), 'chain resource conflict')
                else:
                    usage = [dict(artifact=m, capacity=1, inventory=int(artifacts[m]['consumable']),
                                  start_s=None, end_s=None) for p in steps for m in p['members']]
                require(chain['plan']['resource_usage'] == usage, 'real-member resource reservations changed')
                require(chain['resource_feasibility'] == ('passed' if schedule else 'timing_not_provided'), 'resource claim changed')
                seen.add(cid)
    require(seen == set(report['candidates']), 'unbound candidate')
    for ids in report['feasible_networks']:
        chains = [report['candidates'][cid] for cid in ids]
        require(len(chains) == len(jobs) and {c['task'] for c in chains} == set(jobs), 'network task coverage changed')
        require(all(c['resource_feasibility'] == 'passed' for c in chains), 'network lacks explicit timing')
        require(not resource_conflicts(chains, scene), 'network capacity/inventory conflict')
        selected = {c['task']: c for c in chains}
        require(all(selected[d['after']]['start_s'] >= selected[d['before']]['end_s'] + d['gap_s']
                    for d in scene.get('dependencies', [])), 'network precedence conflict')
    require(not report['budget']['hit'] or report['candidate_search_complete'] is False or
            report['network_search_complete'] is False, 'budget-exhausted report claims complete coverage')
    require(report['status'] != 'feasible_networks' or bool(report['feasible_networks']), 'empty feasible-network claim')
