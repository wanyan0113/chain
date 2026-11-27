"""Generation-side three-valued state semantics; no planning or independent verifier."""
from copy import deepcopy
import operator

from .contracts import ContractError, check_value, require, resolve_fact, validate_instance

COMPARISONS = {'eq': operator.eq, 'ne': operator.ne, 'lt': operator.lt,
               'le': operator.le, 'gt': operator.gt, 'ge': operator.ge}


class TransitionError(ValueError):
    def __init__(self, reason, *, status='rejected'):
        self.status = status
        self.reason = reason
        super().__init__(reason)


def evaluate_condition(problem, condition, state, parameters=None):
    """Return exactly True, False or None (unknown); conditions must be compiled."""
    if 'all' in condition or 'any' in condition:
        is_all = 'all' in condition
        values = [evaluate_condition(problem, child, state, parameters)
                  for child in condition['all' if is_all else 'any']]
        if is_all:
            return False if False in values else None if None in values else True
        return True if True in values else None if None in values else False
    if 'not' in condition:
        value = evaluate_condition(problem, condition['not'], state, parameters)
        return None if value is None else not value
    test = condition['test']
    value = state.get(resolve_fact(problem, test['fact'], parameters))
    return None if value is None else COMPARISONS[test['op']](value, test['value'])


def state_view(problem, base):
    """Validate base facts, then recompute all derived values from scratch."""
    result = {}
    for key, value in base.items():
        require(type(key) is tuple and key in problem.fact_specs, 'invalid ground state identity')
        declaration = problem.fact_specs[key]
        require(declaration['kind'] != 'derived', 'base state must not contain derived facts')
        check_value(declaration, value, declaration.get('unit'), nullable=True)
        if declaration['kind'] == 'static':
            expected = problem.initial_base.get(key)
            require(type(value) is type(expected) and value == expected, 'static state changed')
        result[key] = value
    for key, declaration in problem.fact_specs.items():
        if declaration['kind'] == 'static':
            require(base.get(key) == problem.initial_base.get(key), 'static state removed or changed')
        result.setdefault(key, None)
    for head, rule, parameters in problem.derived_rules:
        result[head] = evaluate_condition(problem, rule['when'], result, parameters)
    return result


def initial_state(problem):
    return deepcopy(problem.initial_base)


def check_invariants(problem, view):
    for index, condition in enumerate(problem.request['forbidden_conditions']):
        status = evaluate_condition(problem, condition, view)
        if status is True:
            raise TransitionError(f'forbidden condition {index} holds')
        if status is None:
            raise TransitionError(f'forbidden condition {index} is unknown', status='blocked_unknown')


def apply_activity(problem, base, instance):
    """Apply one grounded activity atomically. History limits belong to sequence/search."""
    activity = validate_instance(problem, instance)
    if problem.request['activity_limits'].get(instance['activity_id']) == 0:
        raise TransitionError('activity is disabled by request limit')
    parameters = instance['parameters']
    before = state_view(problem, base)
    check_invariants(problem, before)
    for name in ('applicable_when', 'preconditions'):
        status = evaluate_condition(problem, activity[name], before, parameters)
        if status is not True:
            raise TransitionError(name + (' is unknown' if status is None else ' is false'),
                                  status='blocked_unknown' if status is None else 'rejected')
    updates = {}
    for effect in activity['effects']:
        key = resolve_fact(problem, effect['fact'], parameters)
        require(key not in updates, 'duplicate/conflicting ground write')
        if 'forget' in effect:
            value = None
        else:
            expr = effect['set']
            if 'value' in expr:
                value = expr['value']
            else:
                source = before[resolve_fact(problem, expr['read'], parameters)]
                if source is None:
                    raise TransitionError('effect source value is unknown', status='blocked_unknown')
                value = source + expr['offset']
        declaration = problem.fact_specs[key]
        try:
            check_value(declaration, value, declaration.get('unit'), nullable=True)
        except ContractError as exc:
            raise TransitionError('effect outside declared domain: ' + str(exc)) from exc
        updates[key] = value
    after_base = deepcopy(base)
    after_base.update(updates)
    after = state_view(problem, after_base)
    check_invariants(problem, after)
    return after_base


def goal_status(problem, base, history=()):
    """Check final state and activity occurrence requirements, not sequence legality."""
    history = tuple(history)
    require(all(name in problem.activities for name in history), 'unknown activity in history')
    view = state_view(problem, base)
    check_invariants(problem, view)
    if not set(problem.request['required_activities']) <= set(history):
        return False
    return evaluate_condition(problem, problem.request['goal'], view)


def state_records(problem, base):
    """JSON-friendly full view; unknown values are explicit nulls."""
    records = []
    for key, value in sorted(state_view(problem, base).items()):
        item = {'fact': {'predicate': key[0], 'args': list(key[1:])}, 'value': value,
                'kind': problem.fact_specs[key]['kind']}
        if problem.fact_specs[key]['type'] == 'integer':
            item['unit'] = problem.fact_specs[key]['unit']
        records.append(item)
    return records
