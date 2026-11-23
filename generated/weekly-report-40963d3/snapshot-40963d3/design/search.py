"""Exact network search over scored candidates with additive objectives."""
from collections import Counter
import math


def branch_and_bound(groups, doc, conflict_check, *, cooperation_check=None):
    """Prune only monotone resource/precedence failures and optimistic bounds.

    The callback certifies monotone Table-10 prefix failures. Unknown
    prefixes must be expanded: a later known failure may settle them. Disable
    score bounds in this mode to audit even noncompetitive unknown completions.
    """
    from .engine import SearchLimitError
    groups = [sorted(group, key=lambda c: (-c['score'], c['id'])) for group in groups]
    maxima = [max(c['score'] for c in group) for group in groups]
    min_ids = [min(c['id'] for c in group) for group in groups]
    limit = doc['policies'].get('max_network_search_nodes', 1000000)
    stats = {'algorithm': 'branch_and_bound', 'candidate_combinations': math.prod(map(len, groups)),
             'counts_scope': 'visited_complete_candidates', 'evaluated': 0, 'feasible': 0,
             'nodes': 0, 'pruned_constraints': 0, 'pruned_bound': 0, 'rejections': [],
             'optimality': 'not_proven'}
    rejected = Counter()
    table10_reasons, table10_counts = {}, Counter()
    unknown_cooperation = False
    if cooperation_check is not None:
        stats.update(score_bound_enabled=False, pruned_table10=0,
                     audit_scope='visited_complete_combinations; definite-failure prefixes pruned')
    best = None
    chosen = []
    # An explicit stack avoids depending on Python's recursion depth.
    stack = [(0, iter(groups[0]))]
    while stack:
        depth, options = stack[-1]
        try:
            candidate = next(options)
        except StopIteration:
            stack.pop()
            if chosen:
                chosen.pop()
            continue
        stats['nodes'] += 1
        if stats['nodes'] > limit:
            raise SearchLimitError(f'网络分支定界搜索节点超过显式上限 {limit}；未证明最优，不返回完整设计')
        prefix = chosen + [candidate]
        complete = len(prefix) == len(groups)
        if complete:
            stats['evaluated'] += 1
        conflicts = conflict_check(prefix, doc)
        # Complete candidates retain both engineering and source-rule evidence.
        # Engineering-infeasible prefixes need no further source-rule checks.
        article = cooperation_check(prefix, doc) if cooperation_check is not None and (complete or not conflicts) else None
        if article is not None:
            if complete and not conflicts and article['status'] == 'unknown':
                unknown_cooperation = True
            if article['status'] == 'fail' or (complete and article['status'] == 'unknown'):
                conflicts.append({'code': 'TABLE10_' + article['status'].upper(), 'resource': 'parallel-groups'})
                if not complete:
                    stats['pruned_table10'] += 1
                    for check in article.get('checks', []):
                        table10_counts[check['id']] += 1
                        table10_reasons[check['id']] = {key: check[key] for key in ('id', 'quote', 'source')}
        if conflicts:
            stats['pruned_constraints'] += 1
            rejected.update((c['code'], c.get('resource', f"{c.get('before')}->{c.get('after')}")) for c in conflicts)
            continue
        if complete:
            stats['feasible'] += 1
        # Same ordered sum as the engine objective. Replacing remaining scores
        # by their maxima gives a monotone upper bound, including float rounding.
        upper = sum([c['score'] for c in prefix] + maxima[len(prefix):]) / len(groups)
        smallest_ids = tuple(c['id'] for c in prefix) + tuple(min_ids[len(prefix):])
        if cooperation_check is None and best is not None and (upper < -best[0][0] or
                                (upper == -best[0][0] and smallest_ids >= best[0][1])):
            stats['pruned_bound'] += 1
            continue
        if complete:
            key = (-upper, tuple(c['id'] for c in prefix))
            if best is None or key < best[0]:
                best = key, tuple(prefix), upper, article
        else:
            chosen.append(candidate)
            stack.append((depth + 1, iter(groups[depth + 1])))
    stats['rejections'] = [{'code': code, 'resource': resource, 'count': count}
                           for (code, resource), count in sorted(rejected.items())]
    stats['optimality'] = 'not_proven' if unknown_cooperation else 'proven' if best is not None else 'proven_infeasible'
    if cooperation_check is not None:
        stats['table10_pruning'] = [dict(table10_reasons[key], count=count)
                                   for key, count in sorted(table10_counts.items())]
    return best, stats
