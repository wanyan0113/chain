"""Bounded planning, continuation and independent tiny-space enumeration."""
from copy import deepcopy
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from task_flow.contracts import ContractError, canonical_sha256, prepare_problem, read_json, validate_sequence
from task_flow.planner import SearchSession
from tests.task_flow_oracle import exhaustive_sequences, sequence_key
from tests.test_task_flow_contracts import EXAMPLES, ROOT, bind, fixture


def prepared(case='basic', mode='first_feasible'):
    catalog = read_json(EXAMPLES / 'catalog.json')
    request = read_json(EXAMPLES / (case + '.json'))
    request['search']['mode'] = mode
    return prepare_problem(catalog, request)


def found(report):
    return {sequence_key(c['sequence']['steps']) for c in report['candidates']}


class TickClock:
    def __init__(self, step=0.01):
        self.current = 0.0
        self.step = step

    def __call__(self):
        value = self.current
        self.current += self.step
        return value


class TaskFlowPlannerTests(unittest.TestCase):
    def test_first_sequences_follow_initial_state_and_goal(self):
        for case, length in [('basic', 3), ('verified', 4), ('collected', 2),
                             ('already_done', 0), ('mandatory_verify', 1)]:
            problem = prepared(case)
            result = SearchSession(problem).run()
            with self.subTest(case=case):
                self.assertEqual(result['search_status'], 'candidate_found')
                self.assertEqual(result['coverage'], 'partial')
                self.assertEqual(len(result['candidates'][0]['sequence']['steps']), length)
                validate_sequence(problem, result['candidates'][0]['sequence'])
                self.assertEqual(result['candidates'][0]['verification_status'], 'not_run')
                self.assertEqual(result['candidates'][0]['external_status'], 'not_run')

    def test_all_eight_small_cases_match_independent_full_enumeration(self):
        archive = read_json(ROOT / 'docs/task-flow/phase3/example_results.json')
        recorded = {r['case']: r for r in archive['cases']}
        for row in read_json(EXAMPLES / 'manual_cases.json')['cases']:
            problem = prepared(Path(row['request']).stem, 'enumerate_bounded')
            result = SearchSession(problem).run()
            with self.subTest(case=problem.request['request_id']):
                self.assertEqual(result['search_status'], 'exhausted')
                self.assertEqual(result['coverage'], 'complete')
                self.assertEqual(found(result), exhaustive_sequences(problem.catalog, problem.request))
                self.assertEqual(len(found(result)), len(result['candidates']))
                saved = recorded[problem.request['request_id']]
                self.assertEqual(saved['certified_candidates'], len(found(result)))
                self.assertEqual(saved['reference_candidates'], len(found(result)))
                self.assertEqual(saved['conclusion'], result['conclusion'])
                self.assertEqual(saved['unknown_conditions_seen'], result['unknown_conditions_seen'])
                self.assertEqual(archive['source_catalog_sha256'], problem.catalog_sha256)

    def test_same_state_different_capabilities_not_merged(self):
        result = SearchSession(prepared(mode='enumerate_bounded')).run()
        triples = [c for c in result['candidates'] if len(c['sequence']['steps']) == 3]
        self.assertEqual(len(triples), 2)
        self.assertEqual(triples[0]['final_state'], triples[1]['final_state'])
        self.assertNotEqual(triples[0]['candidate_id'], triples[1]['candidate_id'])
        self.assertNotEqual(triples[0]['capability_demands'], triples[1]['capability_demands'])

    def test_enumeration_continues_beyond_satisfied_goal(self):
        result = SearchSession(prepared(mode='enumerate_bounded')).run()
        lengths = [len(c['sequence']['steps']) for c in result['candidates']]
        self.assertIn(3, lengths)
        self.assertIn(4, lengths)
        self.assertEqual(lengths, sorted(lengths))

    def test_successive_first_calls_recover_every_candidate_without_duplicates(self):
        problem = prepared()
        session = SearchSession(problem)
        previous_count = 0
        for _ in range(100):
            result = session.run()
            if result['search_status'] == 'exhausted':
                break
            self.assertEqual(result['search_status'], 'candidate_found')
            self.assertEqual(len(result['candidates']), previous_count + 1)
            previous_count += 1
        else:
            self.fail('continuation failed to exhaust tiny search')
        self.assertEqual(found(result), exhaustive_sequences(problem.catalog, problem.request))
        self.assertEqual(len(found(result)), len(result['candidates']))
        self.assertEqual(result, session.run())

    def test_resume_offers_an_alternative_without_marking_external_approval(self):
        session = SearchSession(prepared('feedback_fallback'))
        first = session.run()
        second = session.run()
        self.assertNotEqual(first['candidates'][0]['candidate_id'], second['candidates'][1]['candidate_id'])
        self.assertEqual(first['candidates'][0]['final_state'], second['candidates'][1]['final_state'])
        self.assertTrue(all(c['external_status'] == 'not_run' for c in second['candidates']))

    def test_reports_and_prepared_inputs_do_not_mutate_session(self):
        problem = prepared()
        original = deepcopy((problem.catalog, problem.request))
        session = SearchSession(problem)
        result = session.run()
        candidate = deepcopy(result['candidates'][0])
        result['candidates'][0]['sequence']['steps'].clear()
        result['input_snapshot']['catalog']['activities'].clear()
        problem.catalog['activities'].clear()
        again = session.run()
        self.assertEqual(again['candidates'][0], candidate)
        self.assertEqual(again['input_snapshot']['catalog'], original[0])

    def test_modified_prepared_catalog_rejected_on_session_creation(self):
        problem = prepared()
        problem.catalog['version'] = 'changed'
        with self.assertRaises(ContractError):
            SearchSession(problem)

    def test_candidate_ids_stable_for_same_input_and_effective_budgets(self):
        problem = prepared()
        a = SearchSession(problem).run(max_nodes=100)
        b = SearchSession(problem).run(max_nodes=200)
        self.assertEqual(a['candidates'], b['candidates'])
        self.assertEqual(a['statistics']['nodes'], b['statistics']['nodes'])

    def test_node_limit_is_exact_and_resumable(self):
        problem = prepared(mode='enumerate_bounded')
        session = SearchSession(problem)
        limited = session.run(max_nodes=1)
        self.assertEqual(limited['statistics']['nodes'], 1)
        self.assertEqual(limited['termination_reason'], 'node_limit')
        self.assertEqual(limited['coverage'], 'partial')
        self.assertEqual(limited['conclusion'], 'undetermined')
        final = session.run(max_nodes=5000)
        self.assertEqual(final['search_status'], 'exhausted')
        self.assertEqual(found(final), exhaustive_sequences(problem.catalog, problem.request))

    def test_limit_retains_prior_candidates_and_resume_does_not_duplicate(self):
        problem = prepared(mode='enumerate_bounded')
        session = SearchSession(problem)
        partial = session.run(max_candidates=1)
        self.assertEqual(partial['termination_reason'], 'candidate_limit')
        self.assertEqual(len(partial['candidates']), 1)
        self.assertEqual(partial['conclusion'], 'candidates_available')
        final = session.run(max_candidates=100)
        self.assertEqual(final['search_status'], 'exhausted')
        self.assertEqual(final['candidates'][0], partial['candidates'][0])
        self.assertEqual(len(final['candidates']), len(found(final)))
        self.assertEqual(found(final), exhaustive_sequences(problem.catalog, problem.request))

    def test_time_limit_uses_injected_monotonic_clock(self):
        problem = prepared(mode='enumerate_bounded')
        session = SearchSession(problem, clock=TickClock())
        result = session.run(timeout_ms=1)
        self.assertEqual(result['termination_reason'], 'time_limit')
        self.assertEqual(result['statistics']['nodes'], 0)
        self.assertEqual(result['conclusion'], 'undetermined')
        result = session.run(timeout_ms=100000)
        self.assertEqual(result['search_status'], 'exhausted')
        self.assertEqual(found(result), exhaustive_sequences(problem.catalog, problem.request))

    def test_caller_pause_does_not_spend_search_time(self):
        clock = TickClock(step=0)
        session = SearchSession(prepared(), clock=clock)
        session.run(timeout_ms=1)
        clock.current += 100000
        result = session.run()
        self.assertEqual(result['search_status'], 'candidate_found')
        self.assertEqual(result['statistics']['elapsed_ms'], 0)

    def test_invalid_budget_update_is_atomic_and_cannot_shrink_after_start(self):
        session = SearchSession(prepared())
        before = session.report()
        with self.assertRaises(ContractError):
            session.run(max_nodes=1, timeout_ms=False)
        self.assertEqual(before, session.report())
        session.run(max_nodes=100)
        for kwargs in ({'max_nodes': 99}, {'timeout_ms': 0}, {'max_candidates': True}, {'max_nodes': 2 ** 60}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ContractError):
                session.run(**kwargs)

    def test_known_no_solution_unknown_block_and_limit_are_distinct(self):
        no_solution = SearchSession(prepared('too_short')).run()
        unknown = SearchSession(prepared('unknown_source')).run()
        limited = SearchSession(prepared()).run(max_nodes=1)
        self.assertEqual(no_solution['conclusion'], 'bounded_no_solution')
        self.assertFalse(no_solution['unknown_conditions_seen'])
        self.assertEqual(unknown['conclusion'], 'blocked_unknown')
        self.assertTrue(unknown['unknown_conditions_seen'])
        self.assertEqual(limited['conclusion'], 'undetermined')
        self.assertEqual(unknown['coverage_scope'], 'certified_executable_sequences_within_declared_bounds')

    def test_zero_length_and_unknown_final_goal(self):
        cat, req = fixture()
        req['search']['max_length'] = 0
        req['initial_state'][2]['value'] = None
        result = SearchSession(prepare_problem(cat, req)).run()
        self.assertEqual(result['conclusion'], 'blocked_unknown')
        self.assertEqual(result['statistics']['nodes'], 0)
        self.assertEqual(result['current_depth_limit'], 0)

    def test_known_failed_precondition_settles_other_unknown_condition(self):
        cat, req = fixture()
        cat['activities'] = [cat['activities'][0]]
        action = cat['activities'][0]
        action['applicable_when'] = deepcopy(action['preconditions'])
        action['preconditions'] = {'test': {'fact': {'predicate': 'collected', 'args': [{'param': 'document'}]}, 'op': 'eq', 'value': True}}
        req['static_facts'][0]['value'] = None
        result = SearchSession(bind(cat, req)).run()
        self.assertEqual(result['conclusion'], 'bounded_no_solution')
        self.assertFalse(result['unknown_conditions_seen'])

    def test_known_forbidden_state_settles_other_unknown_invariant(self):
        cat, req = fixture()
        req['initial_state'][0]['value'] = None
        req['initial_state'][1]['value'] = True
        req['forbidden_conditions'] = [{'test': {'fact': {'predicate': n, 'args': ['doc-1']}, 'op': 'eq', 'value': True}}
                                       for n in ('collected', 'approved')]
        result = SearchSession(prepare_problem(cat, req)).run()
        self.assertEqual(result['conclusion'], 'bounded_no_solution')
        self.assertEqual(result['statistics']['nodes'], 0)
        self.assertFalse(result['unknown_conditions_seen'])

    def test_integer_search_matches_oracle_and_keeps_longer_goals(self):
        folder = EXAMPLES.parent / 'counter_transfer'
        cat, req = read_json(folder / 'catalog.json'), read_json(folder / 'request.json')
        req['search']['mode'] = 'enumerate_bounded'
        result = SearchSession(prepare_problem(cat, req)).run()
        self.assertEqual(found(result), exhaustive_sequences(cat, req))
        self.assertEqual([len(c['sequence']['steps']) for c in result['candidates']], [1, 2, 3, 4, 5])

    def test_two_object_parameterized_search_matches_raw_enumeration(self):
        cat, req = fixture()
        cat['objects']['document'].append('doc-2')
        req['search']['mode'] = 'enumerate_bounded'
        problem = bind(cat, req)
        result = SearchSession(problem).run(max_nodes=10000)
        self.assertEqual(result['search_status'], 'exhausted')
        self.assertEqual(found(result), exhaustive_sequences(cat, req))

    def test_noop_and_mandatory_history_are_not_state_pruned(self):
        problem = prepared('mandatory_verify', 'enumerate_bounded')
        result = SearchSession(problem).run()
        self.assertEqual(found(result), exhaustive_sequences(problem.catalog, problem.request))
        self.assertIn(('verify',), {tuple(s['activity_id'] for s in c['sequence']['steps']) for c in result['candidates']})
        self.assertTrue(all(any(s['activity_id'] == 'verify' for s in c['sequence']['steps']) for c in result['candidates']))

    def test_seeded_boolean_models_match_independent_oracle(self):
        rng = random.Random(20261127)
        refs = ['synthetic-seeded-small-model']
        for trial in range(24):
            predicates = ['p', 'q']
            def atom(name):
                return {'predicate': name, 'args': []}
            def condition():
                test = {'test': {'fact': atom(rng.choice(predicates + ['derived'])), 'op': 'eq', 'value': bool(rng.randrange(2))}}
                return {'not': test} if rng.randrange(3) == 0 else test
            cat = {'format_version': 'task-flow-1.0', 'catalog_id': 'small-model', 'version': '1', 'objects': {},
                   'facts': [{'id': n, 'arguments': [], 'kind': 'mutable' if n in predicates else 'derived', 'type': 'boolean', 'source_refs': refs}
                             for n in predicates + ['derived']],
                   'derived_rules': [{'id': 'derive', 'parameters': {}, 'head': atom('derived'),
                                      'when': {'any': [{'test': {'fact': atom(n), 'op': 'eq', 'value': True}} for n in predicates]}, 'source_refs': refs}],
                   'activities': [], 'capabilities': [{'uri': 'urn:test:capability', 'mapping_status': 'unmapped', 'source_refs': refs}], 'source_refs': refs}
            for index in range(rng.randint(2, 4)):
                effect = {'fact': atom(rng.choice(predicates))}
                effect.update({'forget': True} if rng.randrange(4) == 0 else {'set': {'value': bool(rng.randrange(2))}})
                cat['activities'].append({'id': f'a{index}', 'version': '1', 'type': 'test', 'parameters': {},
                                           'applicable_when': {'all': []}, 'preconditions': condition(), 'effects': [effect],
                                           'required_capabilities': ['urn:test:capability'], 'max_occurrences': 2, 'source_refs': refs})
            req = {'format_version': 'task-flow-1.0', 'request_id': f'trial-{trial}', 'catalog_id': cat['catalog_id'], 'catalog_version': '1',
                   'catalog_sha256': canonical_sha256(cat), 'static_facts': [],
                   'initial_state': [{'fact': atom(n), 'value': rng.choice([True, False, None])} for n in predicates],
                   'ontology_evidence': [], 'goal': condition(), 'required_activities': ['a0'] if trial % 3 == 0 else [],
                   'forbidden_conditions': [condition()] if trial % 5 == 0 else [], 'activity_limits': {},
                   'search': {'mode': 'enumerate_bounded', 'max_length': 4, 'max_nodes': 50000, 'timeout_ms': 20000}, 'source_refs': refs}
            result = SearchSession(prepare_problem(cat, req)).run()
            with self.subTest(trial=trial):
                self.assertEqual(result['search_status'], 'exhausted')
                self.assertEqual(found(result), exhaustive_sequences(cat, req))
                self.assertEqual(len(found(result)), len(result['candidates']))

    def test_cli_plan_persists_new_report_and_refuses_overwrite(self):
        command = [sys.executable, '-B', '-m', 'task_flow', 'plan', '--catalog', str(EXAMPLES / 'catalog.json'), '--request', str(EXAMPLES / 'basic.json')]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'report.json'
            first = subprocess.run(command + ['--output', str(output)], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(first.returncode, 0, first.stderr)
            result = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(result, json.loads(first.stdout))
            self.assertFalse(result['independent_verification_executed'])
            before = output.read_bytes()
            second = subprocess.run(command + ['--output', str(output)], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(second.returncode, 2)
            self.assertEqual(output.read_bytes(), before)

    def test_cli_partial_and_invalid_mode_override_report_distinct_states(self):
        command = [sys.executable, '-B', '-m', 'task_flow', 'plan', '--catalog', str(EXAMPLES / 'catalog.json')]
        limited = subprocess.run(command + ['--request', str(EXAMPLES / 'basic.json'), '--max-nodes', '1'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(limited.returncode, 1)
        self.assertEqual(json.loads(limited.stdout)['search_status'], 'limit_reached')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.json'
            path.write_text('{}', encoding='utf-8')
            invalid = subprocess.run(command + ['--request', str(path), '--mode', 'enumerate_bounded'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(json.loads(invalid.stdout)['status'], 'input_incomplete')


if __name__ == '__main__':
    unittest.main()
