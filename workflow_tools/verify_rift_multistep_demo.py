"""Run the real OWL-to-RDF workflow and independently audit the demo."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
from fractions import Fraction
from itertools import product
import io
import json
import math
from pathlib import Path
import uuid

from design.__main__ import main as design_cli
from design.owl_scene import scene_uri

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT/'design/examples/rift_multistep_demo.json'
NS = 'urn:arcane-rift-demo:'
BASE = 'http://example.org/arcaneweb#'


def require(value, message):
    if not value:
        raise RuntimeError(message)


def run_case(doc, folder, expected):
    folder.mkdir(parents=True)
    input_path = folder/'input.json'
    input_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    captured = io.StringIO()
    with redirect_stdout(captured):
        code = design_cli(['run', str(input_path), '--output-dir', str(folder), '--require-canonical'])
    report = json.loads(captured.getvalue())
    require(report['status'] == expected, (folder.name, report))
    require(code == (0 if expected == 'complete' else 1), f'{folder.name}: unexpected exit code')
    require(report['owl_reasoning']['status'] == 'passed', f'{folder.name}: actual OWL not run')
    require((folder/'design.ttl').is_file() == (expected == 'complete'), f'{folder.name}: wrong RDF export')
    if expected == 'complete':
        require(report['canonical_export_ready'], 'canonical export not ready')
        require(report['rdf_validation']['ERROR'] == report['rdf_validation']['WARN'] == 0, 'RDF validation failed')
        require(report['network_search']['optimality'] == 'proven', 'optimality not proven')
    else:
        require(report['network'] is None, f'{folder.name}: invalid selected network')
    return report


def audit_baseline(report):
    # Independent, hand-derived per-task scores; do not invoke the scorer or
    # resource checker to determine expected optimum or feasibility.
    scores = [dict(A=Fraction(3,10), B=Fraction(61,105), C=Fraction(29,35), D=Fraction(1,2)),
              dict(B=Fraction(3,10), C=Fraction(22,25), D=Fraction(17,60)),
              dict(C=Fraction(1), D=Fraction(0))]
    combinations = list(product(*scores))
    feasible = [p for p in combinations if len(set(p)) == 3]
    ranked = sorted(((sum(scores[i][a] for i,a in enumerate(p))/3, p) for p in feasible), reverse=True)
    require(len(combinations) == 24 and len(feasible) == 8, 'independent enumeration count')
    require(ranked[0] == (Fraction(87,140), ('B','D','C')) and ranked[0][0] > ranked[1][0], 'independent optimum')
    facts = {tuple(f) for f in report['owl_reasoning']['facts']}
    definitions = [('WideSurvey','Dispatch','Stabilize'),
                   ('WideSurvey','ResonanceAnalysis','Dispatch','Stabilize'),
                   ('WideSurvey','ResonanceAnalysis','FineScan','Dispatch','Stabilize')]
    raw = dict(A=(2,.90,9), B=(4,.95,6), C=(6,.99,3), D=(9,.97,5))
    prefix = [(3,.99*.99,2), (5,.99*.99*.99,3), (8,.99*.99*.98*.99,5)]
    selected = []
    for i, task in enumerate(report['tasks']):
        require(len(task['chains']) == len(scores[i]) and not task['blockers'], 'candidate coverage')
        task_node = str(scene_uri('task', f'T{i+1}'))
        require(NS+f'Mission{i+1}' in report['owl_reasoning']['types'][task_node], 'OWL task classification')
        for cap in definitions[i]:
            require((task_node, BASE+'requiresCapability', NS+cap) in facts, 'OWL mission capability entailment')
        for chain in task['chains']:
            steps = chain['plan']['steps']
            require([s['id'] for s in steps] == list(definitions[i]), 'step order')
            letter = steps[-1]['artifact'][-1]
            dt, accuracy, cost = raw[letter]
            expected = (prefix[i][0]+dt, prefix[i][1]*accuracy, prefix[i][2]+cost)
            actual = tuple(chain['metrics'][m] for m in ('time','accuracy','cost'))
            require(all(math.isclose(a,b,abs_tol=1e-12) for a,b in zip(actual, expected)), 'independent metric calculation')
            require(math.isclose(chain['score'], float(scores[i][letter]), abs_tol=1e-12), 'independent score')
            require(all(a['end_s'] == b['start_s'] for a,b in zip(steps,steps[1:])), 'step timing continuity')
            if chain['id'] in report['network']['chains']:
                selected.append(letter)
    require(tuple(selected) == ranked[0][1], 'selected network differs from independent optimum')
    require(math.isclose(report['network']['score'], float(ranked[0][0]), abs_tol=1e-12), 'network score')
    require(report['network']['makespan_s'] == 14 and report['network']['cost_per_execution'] == 24, 'network totals')
    require(report['article_table10']['selected']['status'] == 'pass', 'selected table10 cooperation')
    return dict(raw_combinations=24, feasible_combinations=8, selected=selected,
                exact_score='87/140', unique_optimum=True)


def main():
    doc = json.loads(INPUT.read_text(encoding='utf-8'))
    output = ROOT/'generated/rift_multistep_demo'/('verified-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    results = []
    baseline = run_case(doc, output/'baseline', 'complete')
    oracle = audit_baseline(baseline)
    results.append(dict(case='baseline', status=baseline['status'], passed=True))
    exhaustive = deepcopy(doc)
    exhaustive['policies']['network_search_method'] = 'exhaustive'
    report = run_case(exhaustive, output/'exhaustive', 'complete')
    audit_baseline(report)
    require(report['network'] == baseline['network'], 'solver disagreement')
    require(report['network_search']['evaluated'] == 24 and report['network_search']['feasible'] == 8, 'exhaustive count')
    results.append(dict(case='exhaustive', status=report['status'], passed=True))

    for name, expected in [('review_unavailable','no_feasible_design'), ('review_link_broken','no_feasible_design'),
                           ('shared_tracks_equal_total','no_feasible_design'), ('missing_review_range','blocked'),
                           ('long_range_stock_empty','no_feasible_design')]:
        variant = deepcopy(doc)
        artifacts = {a['id']:a for a in variant['artifacts']}
        if name == 'review_unavailable':
            artifacts['R-Review']['available'] = False
        elif name == 'review_link_broken':
            next(e for e in variant['links'] if (e['from'],e['to']) == ('R-Analysis','R-Review'))['communicates'] = False
        elif name == 'shared_tracks_equal_total':
            artifacts['R-Survey']['attributes']['max_tracks']['value'] = 3
        elif name == 'missing_review_range':
            del artifacts['R-Review']['attributes']['detection_range']
        else:
            for identifier in ('R-Seal-C','R-Seal-D'):
                artifacts[identifier]['inventory'] = 0
        report = run_case(variant, output/name, expected)
        results.append(dict(case=name, status=report['status'], passed=True))
    summary = dict(verification='passed', input=str(INPUT), output=str(output),
                   independent_oracle=oracle, cases=results)
    (output/'verification.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
