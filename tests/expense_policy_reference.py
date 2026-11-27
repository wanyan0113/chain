"""Independent, narrow domain monitor for the synthetic expense case.

Reads raw business records, never the activity model or planner/validator state.
The simplified threshold is a shared declared policy assumption, not real policy.
"""
from copy import deepcopy


def check_expense_policy(raw, steps):
    claims = {c['claim_id']: deepcopy(c) for c in raw['claims']}
    trace = []

    def fail(index, reason):
        return {'passed': False, 'failure_step': index, 'reason': reason, 'trace': trace}

    if len(steps) > raw['search']['max_length']:
        return fail(None, 'length limit')
    for index, step in enumerate(steps):
        if (type(step) is not dict or set(step) != {'activity_id', 'activity_version', 'parameters'} or
                step['activity_version'] != '1.0' or type(step['parameters']) is not dict or
                set(step['parameters']) != {'claim'} or type(step['parameters']['claim']) is not str or
                step['parameters']['claim'] not in claims):
            return fail(index, 'invalid activity instance')
        claim = claims[step['parameters']['claim']]
        state = claim['state']
        name = step['activity_id']
        before = deepcopy(state)
        if name == 'submit':
            if claim['receipt_valid'] is not True or state['submitted'] is not False:
                return fail(index, 'submission requires known valid receipt and not submitted')
            state['submitted'] = True
        elif name in ('screen_auto', 'screen_manual'):
            if state['submitted'] is not True or state['screened'] is not False:
                return fail(index, 'review requires submitted claim not yet reviewed')
            state['screened'] = True
        elif name == 'approve_manager':
            if state['screened'] is not True or claim['amount_cents'] <= 100000 or state['manager_approved'] is not False:
                return fail(index, 'manager approval requirements')
            state['manager_approved'] = True
        elif name == 'authorize':
            if state['screened'] is not True or state['authorized'] is not False:
                return fail(index, 'authorization requires review')
            if claim['amount_cents'] > 100000 and state['manager_approved'] is not True:
                return fail(index, 'high amount requires manager approval')
            state['authorized'] = True
        elif name == 'archive':
            if state['authorized'] is not True or state['archived'] is not False:
                return fail(index, 'archiving requires authorization')
            state['archived'] = True
        else:
            return fail(index, 'unknown activity')
        trace.append({'step_index': index, 'claim_id': claim['claim_id'], 'activity_id': name,
                      'before': before, 'after': deepcopy(state)})
    goal = 'archived' if raw['goal']['require_archive'] else 'authorized'
    if any(claims[name]['state'][goal] is not True for name in raw['goal']['claim_ids']):
        return fail(None, 'business goal not satisfied')
    return {'passed': True, 'failure_step': None, 'reason': None, 'trace': trace}
