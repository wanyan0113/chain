"""Candidate structure consumed by resource search, independent of role counts.

Generation and evaluation still use the original one-unit-per-role model.
Reservations retain the existing conservative whole-chain interval semantics.
"""
from collections import Counter


def attach_plan(chain, artifacts):
    ids = list(chain['artifacts'].values())
    chain['plan'] = {
        'mode': 'single_unit_per_role',
        'members': {role: [identifier] for role, identifier in chain['artifacts'].items()},
        'connections': [{'from': left, 'to': right,
                         'relation': 'communication' if index == 0 else 'control'}
                        for index, (left, right) in enumerate(zip(ids, ids[1:]))],
        'resource_usage': [
            {'artifact': identifier, 'capacity': 1,
             'inventory': 1 if artifacts[identifier]['consumable'] else 0,
             'start_s': chain['start_s'],
             'end_s': (chain['end_s'] + artifacts[identifier]['cooldown_s']) if 'end_s' in chain else None}
            for identifier in ids]}


def resource_usage(chain, artifacts):
    if 'plan' in chain:
        return chain['plan']['resource_usage']
    # Legacy reports and standalone callers still describe singleton roles.
    return [{'artifact': identifier, 'capacity': 1,
             'inventory': int(artifacts[identifier]['consumable']),
             'start_s': chain['start_s'], 'end_s': chain['end_s'] + artifacts[identifier]['cooldown_s']}
            for identifier in chain['artifacts'].values()]


def member_ids(chain):
    if 'plan' in chain:
        return {identifier for members in chain['plan']['members'].values() for identifier in members}
    return set(chain['artifacts'].values())


def resource_conflicts(chains, doc):
    artifacts = {a['id']: a for a in doc['artifacts']}
    reservations = {identifier: [] for identifier in artifacts}
    uses = Counter()
    for chain in chains:
        for item in resource_usage(chain, artifacts):
            identifier = item['artifact']
            reservations[identifier].extend([(item['start_s'], item['capacity']), (item['end_s'], -item['capacity'])])
            uses[identifier] += item['inventory']

    def exceeds(events, capacity):
        occupied = 0
        for _, delta in sorted(events):
            occupied += delta
            if occupied > capacity:
                return True
        return False

    conflicts = []
    for identifier, item in artifacts.items():
        if exceeds(reservations[identifier], item['capacity']):
            conflicts.append({'code': 'CAPACITY', 'resource': identifier})
        if item['consumable'] and uses[identifier] > item['inventory']:
            conflicts.append({'code': 'INVENTORY', 'resource': identifier})
    for group in doc.get('resource_groups', []):
        events = [event for identifier in group['artifacts'] for event in reservations[identifier]]
        if exceeds(events, group['capacity']):
            conflicts.append({'code': 'GROUP_CAPACITY', 'resource': group['id']})
    return conflicts
