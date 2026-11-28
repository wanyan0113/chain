"""Resource-constrained joint selection over a frozen, uniformly scored pool."""
from collections import Counter
from copy import deepcopy
from itertools import combinations, product
import math

from design.engine import network_conflicts
from design.plans import member_ids
from design.search import branch_and_bound
from design.article_integration import cooperation, cooperation_prefix
from design.sequences import SEQUENCES


def optimize(groups,scene,mapping):
    if any(not g for g in groups):
        return None,dict(algorithm='not_run',optimality='proven_infeasible',search_complete=True,
                         termination_reason='empty_task_pool',evaluated=0,nodes=0)
    token = SEQUENCES.set({'communication_model':mapping['communication_model']})
    try:
        table = scene['policies'].get('article_table10',False)
        def check(prefix,doc):
            return cooperation(prefix,doc) if len(prefix)==len(groups) else cooperation_prefix(prefix,doc)
        method = scene['policies'].get('network_search_method','auto')
        total = math.prod(map(len,groups))
        if method == 'branch_and_bound' or (method == 'auto' and total > scene['policies']['max_network_combinations']):
            return branch_and_bound(groups,scene,network_conflicts,cooperation_check=check if table else None,
                                    allow_incomplete=True)
        best, unknown, rejected = None,False,Counter()
        stats = dict(algorithm='exhaustive',candidate_combinations=total,evaluated=0,feasible=0,nodes=0,
                     search_complete=True,termination_reason='search_exhausted',counts_scope='visited_complete_candidates')
        limit = min(scene['policies']['max_network_combinations'],scene['policies'].get('max_network_search_nodes',1000000))
        for chosen in product(*[sorted(g,key=lambda c:c['id']) for g in groups]):
            if stats['evaluated'] >= limit:
                stats.update(search_complete=False,termination_reason='combination_limit')
                break
            stats['evaluated'] += 1
            stats['nodes'] += 1
            conflicts = network_conflicts(chosen,scene)
            article = cooperation(chosen,scene) if table else None
            if not conflicts and article and article['status'] == 'unknown':
                unknown = True
            if article and article['status'] != 'pass':
                conflicts.append(dict(code='TABLE10_'+article['status'].upper(),resource='parallel-groups'))
            if conflicts:
                rejected.update(c['code'] for c in conflicts)
                continue
            stats['feasible'] += 1
            score = sum(c['score'] for c in chosen)/len(chosen)
            key = (-score,tuple(c['id'] for c in chosen))
            if best is None or key < best[0]:
                best = key,tuple(chosen),score,article
        stats['optimality'] = ('not_proven' if unknown or not stats['search_complete'] else
                               'proven' if best is not None else 'proven_infeasible')
        stats['unknown_combinations_seen'] = unknown
        stats['rejections'] = dict(rejected)
        return best,stats
    finally:
        SEQUENCES.reset(token)


def network(chosen,score,scene):
    dependencies = {(d['before'],d['after']) for d in scene.get('dependencies',[])}
    relations = []
    for a,b in combinations(chosen,2):
        if (a['task'],b['task']) in dependencies or (b['task'],a['task']) in dependencies:
            first,last = (a,b) if (a['task'],b['task']) in dependencies else (b,a)
            relations.append(dict(kind='serial',**{'from':first['id'],'to':last['id']}))
        elif max(a['start_s'],b['start_s']) < min(a['end_s'],b['end_s']):
            relations.append(dict(kind='parallel',chains=[a['id'],b['id']]))
        shared = sorted(member_ids(a)&member_ids(b))
        if shared:
            relations.append(dict(kind='resource_sharing',chains=[a['id'],b['id']],artifacts=shared))
    by_task = {c['task']:c for c in chosen}
    for task in by_task:
        successors = sorted(b for a,b in dependencies if a==task)
        if len(successors)>1:
            relations.append(dict(kind='branch',**{'from':by_task[task]['id'],'to':[by_task[t]['id'] for t in successors]}))
    return dict(id='joint-web-1',chains=[c['id'] for c in chosen],score=score,
                objective='mean_of_per_task_normalized_chain_scores',relations=relations,
                makespan_s=max(c['end_s'] for c in chosen)-min(c['start_s'] for c in chosen),
                cost_per_execution=sum(c['metrics']['cost'] for c in chosen),cost_unit=scene['policies']['cost_unit'])
