"""Prepare a separate synthetic complementary-team fixture; no legacy edits."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'task_flow/examples/observatory_multistage'
EXAMPLE = ROOT / 'task_flow/examples/complementary_team'


def inputs():
    load = lambda name: json.loads((BASE / name).read_text(encoding='utf-8'))
    scene, mapping, catalog, request = (load(n) for n in ('scene.json', 'mapping.json', 'catalog-1.json', 'request-1.json'))
    scene['scope'] = 'Synthetic phase-one complementary classification/decision; not physical accuracy evidence'
    scene['policies']['article_table10'] = False
    scene['tasks'] = [scene['tasks'][0]]
    scene['tasks'][0]['id'] = 'T1'
    second = deepcopy(scene['tasks'][0])
    second.update(id='T2', target='Second-Rift')
    scene['tasks'].append(second)
    ids = {'R-Survey', 'R-Dispatch', 'R-Seal-A', 'O-Phase-Tuner', 'O-Safety-Auditor'}
    scene['artifacts'] = [a for a in scene['artifacts'] if a['id'] in ids]
    a = next(a for a in scene['artifacts'] if a['id'] == 'O-Phase-Tuner')
    b = next(a for a in scene['artifacts'] if a['id'] == 'O-Safety-Auditor')
    c = deepcopy(a)
    c['id'] = 'O-Combined'
    c['ontology_types'] += b['ontology_types']
    c['attributes']['supported_functions'] += b['attributes']['supported_functions']
    scene['artifacts'].append(c)
    for artifact in scene['artifacts']:
        artifact['capacity'] = 1 if artifact['id'].startswith('O-') else 2
        artifact['cooldown_s'] = 0
        if artifact['consumable']:
            artifact['inventory'] = 2
    ids.add(c['id'])
    original_contexts = [x for x in scene.get('contexts', []) if x['task'] == 'T1' and x['artifact'] in ids]
    scene['contexts'] = original_contexts + [{**deepcopy(x), 'task': 'T2'} for x in original_contexts]
    for key in ('resource_groups', 'dependencies', 'ontology_relations'):
        scene.pop(key, None)
    # Declared synthetic complete directed physical graph; tests remove/fail
    # particular edges to ensure the hub/control contracts do not bypass them.
    edge = deepcopy(scene['links'][0])
    edge.update(communicates=True, controls=True, blocking_issue_ids=[])
    edge['distance']['value'] = 1
    edge['max_distance']['value'] = 100
    scene['links'] = [{**deepcopy(edge), 'from': left, 'to': right} for left in sorted(ids) for right in sorted(ids) if left != right]
    caps = ['urn:observatory-semantic-demo:PhaseTune', 'urn:observatory-semantic-demo:SafetyAudit']
    for action in catalog['activities']:
        if action['id'] in ('analyze_primary', 'analyze_secondary'):
            action['required_capabilities'] = caps
    # Keep conditions/effects: observed -> analyzed -> decided -> stable.
    # No activity sequence is supplied by the user or the fixture.
    jobs = [dict(task_id='T1', catalog=catalog, request=request),
            json.loads(json.dumps(dict(task_id='T2', catalog=catalog, request=request)).replace('North-Rift', 'Second-Rift'))]
    from task_flow.contracts import canonical_sha256
    for job in jobs:
        job['request']['catalog_sha256'] = canonical_sha256(job['catalog'])
    mapping['format_version'] = 'task-flow-team-mapping-1.0'
    mapping['evaluation'] = dict(mode='feasibility_only', source_refs=['complementary-team-phase-one'])
    config = dict(format_version='task-flow-team-config-1.0', max_members=2,
                  internal_policy='member_to_hub_direct',
                  control_policy='all_contributing_controllers_to_all_executors_direct',
                  budgets=dict(flows_per_task=100, flow_analyses=100, team_nodes=10000, plans_per_stage=1000,
                               chain_nodes=10000, chains_per_task=1000, network_nodes=10000, networks=1000),
                  schedules={t: [dict(start_s=i*10, end_s=(i+1)*10) for i in range(4)] for t in ('T1', 'T2')})
    return jobs, scene, mapping, config


def write_example():
    jobs, scene, mapping, config = inputs()
    EXAMPLE.mkdir(parents=True, exist_ok=True)
    documents = dict(scene=scene, mapping=mapping, config=config)
    manifest = dict(format_version='task-flow-design-jobs-1.0', jobs=[])
    for i, job in enumerate(jobs, 1):
        documents[f'catalog-{i}'], documents[f'request-{i}'] = job['catalog'], job['request']
        manifest['jobs'].append(dict(task_id=job['task_id'], catalog_file=f'catalog-{i}.json', request_file=f'request-{i}.json'))
    documents['jobs'] = manifest
    for name, content in documents.items():
        (EXAMPLE / (name + '.json')).write_text(json.dumps(content, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    if args.prepare:
        write_example()
    if args.output_dir:
        from task_flow.team_feasibility import main as run
        return run(['--jobs', str(EXAMPLE/'jobs.json'), '--scene', str(EXAMPLE/'scene.json'),
                    '--mapping', str(EXAMPLE/'mapping.json'), '--config', str(EXAMPLE/'config.json'),
                    '--output-dir', str(args.output_dir)])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
