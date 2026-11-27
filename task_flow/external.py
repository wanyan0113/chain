"""Document-service availability adapter and candidate-scoped feedback.

Availability is a supplied snapshot, not observed execution or resource scheduling.
No legacy equipment/chain adapter is implemented here.
"""
from copy import deepcopy

from .contracts import canonical_sha256, prepare_problem, require
from .planner import SearchSession
from .validator import verify_candidate

ANALYZER = {'id': 'document-service-availability', 'version': '1.0'}


def _object(value, keys, label):
    require(type(value) is dict and set(value) == set(keys), f'invalid {label} fields')


def _text(value):
    require(type(value) is str and bool(value.strip()), 'expected nonempty string')


def _refs(value):
    require(type(value) is list and 0 < len(value) <= 100, 'expected bounded source references')
    for item in value:
        _text(item)
    require(len(set(value)) == len(value), 'duplicate source reference')


def prepare_external(mapping, context):
    """Strict JSON contracts; unknown availability is explicit, never false by default."""
    canonical_sha256(mapping)
    canonical_sha256(context)
    _object(mapping, ['format_version', 'mapping_id', 'version', 'backend', 'entries', 'source_refs'], 'mapping')
    require(mapping['format_version'] == 'task-flow-mapping-1.0', 'unsupported mapping format')
    for key in ('mapping_id', 'version', 'backend'):
        _text(mapping[key])
    _refs(mapping['source_refs'])
    require(type(mapping['entries']) is list and len(mapping['entries']) <= 10000, 'invalid mapping entries')
    seen = set()
    for entry in mapping['entries']:
        _object(entry, ['capability_uri', 'service_id', 'source_refs'], 'mapping entry')
        for key in ('capability_uri', 'service_id'):
            _text(entry[key])
        _refs(entry['source_refs'])
        require(entry['capability_uri'] not in seen, 'duplicate capability mapping')
        seen.add(entry['capability_uri'])
    _object(context, ['format_version', 'context_id', 'version', 'services', 'source_refs'], 'context')
    require(context['format_version'] == 'task-flow-context-1.0', 'unsupported context format')
    for key in ('context_id', 'version'):
        _text(context[key])
    _refs(context['source_refs'])
    require(type(context['services']) is list and len(context['services']) <= 10000, 'invalid service snapshot')
    seen = set()
    for service in context['services']:
        _object(service, ['service_id', 'available', 'source_refs'], 'service')
        _text(service['service_id'])
        require(type(service['available']) is bool or service['available'] == 'unknown' and
                type(service['available']) is str, 'availability must be true, false or unknown')
        _refs(service['source_refs'])
        require(service['service_id'] not in seen, 'duplicate service')
        seen.add(service['service_id'])
    return deepcopy(mapping), deepcopy(context)


def _binding(problem, candidate, mapping, context):
    return {'candidate_id': candidate['candidate_id'], 'catalog_sha256': problem.catalog_sha256,
            'request_sha256': problem.request_sha256, 'mapping_sha256': canonical_sha256(mapping),
            'context_sha256': canonical_sha256(context), 'analyzer': deepcopy(ANALYZER)}


def analyze_candidate(problem, candidate, mapping, context):
    """Always independently replay before reading demands, including caller claims."""
    problem = prepare_problem(problem.catalog, problem.request)
    mapping, context = prepare_external(mapping, context)
    verification = verify_candidate(problem.catalog, problem.request, candidate)
    result = {'format_version': 'task-flow-external-1.0', 'verification': verification,
              'external_status': 'not_run', 'binding': None, 'steps': [], 'failures': [],
              'scope': 'exact_candidate_in_exact_context', 'external_check_executed': False,
              'claim_scope': 'declared_document_service_availability_only',
              'execution_confirmed': False, 'legacy_integration_status': 'not_implemented'}
    if verification['verification_status'] != 'passed':
        return result
    result['binding'] = _binding(problem, candidate, mapping, context)
    entries = {e['capability_uri']: e for e in mapping['entries']}
    services = {s['service_id']: s for s in context['services']}
    statuses = []
    for index, instance in enumerate(candidate['sequence']['steps']):
        action = problem.activities[instance['activity_id']]
        step = {'step_index': index, 'instance': deepcopy(instance),
                'source_refs': deepcopy(action['source_refs']), 'demands': []}
        for uri in action['required_capabilities']:
            declaration = next(c for c in problem.catalog['capabilities'] if c['uri'] == uri)
            demand = {'capability_uri': uri, 'capability_source_refs': declaration['source_refs'],
                      'mapping': deepcopy(entries.get(uri)), 'service_snapshot': None}
            if mapping['backend'] != ANALYZER['id'] or uri not in entries:
                status, reason = 'unsupported_mapping', 'backend or capability mapping unsupported'
            else:
                service = services.get(entries[uri]['service_id'])
                demand['service_snapshot'] = deepcopy(service)
                if service is None or service['available'] == 'unknown':
                    status, reason = 'blocked_unknown', 'service availability not established'
                elif service['available'] is False:
                    status, reason = 'rejected', 'service explicitly unavailable in supplied context'
                else:
                    status, reason = 'passed', 'service explicitly available in supplied context'
            demand.update(status=status, reason=reason)
            step['demands'].append(demand)
            statuses.append(status)
            if status != 'passed':
                result['failures'].append({'step_index': index, 'capability_uri': uri,
                                           'code': status, 'reason': reason})
        result['steps'].append(step)
    # Unsupported backends also reject empty-flow compatibility without fake steps.
    if mapping['backend'] != ANALYZER['id']:
        statuses.append('unsupported_mapping')
        result['failures'].append({'step_index': None, 'capability_uri': None,
                                  'code': 'unsupported_mapping', 'reason': 'backend not implemented'})
    result['external_status'] = next((s for s in ('unsupported_mapping', 'rejected', 'blocked_unknown')
                                       if s in statuses), 'passed')
    result['external_check_executed'] = mapping['backend'] == ANALYZER['id']
    return deepcopy(result)


class FeedbackStore:
    """In-process, exact binding cache. No imported rejection or prefix generalization."""

    def __init__(self):
        self._rejections = {}

    def lookup(self, binding):
        return deepcopy(self._rejections.get(canonical_sha256(binding)))

    def _record(self, report):
        # Private-to-pipeline evidence is produced by analyze_candidate, not an external
        # analyzer's unchecked assertions. This is a cache, not a trust boundary.
        require(report['verification']['verification_status'] == 'passed', 'unverified feedback')
        require(report['scope'] == 'exact_candidate_in_exact_context', 'unsupported feedback scope')
        if report['external_status'] == 'rejected':
            self._rejections[canonical_sha256(report['binding'])] = deepcopy(report)


class IntegrationSession:
    """Bounded search -> replay -> service analysis -> exact feedback -> resume.

    All budgets are cumulative. SearchSession retains its own node/time limits;
    analysis has a separate candidate-attempt limit, including cache hits.
    """

    def __init__(self, problem, mapping, context, *, feedback=None):
        self._problem = prepare_problem(problem.catalog, problem.request)
        self._mapping, self._context = prepare_external(mapping, context)
        self._search = SearchSession(self._problem)
        self._feedback = FeedbackStore() if feedback is None else feedback
        require(type(self._feedback) is FeedbackStore, 'unsupported feedback store')
        self._attempts = []
        self._seen = set()
        self._selected = None
        self._budget = 0

    def run(self, *, max_checks=100, max_nodes=None, timeout_ms=None):
        require(type(max_checks) is int and self._budget <= max_checks <= 10000 and max_checks > 0,
                'analysis budget must be a positive exact integer and may only increase')
        self._budget = max_checks
        status = 'selected' if self._selected else 'analysis_limit'
        while self._selected is None and len(self._attempts) < max_checks:
            search_report = self._search.report()
            pending = [c for c in search_report['candidates'] if c['candidate_id'] not in self._seen]
            if not pending:
                if search_report['search_status'] == 'exhausted':
                    status = 'search_exhausted'
                    break
                search_report = self._search.run(max_nodes=max_nodes, timeout_ms=timeout_ms)
                pending = [c for c in search_report['candidates'] if c['candidate_id'] not in self._seen]
                if not pending:
                    status = 'search_limit' if search_report['search_status'] == 'limit_reached' else 'search_exhausted'
                    break
            candidate = pending[0]
            # Cache use still checks identity and independent replay; caller flags are
            # never evidence. Context, mappings and analyzer are immutable owned copies.
            verification = verify_candidate(self._problem.catalog, self._problem.request, candidate)
            binding = _binding(self._problem, candidate, self._mapping, self._context)
            cached = self._feedback.lookup(binding) if verification['verification_status'] == 'passed' else None
            external = cached if cached is not None else analyze_candidate(
                self._problem, candidate, self._mapping, self._context)
            self._seen.add(candidate['candidate_id'])
            self._attempts.append({'candidate': deepcopy(candidate), 'analysis': external,
                                   'feedback_reused': cached is not None})
            if external['verification']['verification_status'] == 'passed':
                self._feedback._record(external)
            if external['external_status'] == 'passed':
                self._selected = candidate['candidate_id']
                status = 'selected'
                break
        report = self._search.report()
        if self._selected:
            conclusion = 'declared_external_conditions_satisfied'
        elif status == 'search_exhausted' and not report['unknown_conditions_seen'] and all(
                a['analysis']['external_status'] == 'rejected' for a in self._attempts):
            conclusion = 'no_supported_flow_in_declared_bounds_and_context'
        else:
            conclusion = 'undetermined'
        return deepcopy({'format_version': 'task-flow-integration-1.0', 'integration_status': status,
                         'conclusion': conclusion, 'selected_candidate_id': self._selected,
                         'catalog_sha256': self._problem.catalog_sha256,
                         'request_sha256': self._problem.request_sha256,
                         'input_snapshot': {'mapping': self._mapping, 'context': self._context},
                         'search': report, 'attempts': self._attempts, 'max_checks': self._budget,
                         'legacy_integration_status': 'not_implemented', 'execution_confirmed': False})
