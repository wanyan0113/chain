"""Small brute-force reference, independent of production grounding/state/search.

Only valid bounded test inputs are accepted. This is a test oracle, not the
phase-four versioned standalone verifier or a production planning API.
"""
from collections import Counter
from itertools import product
import operator


def sequence_key(steps):
    return tuple((s['activity_id'], s['activity_version'], tuple(sorted(s['parameters'].items()))) for s in steps)


def exhaustive_sequences(catalog, request):
    objects = catalog['objects']
    declarations = {d['id']: d for d in catalog['facts']}
    actions = {a['id']: a for a in catalog['activities']}

    def assignments(parameters):
        names = sorted(parameters)
        return [dict(zip(names, values)) for values in product(*(objects[parameters[n]] for n in names))]

    def identity(ref, parameters):
        return (ref['predicate'], *(parameters[v['param']] if isinstance(v, dict) else v for v in ref['args']))

    rules = {}
    for rule in catalog['derived_rules']:
        for parameters in assignments(rule['parameters']):
            rules[identity(rule['head'], parameters)] = (rule['when'], parameters)

    def condition(expr, getter, parameters):
        if 'not' in expr:
            v = condition(expr['not'], getter, parameters)
            return None if v is None else not v
        if 'all' in expr:
            values = [condition(e, getter, parameters) for e in expr['all']]
            if any(v is False for v in values):
                return False
            return True if all(v is True for v in values) else None
        if 'any' in expr:
            values = [condition(e, getter, parameters) for e in expr['any']]
            if any(v is True for v in values):
                return True
            return False if all(v is False for v in values) else None
        item = expr['test']
        value = getter(identity(item['fact'], parameters))
        if value is None:
            return None
        functions = {'eq': operator.eq, 'ne': operator.ne, 'lt': operator.lt,
                     'le': operator.le, 'gt': operator.gt, 'ge': operator.ge}
        return functions[item['op']](value, item['value'])

    def getter_for(base):
        cache = dict(base)

        def getter(key):
            if key in cache:
                return cache[key]
            if key in rules:
                expr, parameters = rules[key]
                cache[key] = condition(expr, getter, parameters)
                return cache[key]
            return None
        return getter

    initial = {identity(i['fact'], {}): i['value'] for i in request['static_facts'] + request['initial_state']}
    for item in request['ontology_evidence']:
        initial[identity(item['fact'], {})] = item['value']

    def safe(base):
        getter = getter_for(base)
        return all(condition(c, getter, {}) is False for c in request['forbidden_conditions'])

    instances = []
    for name, action in actions.items():
        instances.extend({'activity_id': name, 'activity_version': action['version'], 'parameters': p}
                         for p in assignments(action['parameters']))
    accepted = set()
    # Generate every raw sequence, replay it from scratch, without prefix pruning.
    for length in range(request['search']['max_length'] + 1):
        for steps in product(instances, repeat=length):
            counts = Counter(s['activity_id'] for s in steps)
            if any(n > min(actions[name]['max_occurrences'], request['activity_limits'].get(name, actions[name]['max_occurrences']))
                   for name, n in counts.items()):
                continue
            base = dict(initial)
            valid = safe(base)
            for step in steps:
                if not valid:
                    break
                action, parameters = actions[step['activity_id']], step['parameters']
                getter = getter_for(base)
                valid = all(condition(action[k], getter, parameters) is True for k in ('applicable_when', 'preconditions'))
                if not valid:
                    break
                changes = {}
                for effect in action['effects']:
                    key = identity(effect['fact'], parameters)
                    if 'forget' in effect:
                        value = None
                    elif 'value' in effect['set']:
                        value = effect['set']['value']
                    else:
                        expr = effect['set']
                        source = getter(identity(expr['read'], parameters))
                        if source is None:
                            valid = False
                            break
                        value = source + expr['offset']
                    spec = declarations[key[0]]
                    if value is not None and spec['type'] == 'integer' and not spec['minimum'] <= value <= spec['maximum']:
                        valid = False
                        break
                    changes[key] = value
                if valid:
                    base.update(changes)
                    valid = safe(base)
            if valid and set(request['required_activities']) <= counts.keys() and condition(request['goal'], getter_for(base), {}) is True:
                accepted.add(sequence_key(steps))
    return accepted
