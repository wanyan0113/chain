"""Opt-in convenience metadata, normalized into unchanged strict core contracts."""
from copy import deepcopy
import hashlib
import json

from .contracts import canonical_sha256, require

ORIGINS = ('synthetic_simulation', 'expert_assumption', 'real_observational', 'mixed')


class _Normalization:
    def __init__(self, original, origin, generator_version):
        require(origin in ORIGINS, 'minimal profile requires an explicit data origin')
        require(generator_version is None or (type(generator_version) is str and generator_version.strip()),
                'generator version must be nonempty text when supplied')
        self.original = deepcopy(original)
        self.digest = hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True,
                                                separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
        self.origin, self.generator = origin, generator_version
        self.attachments, self.bindings = [], []

    def refs(self, node, path):
        if isinstance(node, dict) and ('source_refs' not in node or node['source_refs'] == []):
            reference = 'input-snapshot:' + self.digest + '#' + path
            node['source_refs'] = [reference]
            self.attachments.append(dict(path=path, local_input_reference=reference,
                                         external_source_status='not_provided'))

    def rows(self, node, key, path):
        rows = node.get(key, []) if isinstance(node, dict) else []
        if isinstance(rows, list):
            for index, row in enumerate(rows):
                self.refs(row, path + '/' + key + '/' + str(index))

    def problem(self, catalog, request, path):
        require(type(catalog) is dict and type(request) is dict, 'catalog and request must be objects')
        # A supplied digest is a claim about the incoming catalog. Never repair
        # a stale/mismatched claim silently; only omission permits auto-binding.
        original_sha = canonical_sha256(catalog)
        if 'catalog_sha256' in request:
            require(request['catalog_sha256'] == original_sha, 'supplied catalog digest does not match input')
        self.refs(catalog, path + '/catalog')
        for key in ('facts', 'activities', 'derived_rules', 'capabilities'):
            self.rows(catalog, key, path + '/catalog')
        self.refs(request, path + '/request')
        normalized_sha = canonical_sha256(catalog)
        request['catalog_sha256'] = normalized_sha
        self.bindings.append(dict(path=path + '/request/catalog_sha256', original_catalog_sha256=original_sha,
                                  normalized_catalog_sha256=normalized_sha,
                                  reason='bind_to_catalog_after_optional_metadata_normalization'))
        # ontology_evidence source_ref / knowledge_sha256 / rule_ref are actual
        # evidence bindings, not optional descriptive references. Leave intact.

    def report(self):
        return dict(version='task-flow-input-profile-1.0', profile='minimal', data_origin=self.origin,
                    data_origin_is_self_declared=True, generator_version=self.generator,
                    input_snapshot=self.original, canonical_input_sha256=self.digest,
                    local_reference_attachments=self.attachments, computed_bindings=self.bindings,
                    local_references_are_external_evidence=False, external_sources_authenticated=False,
                    domain_validity='pending', numeric_defaults_inserted=False,
                    note='Local references locate submitted inputs only; no publication, measurement or probability evidence is inferred.')


def normalize_problem(catalog, request, *, data_origin, generator_version=None):
    n = _Normalization(dict(catalog=catalog, request=request), data_origin, generator_version)
    catalog, request = deepcopy(catalog), deepcopy(request)
    n.problem(catalog, request, '')
    return catalog, request, n.report()


def normalize_design(jobs, scene, mapping, *, data_origin, generator_version=None):
    require(type(jobs) is list and type(scene) is dict and type(mapping) is dict,
            'expected jobs array, scene object and mapping object')
    n = _Normalization(dict(jobs=jobs, scene=scene, mapping=mapping), data_origin, generator_version)
    jobs, scene, mapping = deepcopy(jobs), deepcopy(scene), deepcopy(mapping)
    for index, job in enumerate(jobs):
        require(type(job) is dict and set(job) == {'task_id', 'catalog', 'request'}, 'invalid job fields')
        n.problem(job['catalog'], job['request'], '/jobs/' + str(index))
    n.refs(mapping, '/mapping')
    if isinstance(mapping.get('evaluation'), dict):
        n.refs(mapping['evaluation'], '/mapping/evaluation')
    n.rows(mapping, 'entries', '/mapping')
    if isinstance(scene.get('knowledge'), dict):
        n.refs(scene['knowledge'], '/scene/knowledge')
    for key in ('tasks', 'artifacts', 'task_rules', 'artifact_rules', 'links', 'contexts', 'ontology_relations'):
        n.rows(scene, key, '/scene')
    return jobs, scene, mapping, n.report()


def normalize_metrics(source, registry, *, data_origin, generator_version=None):
    """Optional descriptive metric citations, never missing metric values/meaning."""
    from .team_evaluation import source_binding
    from .joint_identity import sha
    require(type(registry) is dict and registry.get('format_version') == 'task-flow-team-evaluation-1.1',
            'minimal metric profile requires explicit evaluation semantics 1.1')
    n = _Normalization(dict(metrics=registry, source_report_sha256=sha(source)), data_origin, generator_version)
    registry = deepcopy(registry)
    expected = source_binding(source)
    if 'binding' in registry:
        require(registry['binding'] == expected, 'supplied metric binding does not match source report')
    else:
        registry['binding'] = expected
        n.bindings.append(dict(path='/metrics/binding', reason='computed_from_supplied_source_report', value=expected))
    n.refs(registry, '/metrics')
    records = registry.get('records', [])
    if isinstance(records, list):
        for i, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            path = '/metrics/records/' + str(i)
            for key in ('quality', 'fusion'):
                if isinstance(record.get(key), dict):
                    n.refs(record[key], path + '/' + key)
            if isinstance(record.get('member_metrics'), dict):
                for member, metric in record['member_metrics'].items():
                    escaped = member.replace('~', '~0').replace('/', '~1')
                    n.refs(metric, path + '/member_metrics/' + escaped)
    return registry, n.report()
