"""Verified activity planning -> real OWL/resource design, with bounded fallback.

Selection is the first resource-feasible tuple of logical flows. Network optimality
is scoped to that tuple, never to unexplored activity sequences. No execution,
automatic scheduling, multi-member activities or FDNA adaptation is claimed.
"""
import argparse
from copy import deepcopy
import hashlib
from itertools import product
import json
from pathlib import Path

from .contracts import ContractError, prepare_problem, read_json, require
from .planner import SearchSession
from .validator import verify_candidate
from design.engine import DesignError, SearchLimitError, design, require_finite, validate_document
from design.knowledge import knowledge_context
from design.sequences import ACTIVITY_ROLES, validate_sequences


def digest(value):
    require_finite(value)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def exact(value, fields, label):
    require(type(value) is dict and set(value) == set(fields), 'invalid ' + label + ' fields')


def refs(value):
    require(type(value) is list and bool(value) and all(type(s) is str and s.strip() for s in value)
            and len(set(value)) == len(value), 'expected nonempty unique source references')


def prepare_mapping(mapping):
    digest(mapping)
    exact(mapping, ['format_version', 'mapping_id', 'version', 'entries', 'communication_model',
                    'allow_nonadjacent_reuse', 'evaluation', 'source_refs'], 'design mapping')
    require(mapping['format_version'] == 'task-flow-design-1.0', 'unsupported design mapping format')
    require(all(type(mapping[k]) is str and mapping[k].strip() for k in ('mapping_id','version')), 'mapping identity missing')
    require(mapping['communication_model'] in ('directed','undirected'), 'invalid communication model')
    require(type(mapping['allow_nonadjacent_reuse']) is bool, 'reuse must be Boolean')
    exact(mapping['evaluation'], ['mode', 'source_refs'], 'evaluation')
    require(mapping['evaluation']['mode'] in ('sequential_product','insight_lock_product','sequential_product_v2'), 'bridge needs scored activity metrics')
    refs(mapping['source_refs'])
    refs(mapping['evaluation']['source_refs'])
    require(type(mapping['entries']) is list and 0 < len(mapping['entries']) <= 1000, 'invalid mapping entries')
    seen = set()
    for e in mapping['entries']:
        exact(e, ['catalog_id','activity_id','activity_version','target_parameter','activity','role',
                  'min_level','incoming_relation','source_refs'], 'activity mapping')
        require(all(type(e[k]) is str and e[k].strip() for k in
                    ('catalog_id','activity_id','activity_version','target_parameter')), 'invalid mapping identity')
        key = (e['catalog_id'],e['activity_id'],e['activity_version'])
        require(key not in seen, 'duplicate activity mapping')
        seen.add(key)
        require(type(e['activity']) is str and e['activity'] in ACTIVITY_ROLES and
                e['role'] == ACTIVITY_ROLES[e['activity']], 'activity/role mismatch')
        require(type(e['min_level']) in (int,float) and 0 <= e['min_level'] <= 1, 'invalid level threshold')
        require(e['incoming_relation'] in ('communication','control'), 'invalid incoming relation')
        refs(e['source_refs'])
    return deepcopy(mapping)


def compile_tuple(jobs, candidates, scene, mapping):
    """Consume replayed instances, never capability_demands asserted by a caller."""
    document = deepcopy(scene)
    entries = {(e['catalog_id'],e['activity_id'],e['activity_version']):e for e in mapping['entries']}
    tasks = {t['id']:t for t in document['tasks']}
    templates, traces = [], []
    for job, candidate in zip(jobs, candidates):
        problem = prepare_problem(job['catalog'],job['request'])
        verification = verify_candidate(problem.catalog,problem.request,candidate)
        if verification['verification_status'] != 'passed':
            return None, {'status':'verification_failed','task_id':job['task_id'],'verification':verification}
        task = tasks[job['task_id']]
        steps = []
        for index, instance in enumerate(candidate['sequence']['steps']):
            key = (problem.catalog['catalog_id'],instance['activity_id'],instance['activity_version'])
            entry = entries.get(key)
            if entry is None:
                return None, {'status':'unsupported_mapping','task_id':job['task_id'],'step_index':index,
                              'reason':'activity/version has no explicit design mapping'}
            # Version and parameter instances have already been independently replayed.
            # This first adapter supports exactly one target parameter, not arbitrary
            # document objects silently redirected to a scene target.
            if instance['parameters'] != {entry['target_parameter']:task['target']}:
                return None, {'status':'unsupported_mapping','task_id':job['task_id'],'step_index':index,
                              'reason':'expected exactly the bound design target parameter'}
            action = problem.activities[instance['activity_id']]
            source = list(dict.fromkeys(action['source_refs'] + entry['source_refs'] + mapping['source_refs']))
            step = dict(id=f's{index+1}', activity=entry['activity'], role=entry['role'],
                        capabilities=deepcopy(action['required_capabilities']), min_level=entry['min_level'], source_refs=source)
            if index:
                step['incoming_relation'] = entry['incoming_relation']
            steps.append(step)
        # A task-specific condition uses an explicit reserved attribute. It cannot
        # silently collide with user data, nor apply another target's template.
        task['attributes']['bridge_task_id'] = task['id']
        templates.append(dict(id='planned-'+str(len(templates)+1),
            when=[{'field':'task.bridge_task_id','op':'eq','value':task['id']}], steps=steps,
            allow_nonadjacent_reuse=mapping['allow_nonadjacent_reuse'],source_refs=mapping['source_refs']))
        traces.append(dict(task_id=task['id'],candidate_id=candidate['candidate_id'], verification=verification,
                           instances=deepcopy(candidate['sequence']['steps']), generated_steps=steps))
    config = dict(templates=templates, communication_model=mapping['communication_model'],
                  evaluation=mapping['evaluation'], source_refs=mapping['source_refs'])
    try:
        with knowledge_context(document):
            from design.capability_identity import load_capability_ontology
            validate_sequences(config, load_capability_ontology())
    except DesignError as exc:
        return None, {'status':'unsupported_mapping','reason':str(exc),'tasks':traces}
    document['knowledge']['sequences_inline'] = config
    return document, {'status':'compiled','tasks':traces,'generated_sequences_sha256':digest(config)}


def run_bridge(jobs, scene, mapping, *, max_checks=100, max_flows_per_task=100):
    """Fallback across exact flow tuples; no prefix-level infeasibility learning."""
    require(type(max_checks) is int and 1 <= max_checks <= 1000, 'max_checks must be 1..1000')
    require(type(max_flows_per_task) is int and 1 <= max_flows_per_task <= 10000, 'invalid per-task flow limit')
    scene, mapping, jobs = deepcopy(scene), prepare_mapping(mapping), deepcopy(jobs)
    validate_document(scene)
    require('knowledge' in scene, 'real OWL knowledge configuration required')
    require('sequences_file' not in scene['knowledge'] and 'sequences_inline' not in scene['knowledge'],
            'bridge scene must not supply a prewritten sequence template')
    require(all('bridge_task_id' not in t['attributes'] for t in scene['tasks']), 'reserved task attribute collision')
    require(type(jobs) is list and 0 < len(jobs) <= 50, 'expected 1..50 task jobs')
    for job in jobs:
        exact(job,['task_id','catalog','request'],'task job')
        require(type(job['task_id']) is str, 'task id must be a string')
    require(len({j['task_id'] for j in jobs}) == len(jobs) and
            {j['task_id'] for j in jobs} == {t['id'] for t in scene['tasks']}, 'jobs must cover every scene task exactly once')
    sessions = []
    owned = []
    for job in jobs:
        exact(job,['task_id','catalog','request'],'task job')
        problem = prepare_problem(job['catalog'],job['request'])
        # Pause after each flow so resource feedback can resume the same frontier.
        request = deepcopy(problem.request)
        request['search']['mode'] = 'first_feasible'
        problem = prepare_problem(problem.catalog,request)
        owned.append(dict(task_id=job['task_id'],catalog=problem.catalog,request=problem.request))
        sessions.append(SearchSession(problem))
    # Freeze all external OWL/rule inputs. No rejection is reused across changes.
    with knowledge_context(scene) as (_, metadata):
        frozen_knowledge = deepcopy(metadata)
    pools, done, limits = [[] for _ in jobs], [False for _ in jobs], [False for _ in jobs]
    attempts, visited, selected = [], set(), None

    def advance(i):
        if done[i] or limits[i]:
            return False
        before = len(pools[i])
        report = sessions[i].run(max_candidates=max_flows_per_task)
        pools[i] = report['candidates']
        done[i] = report['search_status'] == 'exhausted'
        limits[i] = report['search_status'] == 'limit_reached'
        return len(pools[i]) > before

    for i in range(len(jobs)):
        advance(i)
    status = 'search_exhausted'
    while all(pools):
        for candidates in product(*pools):
            identity = tuple(c['candidate_id'] for c in candidates)
            if identity in visited:
                continue
            if len(attempts) >= max_checks:
                status = 'analysis_limit'
                break
            visited.add(identity)
            with knowledge_context(scene) as (_, current):
                require(current == frozen_knowledge, 'knowledge changed during bridge run')
            compiled, compilation = compile_tuple(owned,candidates,scene,mapping)
            attempt = dict(candidate_ids=list(identity),compilation=compilation,design_status='not_run')
            if compiled is not None:
                attempt['design_input'] = compiled
                try:
                    report = design(compiled)
                except SearchLimitError as exc:
                    attempt.update(design_status='search_limit',reason=str(exc))
                except DesignError as exc:
                    # Reasoner failure, malformed metrics, and design errors are
                    # never feedback that this logical flow is proven infeasible.
                    attempt.update(design_status='design_error',reason=str(exc))
                else:
                    with knowledge_context(scene) as (_, current):
                        require(current == frozen_knowledge, 'knowledge changed during design analysis')
                    attempt.update(design_status=report['status'],design_report=report)
                    if report['status'] == 'complete' and report['canonical_export_ready']:
                        selected = len(attempts)
            attempts.append(attempt)
            if selected is not None:
                status = 'selected'
                break
        if status in ('analysis_limit','selected'):
            break
        added = False
        for i in range(len(jobs)):
            added = advance(i) or added
        if not added:
            break
    reports = [s.report() for s in sessions]
    if selected is None and status != 'analysis_limit':
        if any(limits):
            status = 'search_limit'
        elif any(a['design_status'] == 'search_limit' for a in attempts):
            status = 'design_limit'
        elif any(not p for p in pools):
            status = 'blocked' if any(r['unknown_conditions_seen'] for r in reports) else 'logical_no_solution'
        elif any(r['unknown_conditions_seen'] for r in reports) or any(
                a['compilation']['status'] != 'compiled' or a['design_status'] != 'no_feasible_design' for a in attempts):
            status = 'blocked'
        else:
            status = 'resource_no_solution_in_bounds'
    return dict(format_version='task-flow-design-report-1.0',integration_status=status,
        selection_policy='first_resource_feasible_flow_tuple',selected_attempt=selected,
        optimality_scope='design optimum only within the selected fixed tuple of logical flows',
        global_flow_optimality='not_proven', execution_confirmed=False, fdna_integration='not_run',
        binding=dict(scene_sha256=digest(scene),mapping_sha256=digest(mapping),knowledge_sha256=frozen_knowledge['sha256'],
                     jobs_sha256=digest(owned)),
        input_snapshot=dict(scene=scene,mapping=mapping,jobs=owned),
        planning=[dict(task_id=j['task_id'],search=r) for j,r in zip(owned,reports)],
        attempts=attempts,limits=dict(max_checks=max_checks,max_flows_per_task=max_flows_per_task))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs',type=Path,required=True)
    parser.add_argument('--scene',type=Path,required=True)
    parser.add_argument('--mapping',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--max-checks',type=int,default=100)
    parser.add_argument('--max-flows-per-task',type=int,default=100)
    parser.add_argument('--mode',choices=['first_resource_feasible','joint_candidate_optimization'],
                        default='first_resource_feasible')
    parser.add_argument('--max-flow-analyses',type=int,default=10000)
    parser.add_argument('--input-profile',choices=['strict','minimal'],default='strict')
    parser.add_argument('--data-origin',choices=['synthetic_simulation','expert_assumption','real_observational','mixed'])
    parser.add_argument('--generator-version')
    args = parser.parse_args(argv)
    try:
        require(not args.output_dir.exists(), 'output directory already exists; choose a new directory')
        manifest = read_json(args.jobs)
        exact(manifest,['format_version','jobs'],'job manifest')
        require(manifest['format_version'] == 'task-flow-design-jobs-1.0','unsupported jobs format')
        require(type(manifest['jobs']) is list, 'expected job list')
        jobs, protected = [], [args.jobs,args.scene,args.mapping]
        for entry in manifest['jobs']:
            exact(entry,['task_id','catalog_file','request_file'],'job file entry')
            catalog = args.jobs.parent / entry['catalog_file']
            request = args.jobs.parent / entry['request_file']
            jobs.append(dict(task_id=entry['task_id'],catalog=read_json(catalog),request=read_json(request)))
            protected.extend([catalog,request])
        scene = read_json(args.scene,allow_finite_floats=True)
        mapping = read_json(args.mapping,allow_finite_floats=True)
        profile_report = None
        if args.input_profile == 'minimal':
            from .input_profile import normalize_design
            jobs,scene,mapping,profile_report = normalize_design(jobs,scene,mapping,
                data_origin=args.data_origin,generator_version=args.generator_version)
        else:
            require(args.data_origin is None and args.generator_version is None, 'metadata convenience options require minimal profile')
        if args.mode == 'joint_candidate_optimization':
            from .joint import run_joint, export_joint
            report = run_joint(jobs,scene,mapping,max_flows_per_task=args.max_flows_per_task,
                               max_flow_analyses=args.max_flow_analyses)
            export_joint(report,args.output_dir,protected=protected)
            if profile_report is not None:
                _write_input_profile(args.output_dir,profile_report)
            print(json.dumps(dict(integration_status=report['integration_status'],
                global_optimality=report['global_optimality'],unique_chain_count=report['unique_chain_count'],
                output_dir=str(args.output_dir)),ensure_ascii=False))
            return 0 if report['integration_status'] in ('selected','feasible') else 1
        report = run_bridge(jobs,scene,mapping,
                            max_checks=args.max_checks,max_flows_per_task=args.max_flows_per_task)
        from source_data.common import write_outputs
        from design.__main__ import render_summary
        from design.knowledge import knowledge_outputs, knowledge_sources, ontology_for_document
        from design.rdf import design_to_rdf
        from check_ontology import analyze_graph
        payload = lambda x: json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        for index, attempt in enumerate(report['attempts']):
            if 'design_report' not in attempt:
                continue
            doc, result = attempt['design_input'],attempt['design_report']
            outputs = {'design_input.json':payload(doc),'design_report.json':payload(result),
                       'design_summary.md':render_summary(result)}
            outputs.update(knowledge_outputs(doc,result))
            if index == report['selected_attempt']:
                graph = design_to_rdf(doc,result,require_canonical=True)
                review = analyze_graph(ontology_for_document(doc)+graph)
                require(review.counts()['ERROR'] == review.counts()['WARN'] == 0, 'selected RDF validation failed')
                attempt['rdf_validation'] = review.counts()
                outputs['design.ttl'] = graph.serialize(format='turtle')
            write_outputs(args.output_dir/f'attempt-{index+1:03}',outputs,
                          protected=protected+knowledge_sources(doc))
        write_outputs(args.output_dir,{'bridge_report.json':payload(report)},protected=protected)
        if profile_report is not None:
            _write_input_profile(args.output_dir,profile_report)
        print(json.dumps(dict(integration_status=report['integration_status'],selected_attempt=report['selected_attempt'],
                             attempts=len(report['attempts']),output_dir=str(args.output_dir)),ensure_ascii=False))
        return 0 if report['integration_status'] == 'selected' else 1
    except (ValueError,OSError) as exc:
        status='context_changed' if str(exc).startswith('context_changed:') else 'invalid_input'
        print(json.dumps({'integration_status':status,'reason':str(exc)},ensure_ascii=False))
        return 2


def _write_input_profile(output,report):
    with (output/'input_profile.json').open('x',encoding='utf-8',newline='\n') as handle:
        handle.write(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


if __name__ == '__main__':
    raise SystemExit(main())
