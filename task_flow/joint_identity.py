"""Versioned identities shared by planning, candidates, evaluation and evidence."""
from copy import deepcopy
import hashlib
import json
import math
import unicodedata
import uuid

from .contracts import require

IDENTITY_VERSION = 'tfdo-identity-1.0'
BASELINE = 'TFDO-DESIGN-1.0'


def canonical(value):
    """NFC, sorted object keys, exact integers, finite float shortest JSON form.

    Semantic arrays retain order. Set-like contract fields are sorted by their
    projections below, never by a global sort-all-arrays heuristic.
    """
    def normalize(x):
        if type(x) is dict:
            result = {}
            for key, item in x.items():
                require(type(key) is str, 'identity object keys must be strings')
                key = unicodedata.normalize('NFC',key)
                require(key not in result, 'identity normalization key collision')
                result[key] = normalize(item)
            return result
        if type(x) is list:
            return [normalize(v) for v in x]
        if type(x) is str:
            return unicodedata.normalize('NFC',x)
        if type(x) is float:
            require(math.isfinite(x), 'identity does not accept nonfinite numbers')
            return int(x) if x.is_integer() else x
        require(x is None or type(x) in (int,bool), 'identity expects JSON values')
        return x
    return json.dumps(normalize(value),ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def require_nfc_source(value):
    """Match the planning JSON contract before exact-string business evaluation.

    Hash normalization must not merge source strings the business engine would
    distinguish. Reject non-NFC source input instead of silently rewriting it.
    """
    if isinstance(value,str):
        require(unicodedata.normalize('NFC',value)==value,'source strings must be NFC normalized')
    elif isinstance(value,dict):
        for k,v in value.items():
            require_nfc_source(k);require_nfc_source(v)
    elif isinstance(value,list):
        for v in value:require_nfc_source(v)


def ordered_set(values):
    return sorted(deepcopy(values),key=canonical)


def record(value):
    # Remove only provenance/display metadata of a declared record. Attributes
    # remain intact: a business rule can legitimately reference a name field.
    result = {k:deepcopy(v) for k,v in value.items()
              if k not in ('source_refs','display_name','label','display_id')}
    for field in ('blocking_issue_ids','ontology_types','target_ontology_types'):
        if field in result:
            result[field]=ordered_set(result[field])
    return result


def activity_semantics(catalog, sequence):
    used = {s['activity_id'] for s in sequence}
    activities = []
    for action in catalog['activities']:
        if action['id'] not in used:
            continue
        item = record(action)
        item['required_capabilities'] = ordered_set(item['required_capabilities'])
        activities.append(item)
    return dict(activities=ordered_set(activities),facts=ordered_set([record(f) for f in catalog['facts']]),
                derived_rules=ordered_set([record(r) for r in catalog['derived_rules']]),
                objects={k:ordered_set(v) for k,v in catalog['objects'].items()})


def flow_identity(job, candidate, mapping):
    sequence = deepcopy(candidate['sequence']['steps'])
    targets = []
    entries = {(e['catalog_id'],e['activity_id'],e['activity_version']):e for e in mapping['entries']}
    for step in sequence:
        entry = entries.get((job['catalog']['catalog_id'],step['activity_id'],step['activity_version']))
        targets.append(None if entry is None else entry['target_parameter'])
    payload = dict(identity_version=IDENTITY_VERSION,catalog_id=job['catalog']['catalog_id'],
                   catalog_version=job['catalog']['version'],activity_instance_sequence=sequence,
                   target_parameters=targets,activity_semantics=activity_semantics(job['catalog'],sequence))
    return 'flow-'+sha(payload),payload


def task_context(task, job):
    task = record(task)
    if 'ontology_types' in task:
        task['ontology_types'] = ordered_set(task['ontology_types'])
    if 'target_ontology_types' in task:
        task['target_ontology_types'] = ordered_set(task['target_ontology_types'])
    request = job['request']
    projected = {k:deepcopy(request[k]) for k in
                 ('static_facts','initial_state','ontology_evidence','goal','required_activities',
                  'forbidden_conditions','activity_limits')}
    for k in ('static_facts','initial_state','ontology_evidence','required_activities','forbidden_conditions'):
        projected[k] = ordered_set(projected[k])
    return sha(dict(task=task,request=projected))


def design_context(scene):
    projection = {}
    for key in ('artifacts','contexts','links','resource_groups','dependencies','ontology_relations',
                'task_rules','artifact_rules'):
        values = []
        for obj in scene.get(key,[]):
            item = record(obj)
            for field in ('ontology_types','artifacts'):
                if field in item:
                    item[field] = ordered_set(item[field])
            values.append(item)
        projection[key] = ordered_set(values)
    projection['tasks'] = ordered_set([record(t) for t in scene['tasks']])
    # Execution constraints, not budgets, equal-score handling or objective weights.
    projection['policies'] = {k:v for k,v in scene['policies'].items()
                             if k in ('cost_unit','article_table10')}
    return sha(projection)


def mapping_context(mapping):
    entries = [record(e) for e in mapping['entries']]
    return sha(dict(format_version=mapping['format_version'],version=mapping['version'],entries=ordered_set(entries),
                    communication_model=mapping['communication_model'],
                    allow_nonadjacent_reuse=mapping['allow_nonadjacent_reuse']))


def knowledge_context_hash(metadata):
    return sha(ordered_set([{'path':f['path'],'sha256':f['sha256']} for f in metadata['files']
                           if not f['path'].startswith('inline:')]))


def candidate_identity(task_id, task_hash, flow_id, sequence, chain, context_hash, knowledge_hash, mapping_hash):
    payload = dict(identity_version=IDENTITY_VERSION,task_id=task_id,task_context_hash=task_hash,flow_id=flow_id,
                   activity_instance_sequence=deepcopy(sequence),
                   artifact_assignment=[dict(step_index=i,artifact_id=s['artifact'])
                                        for i,s in enumerate(chain['plan']['steps'])],
                   design_context_hash=context_hash,knowledge_snapshot_hash=knowledge_hash,mapping_hash=mapping_hash)
    return 'candidate-'+sha(payload),payload


def new_run_id():
    return 'run-'+uuid.uuid4().hex


def evidence(run_id,candidate_id,kind,ordinal,content):
    payload = dict(run_id=run_id,candidate_id=candidate_id,kind=kind,ordinal=ordinal,
                   analyzer_version='tfdo-joint-1.0',content_sha256=sha(content))
    return dict(candidate_evidence_id='evidence-'+sha(payload),**payload,content=deepcopy(content))
