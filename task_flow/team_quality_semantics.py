"""Meaning and applicability of team quality; no automatic probability claim."""
from .contracts import require
from .joint_identity import sha


def quality_context_hash(report):
    # Include task initial facts, goals and activity definitions as well as
    # equipment/knowledge/time context; a resource-only hash is insufficient.
    return sha(dict(source_input_hash=report['input_hash'], execution_context=report['context']))


def empty_quality_context(report):
    return dict(semantics='UNASSIGNED', definition='', evaluation_method='', population='',
                comparison_scale_id='', execution_conditions=dict(context_hash=quality_context_hash(report), description=''),
                probability_basis=None)


def validate_quality_context(report, record):
    q = record['quality']
    require(q['execution_conditions']['context_hash'] == quality_context_hash(report),
            'quality execution conditions no longer match source context')
    fields = ('definition', 'evaluation_method', 'population', 'comparison_scale_id')
    pending = q['semantics'] == 'UNASSIGNED' or any(not q[k].strip() for k in fields) or not q['execution_conditions']['description'].strip()
    basis = q['probability_basis']
    if q['semantics'] in ('UNASSIGNED', 'quality_score'):
        require(basis is None, 'general quality scores cannot carry a probability interpretation')
    elif basis is None:
        pending = True
    else:
        pending |= not basis['success_event'].strip() or not basis['dependence_assumptions'].strip() or not basis['evidence_refs']
        require(all(v.strip() for v in basis['evidence_refs']), 'empty probability evidence reference')
        if q['semantics'] == 'conditional_success_probability':
            pending |= not isinstance(basis['conditioning_event'], str) or not basis['conditioning_event'].strip()
        else:
            require(basis['conditioning_event'] is None, 'marginal probability must not silently become conditional')
    return pending


def interpretation(source, registry):
    """Presentation metadata only; arithmetic and candidate identity unchanged."""
    counts = {}
    for c in source['candidates'].values():
        counts.setdefault(c['task'], set()).add(len(c['plan']['steps']))
    semantics = sorted({r['quality'].get('semantics', 'UNASSIGNED') for r in registry['records']})
    return dict(version='team-quality-interpretation-1.0', metric_label='串行乘积质量指标',
                metric_key='accuracy', metric_key_is_legacy_scoring_slot=True,
                declared_stage_semantics=semantics, probability_interpretation='not_validated',
                actual_chain_success_probability_claimed=False, normalized_scores_are_probabilities=False,
                length_preference='Appending a factor in [0,1] cannot increase the product; correction benefits are not modeled.',
                stage_counts_by_task={t: sorted(v) for t, v in sorted(counts.items())},
                cross_length_domain_validity='pending', formula_changed=False)
