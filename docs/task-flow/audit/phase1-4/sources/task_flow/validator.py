"""Independent sequence replay. Does not import planner or generation-side state.

Shares strict syntax/input validation, but rebuilds facts, rule order, condition
evaluation and atomic effects directly from raw catalog/request declarations.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import heapq
from itertools import product

from .contracts import (SAFE_INTEGER, ContractError, canonical_sha256, prepare_problem,
                        require, validate_shape)

MAX_TRACE_RECORDS = 100000


def _key(ref, binding):
    return (ref['predicate'], *(binding[a['param']] if isinstance(a, dict) else a for a in ref['args']))


def _ref(key):
    return {'predicate': key[0], 'args': list(key[1:])}


def _bindings(parameters, objects):
    names = sorted(parameters)
    for values in product(*(sorted(objects[parameters[n]]) for n in names)):
        yield dict(zip(names, values))


def _reads(condition, binding):
    if 'test' in condition:
        return {_key(condition['test']['fact'], binding)}
    if 'not' in condition:
        return _reads(condition['not'], binding)
    result = set()
    for child in condition.get('all', condition.get('any')):
        result.update(_reads(child, binding))
    return result


def _evaluate(expression, view, binding, rule_ids):
    """Return a JSON-ready condition explanation, with explicit three-valued truth."""
    evidence = []
    if 'test' in expression:
        test = expression['test']
        key = _key(test['fact'], binding)
        actual = view.get(key)
        expected = test['value']
        if actual is None:
            value = None
        elif test['op'] == 'eq':
            value = actual == expected
        elif test['op'] == 'ne':
            value = actual != expected
        elif test['op'] == 'lt':
            value = actual < expected
        elif test['op'] == 'le':
            value = actual <= expected
        elif test['op'] == 'gt':
            value = actual > expected
        else:
            value = actual >= expected
        evidence.append({'fact': _ref(key), 'actual': actual, 'op': test['op'], 'expected': expected,
                         'derived_rule_id': rule_ids.get(key)})
    elif 'not' in expression:
        child = _evaluate(expression['not'], view, binding, rule_ids)
        value = None if child['value'] is None else not child['value']
        evidence = child['evidence']
    else:
        all_of = 'all' in expression
        children = [_evaluate(child, view, binding, rule_ids)
                    for child in expression['all' if all_of else 'any']]
        values = [c['value'] for c in children]
        if all_of:
            value = False if any(v is False for v in values) else True if all(v is True for v in values) else None
        else:
            value = True if any(v is True for v in values) else False if all(v is False for v in values) else None
        evidence = [e for child in children for e in child['evidence']]
    return {'expression': deepcopy(expression), 'value': value, 'evidence': evidence}


class _Replay:
    def __init__(self, problem):
        self.catalog, self.request = problem.catalog, problem.request
        self.catalog_sha, self.request_sha = problem.catalog_sha256, problem.request_sha256
        self.actions = {a['id']: a for a in self.catalog['activities']}
        self.specs = {}
        for declaration in self.catalog['facts']:
            domains = [self.catalog['objects'][d] for d in declaration['arguments']]
            for values in product(*domains):
                self.specs[(declaration['id'], *values)] = declaration
        self.rules = {}
        for rule in self.catalog['derived_rules']:
            for binding in _bindings(rule['parameters'], self.catalog['objects']):
                self.rules[_key(rule['head'], binding)] = (rule, binding)
        self.rule_ids = {key: rule['id'] for key, (rule, _) in self.rules.items()}
        dependencies = {key: _reads(rule['when'], binding) & self.rules.keys()
                        for key, (rule, binding) in self.rules.items()}
        followers = defaultdict(set)
        counts = {key: len(deps) for key, deps in dependencies.items()}
        for head, deps in dependencies.items():
            for prior in deps:
                followers[prior].add(head)
        ready = [key for key, count in counts.items() if count == 0]
        heapq.heapify(ready)
        self.order = []
        while ready:
            key = heapq.heappop(ready)
            self.order.append(key)
            for child in followers[key]:
                counts[child] -= 1
                if counts[child] == 0:
                    heapq.heappush(ready, child)
        require(len(self.order) == len(self.rules), 'independent rule ordering found a cycle')

    def initial(self):
        return {_key(item['fact'], {}): item['value'] for item in
                self.request['static_facts'] + self.request['initial_state'] + self.request['ontology_evidence']}

    def view(self, base):
        result = {key: base.get(key) for key in self.specs}
        derivations = []
        for head in self.order:
            rule, binding = self.rules[head]
            check = _evaluate(rule['when'], result, binding, self.rule_ids)
            result[head] = check['value']
            derivations.append({'rule_id': rule['id'], 'head': _ref(head), 'parameters': binding,
                                'check': check, 'source_refs': rule['source_refs']})
        return result, derivations

    def records(self, view):
        records = []
        for key in sorted(self.specs):
            spec = self.specs[key]
            item = {'fact': _ref(key), 'value': view[key], 'kind': spec['kind']}
            if spec['type'] == 'integer':
                item['unit'] = spec['unit']
            records.append(item)
        return records

    def forbidden(self, view):
        checks = [_evaluate(c, view, {}, self.rule_ids) for c in self.request['forbidden_conditions']]
        for expected, status in ((True, 'rejected'), (None, 'blocked_unknown')):
            for index, check in enumerate(checks):
                if check['value'] is expected:
                    return checks, (status, 'forbidden_condition', f'forbidden condition {index} is {expected}')
        return checks, None

    def verify(self, sequence, max_trace_records):
        require(type(max_trace_records) is int and 1 <= max_trace_records <= 1000000, 'invalid trace record budget')
        result = {'format_version': 'task-flow-verification-1.0', 'verification_status': 'not_run',
                  'catalog_sha256': self.catalog_sha, 'request_sha256': self.request_sha,
                  'sequence_sha256': None, 'trace': [], 'initial_state': [], 'final_state': [],
                  'initial_derivations': [], 'initial_invariants': [], 'final_checks': None,
                  'input_sources': {'catalog': self.catalog['source_refs'], 'request': self.request['source_refs'],
                                    'imported_static_evidence': self.request['ontology_evidence']},
                  'failure': None, 'independent_verification_executed': False,
                  'external_status': 'not_run'}

        def stop(status, code, reason, phase, index=None):
            result['verification_status'] = status
            result['failure'] = {'phase': phase, 'step_index': index, 'code': code, 'reason': reason}
            return deepcopy(result)

        try:
            validate_shape(sequence, 'sequence')
        except ContractError as exc:
            index = exc.path[1] if len(exc.path) > 1 and exc.path[0] == 'steps' and type(exc.path[1]) is int else None
            return stop('rejected', 'sequence_contract', str(exc), 'sequence', index)
        result['sequence_sha256'] = canonical_sha256(sequence)
        if sequence['catalog_sha256'] != self.catalog_sha or sequence['request_sha256'] != self.request_sha:
            return stop('rejected', 'input_binding', 'sequence catalog/request digest mismatch', 'sequence')
        if len(sequence['steps']) > self.request['search']['max_length']:
            return stop('rejected', 'length_limit', 'sequence exceeds declared length', 'sequence')
        if (2 * len(sequence['steps']) + 2) * max(1, len(self.specs)) > max_trace_records:
            return stop('limit_reached', 'trace_limit', 'verification trace exceeds declared record budget', 'budget')
        result['independent_verification_executed'] = True
        base = self.initial()
        view, derivations = self.view(base)
        result['initial_state'] = result['final_state'] = self.records(view)
        result['initial_derivations'] = derivations
        checks, violation = self.forbidden(view)
        result['initial_invariants'] = checks
        if violation:
            return stop(*violation, 'initial_state')
        occurrences = Counter()
        for index, instance in enumerate(sequence['steps']):
            name, binding = instance['activity_id'], instance['parameters']
            frame = {'step_index': index, 'instance': deepcopy(instance), 'status': 'not_run',
                     'before_state': self.records(view), 'after_state': None, 'committed': False,
                     'applicability': None, 'preconditions': None, 'effects': [],
                     'state_delta': [], 'derived_checks': [], 'invariants_after': [], 'source_refs': []}
            result['trace'].append(frame)

            def fail_step(status, code, reason):
                frame['status'] = status
                return stop(status, code, reason, 'activity', index)

            if name not in self.actions:
                return fail_step('rejected', 'unknown_activity', 'activity is not declared')
            action = self.actions[name]
            frame['source_refs'] = action['source_refs']
            if instance['activity_version'] != action['version']:
                return fail_step('rejected', 'activity_version', 'activity version mismatch')
            if binding.keys() != action['parameters'].keys() or any(
                    binding[p] not in self.catalog['objects'][domain] for p, domain in action['parameters'].items()):
                return fail_step('rejected', 'parameter_binding', 'incomplete or out-of-domain activity binding')
            occurrences[name] += 1
            if occurrences[name] > min(action['max_occurrences'], self.request['activity_limits'].get(name, action['max_occurrences'])):
                return fail_step('rejected', 'occurrence_limit', 'activity occurrence limit exceeded')
            frame['applicability'] = _evaluate(action['applicable_when'], view, binding, self.rule_ids)
            frame['preconditions'] = _evaluate(action['preconditions'], view, binding, self.rule_ids)
            for expected, status in ((False, 'rejected'), (None, 'blocked_unknown')):
                for field in ('applicability', 'preconditions'):
                    if frame[field]['value'] is expected:
                        return fail_step(status, field, field + ' is not established')
            changes = {}
            for effect in action['effects']:
                target = _key(effect['fact'], binding)
                spec = self.specs[target]
                if target in changes or spec['kind'] != 'mutable':
                    return fail_step('rejected', 'effect_write', 'duplicate or non-mutable write')
                if 'forget' in effect:
                    proposed = None
                elif 'value' in effect['set']:
                    proposed = effect['set']['value']
                else:
                    expr = effect['set']
                    source = view[_key(expr['read'], binding)]
                    if source is None:
                        return fail_step('blocked_unknown', 'effect_source', 'effect source is unknown')
                    proposed = source + expr['offset']
                frame['effects'].append({'target': _ref(target), 'old_value': view[target],
                                         'proposed_value': proposed, 'definition': deepcopy(effect)})
                if proposed is not None:
                    valid = type(proposed) is bool if spec['type'] == 'boolean' else (
                        type(proposed) is int and abs(proposed) <= SAFE_INTEGER and spec['minimum'] <= proposed <= spec['maximum'])
                    if not valid:
                        return fail_step('rejected', 'effect_domain', 'effect value outside declared type/range')
                changes[target] = proposed
            proposed_base = dict(base)
            proposed_base.update(changes)
            proposed_view, derivations = self.view(proposed_base)
            frame['after_state'] = self.records(proposed_view)
            frame['derived_checks'] = derivations
            frame['state_delta'] = [{'fact': _ref(key), 'before': view[key], 'after': proposed_view[key]}
                                    for key in sorted(self.specs) if type(view[key]) is not type(proposed_view[key]) or view[key] != proposed_view[key]]
            frame['invariants_after'], violation = self.forbidden(proposed_view)
            if violation:
                return fail_step(*violation)
            base, view = proposed_base, proposed_view
            frame['status'], frame['committed'] = 'passed', True
            result['final_state'] = self.records(view)
        goal = _evaluate(self.request['goal'], view, {}, self.rule_ids)
        missing = sorted(set(self.request['required_activities']) - occurrences.keys())
        result['final_checks'] = {'goal': goal, 'missing_required_activities': missing}
        if missing:
            return stop('rejected', 'mandatory_activity', 'required activities did not occur: ' + ', '.join(missing), 'final')
        if goal['value'] is not True:
            return stop('blocked_unknown' if goal['value'] is None else 'rejected', 'final_goal', 'final goal is not established', 'final')
        result['verification_status'] = 'passed'
        return deepcopy(result)


def verify_sequence(catalog, request, sequence, *, max_trace_records=MAX_TRACE_RECORDS):
    """Verify the sequence against external inputs; malformed catalog/request raise."""
    return _Replay(prepare_problem(catalog, request)).verify(sequence, max_trace_records)


def _verify_candidate(engine, candidate, max_trace_records):
    expected_keys = {'candidate_id', 'sequence', 'capability_demands', 'final_state',
                     'generation_goal_satisfied', 'verification_status', 'external_status'}
    if type(candidate) is not dict or set(candidate) != expected_keys:
        result = engine.verify({}, max_trace_records)
        result['failure'] = {'phase': 'candidate', 'step_index': None, 'code': 'candidate_shape', 'reason': 'invalid candidate envelope'}
        return result
    result = engine.verify(candidate['sequence'], max_trace_records)
    if result['verification_status'] != 'passed':
        return result
    sequence = candidate['sequence']
    demands = [{'step_index': i, 'activity_id': step['activity_id'],
                'required_capabilities': engine.actions[step['activity_id']]['required_capabilities'],
                'source_refs': engine.actions[step['activity_id']]['source_refs']} for i, step in enumerate(sequence['steps'])]
    try:
        checks = [
            ('candidate_identity', candidate['candidate_id'] == 'flow-' + canonical_sha256(sequence)),
            ('claimed_final_state', canonical_sha256(candidate['final_state']) == canonical_sha256(result['final_state'])),
            ('claimed_capabilities', canonical_sha256(candidate['capability_demands']) == canonical_sha256(demands)),
            ('claimed_goal', candidate['generation_goal_satisfied'] is True)]
    except ContractError as exc:
        checks = [('candidate_metadata', False)]
        metadata_reason = str(exc)
    else:
        metadata_reason = 'candidate claims differ from independent replay'
    for code, passed in checks:
        if not passed:
            result['verification_status'] = 'rejected'
            result['failure'] = {'phase': 'candidate', 'step_index': None, 'code': code, 'reason': metadata_reason}
            break
    return result


def verify_candidate(catalog, request, candidate, *, max_trace_records=MAX_TRACE_RECORDS):
    return _verify_candidate(_Replay(prepare_problem(catalog, request)), candidate, max_trace_records)


def verify_search_report(catalog, request, report, *, max_candidates=1000, max_trace_records=MAX_TRACE_RECORDS):
    """Verify candidate contents, not search coverage, optimality or external claims."""
    engine = _Replay(prepare_problem(catalog, request))
    require(type(max_candidates) is int and 1 <= max_candidates <= 10000, 'invalid verification candidate budget')
    require(type(max_trace_records) is int and 1 <= max_trace_records <= 1000000, 'invalid trace record budget')
    result = {'format_version': 'task-flow-verification-batch-1.0', 'verification_status': 'not_run',
              'catalog_sha256': engine.catalog_sha, 'request_sha256': engine.request_sha,
              'candidate_verifications': [], 'coverage': 'none', 'failure': None,
              'coverage_scope': 'candidate_verification_attempts_not_search',
              'search_claims_verified': False, 'external_status': 'not_run', 'counts': {},
              'candidate_count': 0, 'checked_count': 0, 'planning_executed': False,
              'independent_verification_executed': False}
    try:
        require(type(report) is dict and report.get('format_version') == 'task-flow-search-1.0', 'not a supported search report')
        require(report.get('catalog_sha256') == engine.catalog_sha and report.get('request_sha256') == engine.request_sha, 'search report input binding mismatch')
        snapshot = report.get('input_snapshot', {})
        require(type(snapshot) is dict, 'search report snapshot must be an object')
        require(canonical_sha256(snapshot.get('catalog')) == engine.catalog_sha and
                canonical_sha256(snapshot.get('request')) == engine.request_sha, 'search report snapshot mismatch')
        candidates = report.get('candidates')
        require(type(candidates) is list, 'candidate array missing')
        ids = [c.get('candidate_id') for c in candidates if type(c) is dict]
        require(len(ids) == len(candidates) and all(type(i) is str for i in ids) and len(ids) == len(set(ids)), 'invalid or duplicate candidate identities')
    except ContractError as exc:
        result['verification_status'] = 'rejected'
        result['failure'] = {'code': 'report_binding', 'reason': str(exc)}
        return result
    for candidate in candidates[:max_candidates]:
        checked = _verify_candidate(engine, candidate, max_trace_records)
        result['candidate_verifications'].append({'candidate_id': candidate['candidate_id'], 'report': checked})
    counts = Counter(c['report']['verification_status'] for c in result['candidate_verifications'])
    result.update(candidate_count=len(candidates), checked_count=len(result['candidate_verifications']), counts=dict(counts))
    result['independent_verification_executed'] = any(c['report']['independent_verification_executed'] for c in result['candidate_verifications'])
    if len(candidates) > max_candidates:
        result['verification_status'], result['coverage'] = 'limit_reached', 'partial'
    elif not candidates:
        result['failure'] = {'code': 'no_candidates', 'reason': 'no candidate sequences to verify; search claims were not checked'}
    else:
        result['coverage'] = 'complete'
        result['verification_status'] = ('rejected' if counts['rejected'] else 'blocked_unknown' if counts['blocked_unknown']
                                         else 'limit_reached' if counts['limit_reached'] else 'passed')
        if counts['limit_reached']:
            result['coverage'] = 'partial'
    return deepcopy(result)
