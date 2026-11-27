"""State transition semantics and manual traces, not planner verification."""
from copy import deepcopy
import unittest

from task_flow.contracts import ContractError, prepare_problem, read_json
from task_flow.state import (TransitionError, apply_activity, evaluate_condition, goal_status,
                             initial_state, state_records, state_view)
from tests.test_task_flow_contracts import EXAMPLES, bind, fixture, instance


def fact(name, args=None):
    return {'predicate': name, 'args': ['doc-1'] if args is None else args}


def test_condition(name, value=True, op='eq', unit=None):
    item = {'fact': fact(name), 'op': op, 'value': value}
    if unit is not None:
        item['unit'] = unit
    return {'test': item}


def numeric_fixture():
    cat, req = fixture()
    for name in ('left', 'right'):
        cat['facts'].append({'id': name, 'arguments': ['document'], 'kind': 'mutable', 'type': 'integer',
                             'minimum': 0, 'maximum': 20, 'unit': 'count', 'source_refs': ['synthetic-test']})
        req['initial_state'].append({'fact': fact(name), 'value': 5 if name == 'left' else 2, 'unit': 'count'})
    # Both RHS must read the old state: left<-right+1 and right<-left-1.
    action = cat['activities'][0]
    action['effects'] = [
        {'fact': fact('left', [{'param': 'document'}]), 'set': {'read': fact('right', [{'param': 'document'}]), 'offset': 1, 'unit': 'count'}},
        {'fact': fact('right', [{'param': 'document'}]), 'set': {'read': fact('left', [{'param': 'document'}]), 'offset': -1, 'unit': 'count'}}]
    return cat, req


class TaskFlowStateTests(unittest.TestCase):
    def test_all_manual_positive_witnesses_reach_goals(self):
        cat, _ = fixture()
        count = 0
        for row in read_json(EXAMPLES / 'manual_cases.json')['cases']:
            problem = prepare_problem(cat, read_json(EXAMPLES / row['request']))
            for witness in row['manual_witnesses']:
                base = initial_state(problem)
                for step in witness:
                    base = apply_activity(problem, base, step)
                self.assertIs(goal_status(problem, base, [s['activity_id'] for s in witness]), True)
                count += 1
        self.assertEqual(count, 7)

    def test_unknown_and_explicit_false_are_different(self):
        cat, req = fixture()
        req['initial_state'] = [i for i in req['initial_state'] if i['fact']['predicate'] != 'collected']
        problem = prepare_problem(cat, req)
        view = state_view(problem, initial_state(problem))
        self.assertIsNone(evaluate_condition(problem, test_condition('collected'), view))
        self.assertIs(evaluate_condition(problem, test_condition('approved'), view), False)
        self.assertIsNone(evaluate_condition(problem, {'not': test_condition('collected')}, view))
        self.assertIs(evaluate_condition(problem, {'not': test_condition('approved')}, view), True)

    def test_three_valued_all_any_and_empty_conditions(self):
        cat, req = fixture()
        problem = prepare_problem(cat, req)
        view = state_view(problem, initial_state(problem))
        view[('collected', 'doc-1')] = None
        unknown, false = test_condition('collected'), test_condition('approved')
        true = {'not': false}
        for condition, expected in [({'all': [unknown, false]}, False), ({'all': [unknown, true]}, None),
                                    ({'any': [unknown, true]}, True), ({'any': [unknown, false]}, None),
                                    ({'all': []}, True), ({'any': []}, False)]:
            self.assertIs(evaluate_condition(problem, condition, view), expected)

    def test_unknown_precondition_blocks_without_mutation(self):
        cat, _ = fixture()
        problem = prepare_problem(cat, read_json(EXAMPLES / 'unknown_source.json'))
        base = initial_state(problem)
        original = deepcopy(base)
        with self.assertRaises(TransitionError) as cm:
            apply_activity(problem, base, instance())
        self.assertEqual(cm.exception.status, 'blocked_unknown')
        self.assertEqual(base, original)

    def test_false_precondition_rejects(self):
        cat, req = fixture()
        problem = prepare_problem(cat, req)
        with self.assertRaises(TransitionError) as cm:
            apply_activity(problem, initial_state(problem), instance('approve'))
        self.assertEqual(cm.exception.status, 'rejected')

    def test_derived_facts_recomputed_after_invalidation(self):
        cat, req = fixture()
        cat['activities'][0]['effects'] = [{'fact': fact('collected', [{'param': 'document'}]), 'forget': True}]
        req['initial_state'][0]['value'] = True
        req['initial_state'][1]['value'] = True
        problem = bind(cat, req)
        base = initial_state(problem)
        self.assertIs(state_view(problem, base)[('ready', 'doc-1')], True)
        after = apply_activity(problem, base, instance())
        self.assertIsNone(state_view(problem, after)[('ready', 'doc-1')])
        self.assertIs(base[('collected', 'doc-1')], True)

    def test_derived_topological_order_independent_of_catalog_order(self):
        cat, req = fixture()
        cat['facts'].append({'id': 'second_ready', 'arguments': ['document'], 'kind': 'derived',
                             'type': 'boolean', 'source_refs': ['synthetic-test']})
        cat['derived_rules'].insert(0, {'id': 'second', 'parameters': {'document': 'document'},
                                       'head': fact('second_ready', [{'param': 'document'}]),
                                       'when': {'test': {'fact': fact('ready', [{'param': 'document'}]), 'op': 'eq', 'value': True}},
                                       'source_refs': ['synthetic-test']})
        req['initial_state'][0]['value'] = req['initial_state'][1]['value'] = True
        problem = bind(cat, req)
        self.assertIs(state_view(problem, initial_state(problem))[('second_ready', 'doc-1')], True)

    def test_static_state_cannot_be_changed_or_removed(self):
        cat, req = fixture()
        problem = prepare_problem(cat, req)
        for change in ('remove', 'change'):
            base = initial_state(problem)
            if change == 'remove':
                del base[('source_available',)]
            else:
                base[('source_available',)] = False
            with self.subTest(change=change), self.assertRaises(ContractError):
                state_view(problem, base)

    def test_input_derived_values_rejected_instead_of_retained(self):
        cat, req = fixture()
        problem = prepare_problem(cat, req)
        base = initial_state(problem)
        base[('ready', 'doc-1')] = True
        with self.assertRaises(ContractError):
            state_view(problem, base)

    def test_numeric_updates_are_atomic_and_effect_order_independent(self):
        cat, req = numeric_fixture()
        problem = bind(cat, req)
        base = initial_state(problem)
        after = apply_activity(problem, base, instance())
        self.assertEqual((after[('left', 'doc-1')], after[('right', 'doc-1')]), (3, 4))
        self.assertEqual((base[('left', 'doc-1')], base[('right', 'doc-1')]), (5, 2))
        cat['activities'][0]['effects'].reverse()
        other = bind(cat, req)
        self.assertEqual(apply_activity(other, initial_state(other), instance()), after)

    def test_numeric_overflow_rolls_back_whole_step(self):
        cat, req = numeric_fixture()
        cat['activities'][0]['effects'][1]['set']['offset'] = 100
        problem = bind(cat, req)
        base = initial_state(problem)
        original = deepcopy(base)
        with self.assertRaises(TransitionError):
            apply_activity(problem, base, instance())
        self.assertEqual(base, original)

    def test_boolean_and_wrong_units_rejected_for_integer_facts(self):
        for mode in ('boolean', 'unit', 'float'):
            cat, req = numeric_fixture()
            record = req['initial_state'][-1]
            if mode == 'boolean':
                record['value'] = True
            elif mode == 'float':
                record['value'] = 2.0
            else:
                record['unit'] = 'seconds'
            with self.subTest(mode=mode), self.assertRaises(ContractError):
                bind(cat, req)

    def test_numeric_comparison_and_unknown_read(self):
        cat, req = numeric_fixture()
        req['goal'] = test_condition('left', 3, 'ge', 'count')
        problem = bind(cat, req)
        self.assertIs(goal_status(problem, initial_state(problem)), True)
        base = initial_state(problem)
        base[('right', 'doc-1')] = None
        with self.assertRaises(TransitionError) as cm:
            apply_activity(problem, base, instance())
        self.assertEqual(cm.exception.status, 'blocked_unknown')

    def test_initial_and_post_transition_forbidden_states(self):
        cat, req = fixture()
        req['forbidden_conditions'] = [test_condition('collected')]
        problem = prepare_problem(cat, req)
        base = initial_state(problem)
        with self.assertRaises(TransitionError):
            apply_activity(problem, base, instance())
        self.assertIs(base[('collected', 'doc-1')], False)
        base[('collected', 'doc-1')] = True
        with self.assertRaises(TransitionError):
            goal_status(problem, base)

    def test_unknown_forbidden_condition_is_not_safety_proof(self):
        cat, req = fixture()
        req['forbidden_conditions'] = [test_condition('collected')]
        req['initial_state'][0]['value'] = None
        problem = prepare_problem(cat, req)
        with self.assertRaises(TransitionError) as cm:
            goal_status(problem, initial_state(problem))
        self.assertEqual(cm.exception.status, 'blocked_unknown')

    def test_initial_goal_and_mandatory_activity_are_separate(self):
        cat, _ = fixture()
        done = prepare_problem(cat, read_json(EXAMPLES / 'already_done.json'))
        self.assertIs(goal_status(done, initial_state(done)), True)
        mandatory = prepare_problem(cat, read_json(EXAMPLES / 'mandatory_verify.json'))
        self.assertIs(goal_status(mandatory, initial_state(mandatory)), False)
        self.assertIs(goal_status(mandatory, apply_activity(mandatory, initial_state(mandatory), instance('verify')), ['verify']), True)

    def test_goal_destroyed_after_previous_success_is_false(self):
        cat, req = fixture()
        req['initial_state'][2]['value'] = True
        cat['activities'][0]['effects'] = [{'fact': fact('published', [{'param': 'document'}]), 'set': {'value': False}}]
        problem = bind(cat, req)
        base = initial_state(problem)
        self.assertIs(goal_status(problem, base), True)
        self.assertIs(goal_status(problem, apply_activity(problem, base, instance())), False)

    def test_json_records_include_unknown_and_derived_identity(self):
        cat, req = fixture()
        req['initial_state'][0]['value'] = None
        problem = prepare_problem(cat, req)
        records = state_records(problem, initial_state(problem))
        self.assertEqual(len(records), 6)
        collected = next(r for r in records if r['fact']['predicate'] == 'collected')
        self.assertEqual(collected['fact']['args'], ['doc-1'])
        self.assertIsNone(collected['value'])

    def test_explicit_zero_limit_disables_activity(self):
        cat, req = fixture()
        req['activity_limits']['collect'] = 0
        problem = prepare_problem(cat, req)
        with self.assertRaisesRegex(TransitionError, 'disabled'):
            apply_activity(problem, initial_state(problem), instance())

    def test_mandatory_activity_history_accepts_an_iterator(self):
        cat, _ = fixture()
        problem = prepare_problem(cat, read_json(EXAMPLES / 'mandatory_verify.json'))
        self.assertIs(goal_status(problem, initial_state(problem), iter(['verify'])), True)

    def test_checked_in_integer_example_updates_and_reaches_goal(self):
        directory = EXAMPLES.parent / 'counter_transfer'
        problem = prepare_problem(read_json(directory / 'catalog.json'), read_json(directory / 'request.json'))
        base = initial_state(problem)
        self.assertIs(goal_status(problem, base), False)
        after = apply_activity(problem, base, {'activity_id': 'transfer', 'activity_version': '1.0', 'parameters': {'resource': 'r-1'}})
        self.assertEqual((after[('remaining', 'r-1')], after[('processed', 'r-1')]), (4, 3))
        self.assertIs(goal_status(problem, after), True)


if __name__ == '__main__':
    unittest.main()
