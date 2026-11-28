"""Run real HermiT in an isolated process and return named entailments."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager

from rdflib import RDF, OWL, URIRef

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _stage(observer,name):
    if observer:observer('start',name)
    try:yield
    finally:
        if observer:observer('end',name)


def infer_owl(graph, subjects, predicates, *, timeout=120, observer=None):
    if any(graph.triples((None, OWL.imports, None))):
        return {'status': 'not_run', 'engine': 'HermiT', 'reason': '不加载 owl:imports，请显式配置本地知识文件'}
    if not importlib.util.find_spec('owlready2') or not shutil.which('java'):
        return {'status': 'not_run', 'engine': 'HermiT', 'reason': '需要owlready2和Java'}
    (ROOT / 'tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
        directory = Path(directory)
        with _stage(observer,'adapter_input_serialization'):
            graph.serialize(directory / 'input.rdf', format='xml')
            (directory / 'query.json').write_text(json.dumps({'subjects': subjects, 'predicates': predicates}), encoding='utf-8')
        try:
            with _stage(observer,'python_worker_launch_and_wait'):
                result = subprocess.run([sys.executable, '-B', '-m', 'adapters.owl_infer', str(directory)],
                                        cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                        timeout=timeout)
            with _stage(observer,'adapter_result_json_decode'):
                report = json.loads(result.stdout)
            if result.returncode:
                return {'status': 'not_run', 'engine': 'HermiT', 'reason': '推理进程异常退出'}
            return report
        except subprocess.TimeoutExpired:
            return {'status': 'not_run', 'engine': 'HermiT', 'reason': 'OWL推理超时'}
        except (OSError, ValueError) as exc:
            return {'status': 'not_run', 'engine': 'HermiT', 'reason': str(exc)}


def worker(directory, *, observer=None):
    with _stage(observer,'python_owlready_import'):
        import owlready2
    try:
        with _stage(observer,'python_query_input_read'):
            query = json.loads((directory / 'query.json').read_text(encoding='utf-8'))
        with _stage(observer,'python_world_create'):
            world = owlready2.World()
        with _stage(observer,'python_ontology_load'):
            with (directory / 'input.rdf').open('rb') as handle:
                world.get_ontology('urn:arcane-owl-input:').load(fileobj=handle)
        with _stage(observer,'owlready_sync_inclusive'):
            owlready2.sync_reasoner(world, infer_property_values=True, debug=0)
        with _stage(observer,'python_unsatisfiable_check'):
            unsatisfiable = sorted(c.iri for c in world.inconsistent_classes() if c != owlready2.Nothing)
        if unsatisfiable:
            return {'status': 'failed', 'engine': 'HermiT', 'reason': '存在不可满足类', 'unsatisfiable_classes': unsatisfiable}
        with _stage(observer,'python_query_and_result_conversion'):
            graph = world.as_rdflib_graph()
            types, facts = {}, set()
            for subject in query['subjects']:
                entity = world[subject]
                types[subject] = sorted({value.iri for value in entity.INDIRECT_is_a
                                         if isinstance(value, owlready2.ThingClass) and value != owlready2.Thing}) if entity is not None else []
                for predicate in query['predicates']:
                    for object_ in sorted(graph.objects(URIRef(subject), URIRef(predicate)), key=str):
                        if isinstance(object_, URIRef):
                            facts.add((subject, predicate, str(object_)))
        return {'status': 'passed', 'engine': 'HermiT', 'owlready2_version': owlready2.VERSION,
                'ontology_consistent': True, 'types': types, 'facts': sorted(facts)}
    except owlready2.OwlReadyInconsistentOntologyError:
        return {'status': 'failed', 'engine': 'HermiT', 'reason': 'OWL知识与实例不一致', 'ontology_consistent': False}
    except Exception as exc:
        return {'status': 'not_run', 'engine': 'HermiT', 'reason': str(exc)}


if __name__ == '__main__':
    print(json.dumps(worker(Path(sys.argv[1])), ensure_ascii=True))
