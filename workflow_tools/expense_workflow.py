"""Synthetic expense-approval input adapter; no payments or external actions.

Limited to two claims, integer cents in CNY and a deliberately simplified policy.
This application fixture does not adapt legacy equipment or weapon workflows.
"""
from copy import deepcopy
import re

from task_flow.contracts import canonical_sha256, require

STATE_KEYS = ('submitted', 'screened', 'manager_approved', 'authorized', 'archived')
MANAGER_THRESHOLD_CENTS = 100000
REFS = ['synthetic-expense-policy-v1-not-real-financial-policy']


def _fields(value, names):
    require(type(value) is dict and set(value) == set(names), 'invalid expense input fields')


def _refs(value):
    require(type(value) is list and 1 <= len(value) <= 20 and all(type(v) is str and v.strip() for v in value), 'missing expense source references')


def validate_input(data):
    canonical_sha256(data)
    _fields(data, ['format_version', 'case_id', 'currency', 'claims', 'goal', 'search', 'source_refs'])
    require(data['format_version'] == 'expense-approval-1.0', 'unsupported expense input format')
    require(data['currency'] == 'CNY', 'only synthetic CNY integer cents are supported')
    require(type(data['case_id']) is str and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]*', data['case_id']), 'invalid case id')
    _refs(data['source_refs'])
    require(type(data['claims']) is list and 1 <= len(data['claims']) <= 2, 'expected one or two claims')
    ids = set()
    for claim in data['claims']:
        _fields(claim, ['claim_id', 'amount_cents', 'receipt_valid', 'state', 'source_refs'])
        name = claim['claim_id']
        require(type(name) is str and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]*', name), 'invalid claim id')
        require(name not in ids, 'duplicate claim id')
        ids.add(name)
        require(type(claim['amount_cents']) is int and 1 <= claim['amount_cents'] <= 100000000, 'amount must be positive bounded integer cents')
        require(type(claim['receipt_valid']) is bool or claim['receipt_valid'] is None, 'receipt status must be boolean or unknown')
        _fields(claim['state'], STATE_KEYS)
        require(all(type(v) is bool or v is None for v in claim['state'].values()), 'invalid claim state')
        _refs(claim['source_refs'])
        s = claim['state']
        for later, earlier in [('screened', 'submitted'), ('manager_approved', 'screened'),
                               ('authorized', 'screened'), ('archived', 'authorized')]:
            require(not (s[later] is True and s[earlier] is False), 'contradictory expense state')
        require(not (s['authorized'] is True and claim['amount_cents'] > MANAGER_THRESHOLD_CENTS and
                     s['manager_approved'] is False), 'high amount authorization lacks manager approval')
    _fields(data['goal'], ['claim_ids', 'require_archive'])
    goal_ids = data['goal']['claim_ids']
    require(type(goal_ids) is list and 1 <= len(goal_ids) <= 2 and all(type(n) is str for n in goal_ids)
            and len(set(goal_ids)) == len(goal_ids) and set(goal_ids) <= ids, 'invalid goal claim identities')
    require(type(data['goal']['require_archive']) is bool, 'require_archive must be boolean')
    _fields(data['search'], ['max_length', 'max_nodes', 'timeout_ms'])
    for key, low, high in [('max_length', 0, 10), ('max_nodes', 1, 20000), ('timeout_ms', 1, 30000)]:
        require(type(data['search'][key]) is int and low <= data['search'][key] <= high, 'expense search budget outside bounds')


def adapt(data):
    validate_input(data)
    data = deepcopy(data)
    digest = canonical_sha256(data)
    parameter = [{'param': 'claim'}]

    def test(name, value=True, op='eq', args=None):
        result = {'test': {'fact': {'predicate': name, 'args': parameter if args is None else args}, 'op': op, 'value': value}}
        if name == 'amount_cents':
            result['test']['unit'] = 'CNY_cent'
        return result

    def effect(name):
        return {'fact': {'predicate': name, 'args': parameter}, 'set': {'value': True}}

    facts = [{'id': name, 'arguments': ['claim'], 'kind': 'mutable', 'type': 'boolean', 'source_refs': REFS}
             for name in STATE_KEYS]
    facts += [{'id': 'receipt_valid', 'arguments': ['claim'], 'kind': 'static', 'type': 'boolean', 'source_refs': REFS},
              {'id': 'amount_cents', 'arguments': ['claim'], 'kind': 'static', 'type': 'integer',
               'minimum': 1, 'maximum': 100000000, 'unit': 'CNY_cent', 'source_refs': REFS},
              {'id': 'eligible', 'arguments': ['claim'], 'kind': 'derived', 'type': 'boolean', 'source_refs': REFS}]
    eligibility = {'all': [test('screened'), {'any': [test('amount_cents', MANAGER_THRESHOLD_CENTS, 'le'), test('manager_approved')]}]}
    specs = [
        ('submit', [test('receipt_valid'), test('submitted', False)], 'submitted', 'Submit'),
        ('screen_auto', [test('submitted'), test('screened', False)], 'screened', 'AutomaticReview'),
        ('screen_manual', [test('submitted'), test('screened', False)], 'screened', 'ManualReview'),
        ('approve_manager', [test('screened'), test('amount_cents', MANAGER_THRESHOLD_CENTS, 'gt'), test('manager_approved', False)], 'manager_approved', 'ManagerApproval'),
        ('authorize', [test('eligible'), test('authorized', False)], 'authorized', 'RecordAuthorization'),
        ('archive', [test('authorized'), test('archived', False)], 'archived', 'Archive')]
    activities = [{'id': name, 'version': '1.0', 'type': 'expense_approval', 'parameters': {'claim': 'claim'},
                   'applicable_when': {'all': []}, 'preconditions': {'all': checks}, 'effects': [effect(after)],
                   'required_capabilities': ['urn:expense:' + capability], 'max_occurrences': 2, 'source_refs': REFS}
                  for name, checks, after, capability in specs]
    catalog = {'format_version': 'task-flow-1.0', 'catalog_id': 'synthetic-expense-approval', 'version': '1.0',
               'objects': {'claim': sorted(c['claim_id'] for c in data['claims'])}, 'facts': facts,
               'derived_rules': [{'id': 'expense_eligibility', 'parameters': {'claim': 'claim'},
                                  'head': {'predicate': 'eligible', 'args': parameter}, 'when': eligibility, 'source_refs': REFS}],
               'activities': activities,
               'capabilities': [{'uri': 'urn:expense:' + name, 'mapping_status': 'unmapped', 'source_refs': REFS}
                                for name in sorted({s[3] for s in specs})], 'source_refs': REFS}
    initial, static, trace, forbidden = [], [], [], []
    for claim in data['claims']:
        args = [claim['claim_id']]
        for key, value in claim['state'].items():
            initial.append({'fact': {'predicate': key, 'args': args}, 'value': value})
            trace.append({'claim_id': claim['claim_id'], 'field': 'state.' + key, 'predicate': key,
                          'source_refs': claim['source_refs'], 'raw_sha256': digest})
        for key in ['receipt_valid', 'amount_cents']:
            item = {'fact': {'predicate': key, 'args': args}, 'value': claim[key]}
            if key == 'amount_cents':
                item['unit'] = 'CNY_cent'
            static.append(item)
            trace.append({'claim_id': claim['claim_id'], 'field': key, 'predicate': key,
                          'source_refs': claim['source_refs'], 'raw_sha256': digest})
        for later, earlier in [('screened', 'submitted'), ('manager_approved', 'screened'),
                               ('authorized', 'eligible'), ('archived', 'authorized')]:
            forbidden.append({'all': [test(later, args=args), {'not': test(earlier, args=args)}]})
    goal_fact = 'archived' if data['goal']['require_archive'] else 'authorized'
    request = {'format_version': 'task-flow-1.0', 'request_id': data['case_id'], 'catalog_id': catalog['catalog_id'],
               'catalog_version': catalog['version'], 'catalog_sha256': canonical_sha256(catalog),
               'static_facts': static, 'initial_state': initial, 'ontology_evidence': [],
               'goal': {'all': [test(goal_fact, args=[name]) for name in data['goal']['claim_ids']]},
               'required_activities': [], 'forbidden_conditions': forbidden, 'activity_limits': {},
               'search': dict(data['search'], mode='first_feasible'),
               'source_refs': [*data['source_refs'], 'raw-sha256:' + digest]}
    mapping = {'format_version': 'task-flow-mapping-1.0', 'mapping_id': 'synthetic-expense-services', 'version': '1.0',
               'backend': 'document-service-availability',
               'entries': [{'capability_uri': c['uri'], 'service_id': c['uri'].split(':')[-1], 'source_refs': REFS}
                           for c in catalog['capabilities']], 'source_refs': REFS}
    context = {'format_version': 'task-flow-context-1.0', 'context_id': 'synthetic-expense-services', 'version': '1.0',
               'services': [{'service_id': e['service_id'], 'available': True, 'source_refs': REFS} for e in mapping['entries']],
               'source_refs': REFS}
    return {'catalog': catalog, 'request': request, 'mapping': mapping, 'context': context,
            'raw_sha256': digest, 'field_mapping': trace, 'real_payment_executed': False}
