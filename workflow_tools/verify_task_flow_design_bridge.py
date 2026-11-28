"""Reproduce real CLI integration, bounded fallback and failure classifications."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
import io
import json
from pathlib import Path
import uuid

from task_flow.design_bridge import main as bridge_cli

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT/'task_flow/examples/design_bridge'


def require(value,message):
    if not value:
        raise RuntimeError(message)


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    output = ROOT/'generated/task-flow'/('design-bridge-verified-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    results = []
    for name,expected in [('fallback','selected'),('primary_available','selected'),
                          ('no_stabilizer','resource_no_solution_in_bounds'),('unknown_distance','blocked'),
                          ('unmapped','blocked'),('logical_short_bound','logical_no_solution'),
                          ('analysis_budget','analysis_limit')]:
        folder = output/name
        folder.mkdir(parents=True)
        scene = json.loads((EXAMPLE/'scene.json').read_text(encoding='utf-8'))
        mapping = json.loads((EXAMPLE/'mapping.json').read_text(encoding='utf-8'))
        # The full fallback uses three simultaneous tasks. Failure probes use
        # one target to isolate a specific constraint without unrelated failures.
        indices = [1,2,3] if name in ('fallback','primary_available','analysis_budget') else [1]
        if len(indices) == 1:
            scene['tasks'] = scene['tasks'][:1]
            scene['contexts'] = [c for c in scene['contexts'] if c['task'] == 'T1']
        if name == 'primary_available':
            next(a for a in scene['artifacts'] if a['id'] == 'R-Analysis')['available'] = True
        elif name == 'no_stabilizer':
            for a in scene['artifacts']:
                if a['id'].startswith('R-Seal-'):
                    a['inventory'] = 0
        elif name == 'unknown_distance':
            del next(a for a in scene['artifacts'] if a['id'] == 'R-Survey')['attributes']['detection_range']
        elif name == 'unmapped':
            mapping['entries'] = [e for e in mapping['entries'] if e['activity_id'] != 'observe']
        manifest = dict(format_version='task-flow-design-jobs-1.0',jobs=[])
        for i in indices:
            catalog = json.loads((EXAMPLE/f'catalog-{i}.json').read_text(encoding='utf-8'))
            request = json.loads((EXAMPLE/f'request-{i}.json').read_text(encoding='utf-8'))
            if name == 'logical_short_bound':
                request['search']['max_length'] = 2
            save(folder/f'catalog-{i}.json',catalog)
            save(folder/f'request-{i}.json',request)
            manifest['jobs'].append(dict(task_id=f'T{i}',catalog_file=f'catalog-{i}.json',request_file=f'request-{i}.json'))
        save(folder/'jobs.json',manifest)
        save(folder/'scene.json',scene)
        save(folder/'mapping.json',mapping)
        captured = io.StringIO()
        with redirect_stdout(captured):
            code = bridge_cli(['--jobs',str(folder/'jobs.json'),'--scene',str(folder/'scene.json'),
                               '--mapping',str(folder/'mapping.json'),'--output-dir',str(folder/'result'),
                               '--max-checks','1' if name == 'analysis_budget' else '100'])
        require(code == (0 if expected == 'selected' else 1),(name,code,captured.getvalue()))
        report = json.loads((folder/'result/bridge_report.json').read_text(encoding='utf-8'))
        require(report['integration_status'] == expected,(name,report['integration_status']))
        if expected == 'selected':
            chosen = report['attempts'][report['selected_attempt']]
            require(chosen['rdf_validation']['ERROR'] == chosen['rdf_validation']['WARN'] == 0,'RDF errors')
            require(chosen['design_report']['owl_reasoning']['status'] == 'passed','OWL not executed')
            require(chosen['design_report']['network_search']['optimality'] == 'proven','fixed-tuple design optimum')
            require([len(t['generated_steps']) for t in chosen['compilation']['tasks']] == [3,4,4],'generated lengths')
            for task in chosen['design_report']['tasks']:
                for chain in task['chains']:
                    resources = {a['id']:a for a in chosen['design_input']['artifacts']}
                    effect = chain['plan']['steps'][-1]['artifact']
                    require(abs(chain['metrics']['accuracy']-.99*resources[effect]['activity_metrics']['act']['accuracy']) < 1e-12,'precision formula')
            require(len(report['attempts']) == (4 if name == 'fallback' else 1),'fallback attempt count')
        else:
            require(not list((folder/'result').rglob('design.ttl')),'failure exported selected RDF')
        results.append(dict(case=name,expected=expected,actual=report['integration_status'],passed=True,
                            attempts=len(report['attempts'])))
    summary = dict(verification='passed',output=str(output),cases=results)
    save(output/'verification.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
