"""Optional isolated HermiT check of local RDF, without external imports."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from rdflib import Graph, OWL

ROOT = Path(__file__).resolve().parents[1]


def check_owl(graph, *, timeout=120):
    if any(graph.triples((None, OWL.imports, None))):
        return {'status': 'not_run', 'reason': '仅检查本地已合并图，不自动加载 owl:imports'}
    if not importlib.util.find_spec('owlready2') or not shutil.which('java'):
        return {'status': 'not_run', 'reason': '需要 requirements-reasoner.txt 依赖和Java'}
    (ROOT / 'tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
        source = Path(directory) / 'ontology.rdf'
        graph.serialize(source, format='xml')
        try:
            completed = subprocess.run([sys.executable, '-B', '-m', 'adapters.owl_check', '--worker', str(source)],
                                       cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                                       errors='replace', timeout=timeout)
            report = json.loads(completed.stdout)
        except subprocess.TimeoutExpired:
            return {'status': 'not_run', 'reason': 'HermiT检查超时'}
        except ValueError:
            return {'status': 'not_run', 'reason': 'HermiT未返回有效报告', 'diagnostic': completed.stderr[-2000:]}
        return report


def worker(source):
    import owlready2
    try:
        world = owlready2.World()
        with source.open('rb') as handle:
            world.get_ontology('urn:local-owl-check:').load(fileobj=handle)
        owlready2.sync_reasoner(world, debug=0)
        unsatisfiable = sorted(str(c.iri) for c in world.inconsistent_classes() if c != owlready2.Nothing)
        return {'status': 'failed' if unsatisfiable else 'passed', 'engine': 'HermiT',
                'ontology_consistent': True, 'unsatisfiable_classes': unsatisfiable,
                'scope': 'OWL一致性与不可满足类检查；不证明来源事实或业务规则正确'}
    except owlready2.OwlReadyInconsistentOntologyError:
        return {'status': 'failed', 'engine': 'HermiT', 'ontology_consistent': False}
    except Exception as exc:
        return {'status': 'not_run', 'engine': 'HermiT', 'reason': str(exc)}


def main():
    parser = argparse.ArgumentParser(description='本地本体与实例HermiT一致性检查')
    parser.add_argument('inputs', nargs='*', type=Path)
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.worker:
        report = worker(args.worker)
    else:
        if not args.inputs:
            parser.error('至少提供一个本地TTL文件')
        graph = Graph()
        for path in args.inputs:
            graph.parse(path, format='turtle')
        report = check_owl(graph)
        if args.report:
            from source_data.common import write_outputs
            write_outputs(args.report.parent, {args.report.name: json.dumps(report, ensure_ascii=False, indent=2) + '\n'}, protected=args.inputs)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1 if report['status'] == 'failed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
