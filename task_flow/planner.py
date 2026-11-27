"""Deterministic bounded sequence search. No independent verification or scheduling."""
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
import time

from .contracts import SAFE_INTEGER, canonical_sha256, prepare_problem, require
from .state import (TransitionError, apply_activity, goal_status, initial_state,
                    state_records)

DEFAULT_MAX_CANDIDATES = 10000


@dataclass
class _Frame:
    base: dict
    steps: tuple = ()
    counts: dict = field(default_factory=dict)
    next_index: int = 0
    terminal_checked: bool = False


class SearchSession:
    """Own an in-process frontier. Limits are cumulative and pauses do not spend time.

    Calling run again after candidate_found continues beyond that candidate. A limit
    may be increased explicitly on the same session; file checkpoints are unsupported.
    """

    def __init__(self, problem, *, clock=None):
        # Revalidate owned input copies: do not trust a modified prepared object.
        self._problem = prepare_problem(problem.catalog, problem.request, max_groundings=100000)
        self._clock = time.monotonic if clock is None else clock
        self._stack = [_Frame(initial_state(self._problem))]
        self._depth_limit = 0
        self._candidates = []
        self._elapsed = 0.0
        search = self._problem.request['search']
        self._limits = {key: search[key] for key in ('max_nodes', 'timeout_ms')}
        self._limits['max_candidates'] = DEFAULT_MAX_CANDIDATES
        self._stats = Counter(nodes=0, prefixes_checked=0, transitions_applied=0, rejected_prefixes=0,
                              rejected_occurrences=0, rejected_transitions=0,
                              unknown_branches=0, unknown_goal_checks=0)
        self._unknown_examples = []
        self._reasons = Counter()
        self._status = 'not_started'
        self._reason = 'not_started'

    def _unknown(self, category, frame, reason, instance=None):
        self._stats[category] += 1
        if len(self._unknown_examples) < 30:
            record = {'prefix': list(frame.steps), 'reason': reason}
            if instance is not None:
                record['attempted_instance'] = instance
            self._unknown_examples.append(deepcopy(record))

    def _candidate(self, frame):
        problem = self._problem
        sequence = {'format_version': 'task-flow-1.0', 'catalog_sha256': problem.catalog_sha256,
                    'request_sha256': problem.request_sha256, 'steps': list(frame.steps)}
        demands = []
        for index, instance in enumerate(frame.steps):
            activity = problem.activities[instance['activity_id']]
            demands.append({'step_index': index, 'activity_id': activity['id'],
                            'required_capabilities': activity['required_capabilities'],
                            'source_refs': activity['source_refs']})
        return deepcopy({'candidate_id': 'flow-' + canonical_sha256(sequence), 'sequence': sequence,
                         'capability_demands': demands, 'final_state': state_records(problem, frame.base),
                         'generation_goal_satisfied': True, 'verification_status': 'not_run',
                         'external_status': 'not_run'})

    def run(self, *, max_nodes=None, timeout_ms=None, max_candidates=None):
        limits = dict(self._limits)
        for key, value in (('max_nodes', max_nodes), ('timeout_ms', timeout_ms), ('max_candidates', max_candidates)):
            if value is not None:
                require(type(value) is int and 1 <= value <= SAFE_INTEGER and
                        (self._status == 'not_started' or value >= self._limits[key]),
                        'budgets must be positive exact integers; resume limits may only increase')
                limits[key] = value
        self._limits = limits
        if self._status == 'exhausted':
            return self.report()
        problem = self._problem
        start = self._clock()
        self._status, self._reason = 'running', 'running'
        try:
            while True:
                if (self._elapsed + self._clock() - start) * 1000 >= self._limits['timeout_ms']:
                    self._status, self._reason = 'limit_reached', 'time_limit'
                    break
                if not self._stack:
                    if self._depth_limit >= problem.request['search']['max_length']:
                        self._status, self._reason = 'exhausted', 'frontier_exhausted'
                        break
                    self._depth_limit += 1
                    self._stack.append(_Frame(initial_state(problem)))
                frame = self._stack[-1]
                if not frame.terminal_checked:
                    try:
                        terminal = goal_status(problem, frame.base, frame.counts)
                    except TransitionError as exc:
                        if exc.status == 'blocked_unknown':
                            self._unknown('unknown_branches', frame, exc.reason)
                        else:
                            self._stats['rejected_prefixes'] += 1
                            self._reasons[exc.reason] += 1
                        self._stack.pop()
                        continue
                    is_endpoint = len(frame.steps) == self._depth_limit
                    if is_endpoint and terminal is True and len(self._candidates) >= self._limits['max_candidates']:
                        # Leave this terminal unprocessed for an explicit larger budget.
                        self._status, self._reason = 'limit_reached', 'candidate_limit'
                        break
                    frame.terminal_checked = True
                    self._stats['prefixes_checked'] += 1
                    if is_endpoint and terminal is None:
                        self._unknown('unknown_goal_checks', frame, 'final goal contains unknown facts')
                    elif is_endpoint and terminal is True:
                        self._candidates.append(self._candidate(frame))
                        if problem.request['search']['mode'] == 'first_feasible':
                            self._status, self._reason = 'candidate_found', 'first_feasible_pause'
                            break
                if len(frame.steps) >= self._depth_limit or frame.next_index >= len(problem.instances):
                    self._stack.pop()
                    continue
                # Count every attempted grounded instance, including occurrence rejection.
                if self._stats['nodes'] >= self._limits['max_nodes']:
                    self._status, self._reason = 'limit_reached', 'node_limit'
                    break
                instance = problem.instances[frame.next_index]
                frame.next_index += 1
                self._stats['nodes'] += 1
                name = instance['activity_id']
                activity = problem.activities[name]
                cap = min(activity['max_occurrences'], problem.request['activity_limits'].get(name, activity['max_occurrences']))
                if frame.counts.get(name, 0) >= cap:
                    self._stats['rejected_occurrences'] += 1
                    continue
                try:
                    after = apply_activity(problem, frame.base, instance)
                except TransitionError as exc:
                    if exc.status == 'blocked_unknown':
                        self._unknown('unknown_branches', frame, exc.reason, instance)
                    else:
                        self._stats['rejected_transitions'] += 1
                        self._reasons[exc.reason] += 1
                    continue
                counts = dict(frame.counts)
                counts[name] = counts.get(name, 0) + 1
                self._stats['transitions_applied'] += 1
                self._stack.append(_Frame(after, frame.steps + (instance,), counts))
        finally:
            self._elapsed += max(0.0, self._clock() - start)
        return self.report()

    def report(self):
        unknown = bool(self._stats['unknown_branches'] + self._stats['unknown_goal_checks'])
        if self._status == 'exhausted':
            conclusion = ('candidates_available' if self._candidates else
                          'blocked_unknown' if unknown else 'bounded_no_solution')
            coverage = 'complete'
        else:
            conclusion = 'candidates_available' if self._candidates else 'undetermined'
            coverage = 'not_started' if self._status == 'not_started' else 'partial'
        return deepcopy({'format_version': 'task-flow-search-1.0',
                         'algorithm': 'iterative_deepening_depth_first',
                         'catalog_sha256': self._problem.catalog_sha256,
                         'request_sha256': self._problem.request_sha256,
                         'search_status': self._status, 'termination_reason': self._reason,
                         'conclusion': conclusion, 'coverage': coverage,
                         'coverage_scope': 'certified_executable_sequences_within_declared_bounds',
                         'unknown_conditions_seen': unknown,
                         'unknown_examples': self._unknown_examples,
                         'candidates': self._candidates,
                         'statistics': dict(self._stats, candidate_count=len(self._candidates),
                                            elapsed_ms=round(self._elapsed * 1000, 3)),
                         'rejections': [{'reason': reason, 'count': count} for reason, count in sorted(self._reasons.items())],
                         'limits': dict(self._limits, max_length=self._problem.request['search']['max_length']),
                         'current_depth_limit': self._depth_limit,
                         'resume_supported': self._status != 'exhausted',
                         'planning_executed': self._status != 'not_started',
                         'independent_verification_executed': False,
                         'input_snapshot': {'catalog': self._problem.catalog, 'request': self._problem.request}})


def search(problem, **limits):
    """One run; retain a SearchSession explicitly when continuation is needed."""
    return SearchSession(problem).run(**limits)
