"""Independent literal-rule replay for the fictional astral-rift experiment.

Does not read the generated catalog or call production execution/grounding.
It shares the declared experiment assumptions, not a real-world domain authority.
"""
from itertools import permutations

RULES = {
    'S1': (('rift_exists',), 'anomaly_known', 2),
    'S2': (('anomaly_known',), 'state_verified', 3),
    'S3': (('state_verified',), 'energy_logged', 2),
    'D1': (('state_verified',), 'stability_analyzed', 4),
    'D2': (('stability_analyzed',), 'plan_defined', 5),
    'D3': (('energy_logged',), 'observation_assessed', 3),
    'A1': (('plan_defined', 'can_stabilize'), 'stabilized', 6),
    'A2': (('stabilized',), 'result_confirmed', 2)}
HISTORY = [('state_verified','anomaly_known'), ('energy_logged','state_verified'),
           ('stability_analyzed','state_verified'), ('plan_defined','stability_analyzed'),
           ('observation_assessed','energy_logged'), ('stabilized','plan_defined'),
           ('result_confirmed','stabilized')]


def key(steps):
    return tuple((s['activity_id'],s['activity_version'],tuple(sorted(s['parameters'].items()))) for s in steps)


def replay(initial, goals, steps, *, delay_budget=None, max_length=8):
    state = dict(initial)
    trace = []
    seen = set()

    def fail(index, reason):
        return {'passed':False, 'failure_step':index, 'reason':reason, 'trace':trace}

    def safe():
        return all(state[c] is not True or state[p] is True for c,p in HISTORY)

    if len(steps)>max_length:
        return fail(None,'length_limit')
    if not safe():
        return fail(None,'initial_history_not_certified')
    if delay_budget is not None and state['elapsed_seconds']>delay_budget:
        return fail(None,'initial_delay_limit')
    for index, instance in enumerate(steps):
        if (type(instance) is not dict or set(instance)!={'activity_id','activity_version','parameters'} or
                type(instance['activity_id']) is not str or instance['activity_id'] not in RULES or
                instance['activity_version']!='1.0' or instance['parameters']!={'rift':'rift-01'}):
            return fail(index,'invalid_instance')
        name=instance['activity_id']
        if name in seen:
            return fail(index,'repeated_activity')
        requirements, effect, seconds=RULES[name]
        if not all(state[f] is True for f in requirements):
            return fail(index,'precondition_not_certified')
        before=dict(state)
        state[effect]=True
        state['elapsed_seconds']+=seconds
        if not safe() or state['elapsed_seconds']>60 or (delay_budget is not None and state['elapsed_seconds']>delay_budget):
            return fail(index,'state_or_delay_limit')
        seen.add(name)
        trace.append({'step_index':index,'instance':instance,'before':before,'after':dict(state)})
    if not all(state[f] is True for f in goals):
        return fail(None,'goal_not_satisfied')
    return {'passed':True,'failure_step':None,'reason':None,'trace':trace,
            'final_state':state,'delay_seconds':state['elapsed_seconds']}


def enumerate_reference(initial, goals):
    instances=[{'activity_id':name,'activity_version':'1.0','parameters':{'rift':'rift-01'}} for name in sorted(RULES)]
    accepted=set()
    checked=0
    delays={}
    for length in range(9):
        for steps in permutations(instances,length):
            checked+=1
            result=replay(initial,goals,steps)
            if result['passed']:
                identity=key(steps)
                accepted.add(identity)
                delays[identity]=result['delay_seconds']
    return {'raw_sequences_checked':checked,'accepted':accepted,'delays':delays}
