"""Independent descriptive references for team statistical-semantic review.

Standard library only. No production scores, inference, optimization, probability
calibration or causal-effect certification are calculated here.
"""
import math


def group_levels(rows, members):
    if not rows or not members or len(set(members)) != len(members):
        raise ValueError('nonempty samples and unique declared members required')
    seen, member_correct, group_correct, endpoint_correct = set(), {m: 0 for m in members}, 0, 0
    recovery = 0
    for r in rows:
        if r['sample_id'] in seen or set(r['member_outputs']) != set(members):
            raise ValueError('duplicate sample or changed member roster')
        seen.add(r['sample_id'])
        outcomes = []
        for m in members:
            actual, reference = r['member_outputs'][m]['actual'], r['member_outputs'][m]['reference']
            if actual is None or reference is None:
                raise ValueError('missing member output/reference cannot become a failure label')
            good = actual == reference
            member_correct[m] += good
            outcomes.append(good)
        for level in ('group_output', 'task_endpoint'):
            if r[level]['actual'] is None or r[level]['reference'] is None:
                raise ValueError('independent group/endpoint output and reference required')
        group_good = r['group_output']['actual'] == r['group_output']['reference']
        group_correct += group_good
        endpoint_correct += r['task_endpoint']['actual'] == r['task_endpoint']['reference']
        recovery += group_good and not all(outcomes)
    n = len(rows)
    rates = {m: count/n for m, count in member_correct.items()}
    return dict(n=n, member_counts=member_correct, member_rates=rates,
                member_rate_product=math.prod(rates.values()), group_correct=group_correct,
                group_rate=group_correct/n, endpoint_correct=endpoint_correct, endpoint_rate=endpoint_correct/n,
                group_correct_despite_member_failure=recovery,
                member_scores_used_as_group_truth=False, causal_correction_proven=False)


def serial_events(rows, stages):
    if not rows or not stages or len(set(stages)) != len(stages):
        raise ValueError('nonempty samples and unique ordered stage instances required')
    seen, marginals, conditional, joint, endpoints, mismatch = set(), [], [], 0, 0, 0
    for r in rows:
        if r['sample_id'] in seen or set(r['events']) != set(stages):
            raise ValueError('duplicate sample or changed stage instances')
        if any(type(r['events'][s]) is not bool for s in stages) or type(r['endpoint']) is not bool:
            raise ValueError('unknown labels are not Boolean failure events')
        seen.add(r['sample_id'])
        all_good = all(r['events'][s] for s in stages)
        joint += all_good
        endpoints += r['endpoint']
        mismatch += all_good != r['endpoint']
    for i, stage in enumerate(stages):
        correct = sum(r['events'][stage] for r in rows)
        eligible = [r for r in rows if all(r['events'][prior] for prior in stages[:i])]
        successes = sum(r['events'][stage] for r in eligible)
        marginals.append(dict(stage_instance_id=stage, numerator=correct, denominator=len(rows), value=correct/len(rows)))
        conditional.append(dict(stage_instance_id=stage, conditioned_on=stages[:i], numerator=successes,
                                denominator=len(eligible), value=successes/len(eligible) if eligible else None))
    values = [c['value'] for c in conditional]
    return dict(n=len(rows), marginals=marginals, conditionals=conditional,
                marginal_product=math.prod(c['value'] for c in marginals),
                conditional_product=math.prod(values) if all(v is not None for v in values) else None,
                joint_event_rate=joint/len(rows), endpoint_rate=endpoints/len(rows),
                endpoint_joint_disagreements=mismatch, zero_support_stages=[c['stage_instance_id'] for c in conditional if not c['denominator']],
                independence_tested=False, endpoint_equivalence_proven=False)


def paired_endpoints(enrollments, short_rows, long_rows):
    """Keep the enrolled denominator and report incomplete pairs explicitly."""
    enrolled = {}
    for r in enrollments:
        if r['pair_id'] in enrolled or any(e['sample_id'] == r['sample_id'] for e in enrolled.values()):
            raise ValueError('duplicate enrolled pair/sample')
        enrolled[r['pair_id']] = r
    if not enrolled:
        raise ValueError('explicit enrolled cohort required')
    arms = []
    for rows in (short_rows, long_rows):
        arm = {}
        for r in rows:
            if r['pair_id'] in arm or r['pair_id'] not in enrolled:
                raise ValueError('duplicate or unregistered arm record')
            target = enrolled[r['pair_id']]
            if any(r[k] != target[k] for k in ('sample_id', 'population')):
                raise ValueError('paired comparison changed sample/population')
            if r['success'] is not None and type(r['success']) is not bool:
                raise ValueError('invalid endpoint label')
            if r['success'] is None and not r.get('missing_reason'):
                raise ValueError('unknown outcome requires missing reason')
            arm[r['pair_id']] = r
        arms.append(arm)
    complete, missing = [], []
    for pair in enrolled:
        if all(pair in arm and arm[pair]['success'] is not None for arm in arms):
            complete.append(pair)
        else:
            missing.append(pair)
    n = len(complete)
    short_success = sum(arms[0][p]['success'] for p in complete)
    long_success = sum(arms[1][p]['success'] for p in complete)
    return dict(n_enrolled=len(enrolled), n_complete_pairs=n, missing_pairs=missing,
                short_correct_in_complete_pairs=short_success, long_correct_in_complete_pairs=long_success,
                complete_pair_rate_difference=(long_success-short_success)/n if n else None,
                pair_populations=sorted({r['population'] for r in enrolled.values()}),
                coverage_complete=not missing, causal_effect_proven=False)
