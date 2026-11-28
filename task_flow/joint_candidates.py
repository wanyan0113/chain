"""Deduplicate, freeze, then evaluate candidates. No network decisions here."""
from copy import deepcopy
import math

from .contracts import require
from .joint_identity import canonical, evidence, sha

RAW_POLICY = 'sequential_product_v2'
NORMALIZATION_POLICY = 'per_task_frozen_minmax_float64_v1'
NETWORK_POLICY = 'mean_of_per_task_normalized_chain_scores_v1'


def metrics_for_chain(chain, scene):
    resources = {a['id']:a for a in scene['artifacts']}
    metrics = []
    for step in chain['plan']['steps']:
        value = resources[step['artifact']].get('activity_metrics',{}).get(step['activity'])
        require(type(value) is dict,'missing raw activity metrics')
        require(value['cost']['unit'] == scene['policies']['cost_unit'],'inconsistent cost unit')
        require(all(type(value[k]) in (int,float) and math.isfinite(value[k]) for k in ('duration_s','accuracy')),
                'invalid activity metric')
        require(value['duration_s'] > 0 and 0 <= value['accuracy'] <= 1,'invalid activity metric range')
        require(type(value['cost']['value']) in (int,float) and math.isfinite(value['cost']['value'])
                and value['cost']['value'] >= 0,'invalid cost value')
        metrics.append(value)
    result = dict(time=sum(m['duration_s'] for m in metrics),accuracy=math.prod(m['accuracy'] for m in metrics),
                  cost=sum(m['cost']['value'] for m in metrics),cost_unit=scene['policies']['cost_unit'])
    require(all(math.isfinite(result[k]) for k in ('time','accuracy','cost')),'raw metric overflow')
    return result


class CandidateStore:
    def __init__(self,run_id):
        self.run_id = run_id
        self.candidates = {}
        self.evidence_records = []
        self.generated_count = 0
        self._frozen = False

    def add(self,candidate_id,payload,chain,origin):
        require(not self._frozen,'candidate set already frozen')
        require(candidate_id=='candidate-'+sha(payload),'candidate ID does not match structured payload')
        self.generated_count += 1
        content = dict(origin=deepcopy(origin),chain=deepcopy(chain))
        rec = evidence(self.run_id,candidate_id,'chain_generation_and_verification',len(self.evidence_records),content)
        old = self.candidates.get(candidate_id)
        if old is not None:
            require(canonical(old['identity_payload']) == canonical(payload),'candidate identity collision')
            require(canonical(old['chain']['metrics']) == canonical(chain['metrics']),
                    'same candidate and raw policy has inconsistent metrics')
            old['candidate_evidence_ids'].append(rec['candidate_evidence_id'])
        else:
            candidate = deepcopy(chain)
            candidate['id'] = candidate_id
            candidate.pop('normalized',None)
            candidate['score'] = None
            self.candidates[candidate_id] = dict(candidate_id=candidate_id,identity_payload=deepcopy(payload),
                flow_id=payload['flow_id'],task_id=payload['task_id'],chain=candidate,
                candidate_evidence_ids=[rec['candidate_evidence_id']])
        self.evidence_records.append(rec)

    def freeze(self,task_ids,coverage):
        self._frozen = True
        sets = []
        for task in task_ids:
            ids = sorted(k for k,c in self.candidates.items() if c['task_id'] == task)
            payload = dict(task_id=task,candidate_ids=ids,raw_metric_policy=RAW_POLICY,
                           raw_metrics=[self.candidates[k]['chain']['metrics'] for k in ids])
            sets.append(dict(**payload,candidate_set_hash=sha(payload)))
        return dict(version='tfdo-candidate-set-1.0',raw_metric_policy=RAW_POLICY,
                    sets=sets,coverage=deepcopy(coverage),
                    candidate_set_hash=sha([{'task_id':s['task_id'],'hash':s['candidate_set_hash']} for s in sets]))


def evaluate(snapshot,candidates,weights):
    require(set(weights) == {'time','accuracy','cost'} and
            all(type(v) in (int,float) and math.isfinite(v) and 0 <= v <= 1 for v in weights.values()) and
            math.isclose(sum(weights.values()),1,rel_tol=0,abs_tol=1e-9),'invalid scoring weights')
    units={c['chain']['metrics'].get('cost_unit') for c in candidates.values()
           if isinstance(c['chain']['metrics'],dict)}
    require(len(units)==1 and all(type(u) is str and u for u in units),'missing or inconsistent candidate cost unit')
    results,extrema = {},{}
    for pool in snapshot['sets']:
        ids = pool['candidate_ids']
        require(bool(ids),'task has no candidates; do not remove it from the objective')
        actual = dict(task_id=pool['task_id'],candidate_ids=ids,raw_metric_policy=RAW_POLICY,
                      raw_metrics=[candidates[k]['chain']['metrics'] for k in ids])
        require(sha(actual) == pool['candidate_set_hash'],'frozen candidate set changed')
        bounds = {}
        for key in ('time','accuracy','cost'):
            values = []
            for identifier in ids:
                metric = candidates[identifier]['chain']['metrics']
                require(type(metric) is dict and key in metric,'missing metric; cannot assign neutral score')
                value = metric[key]
                require(type(value) in (int,float) and math.isfinite(value),'invalid raw metric')
                values.append(value)
            bounds[key] = dict(min=min(values),max=max(values),degenerate=min(values)==max(values))
        extrema[pool['task_id']] = bounds
        for identifier in ids:
            normalized = {}
            for key,bound in bounds.items():
                raw = candidates[identifier]['chain']['metrics'][key]
                value = .5 if bound['degenerate'] else (raw-bound['min'])/(bound['max']-bound['min'])
                if not bound['degenerate'] and key != 'accuracy':
                    value = 1-value
                normalized[key] = value
            results[identifier] = dict(normalized=normalized,score=sum(weights[k]*v for k,v in normalized.items()))
    context = dict(raw_metric_policy=RAW_POLICY,normalization_policy=NORMALIZATION_POLICY,equal_metric_score=.5,
                   arithmetic='finite_float64_exact_equality',candidate_set_hash=snapshot['candidate_set_hash'],
                   normalization_scope='per_task_union_of_locally_feasible_cross_flow_chains',
                   normalization_extrema=extrema,chain_weights=deepcopy(weights),network_objective_policy=NETWORK_POLICY)
    return dict(**context,evaluation_context_hash=sha(context),candidates=results,
                model_validation=dict(implementation='formula_checked',domain_validity='pending'))
