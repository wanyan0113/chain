"""Independent replay, tampered candidates, provenance and CLI boundaries."""
from copy import deepcopy
from itertools import product
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from task_flow import SearchSession, prepare_problem, read_json
from task_flow.contracts import ContractError, canonical_sha256
from task_flow.validator import verify_candidate, verify_search_report, verify_sequence
from tests.task_flow_oracle import exhaustive_sequences, sequence_key
from tests.test_task_flow_contracts import EXAMPLES, ROOT, fixture, instance
from tests.test_task_flow_state import fact, numeric_fixture, test_condition


def sequence(catalog, request, names):
    return {'format_version': 'task-flow-1.0', 'catalog_sha256': canonical_sha256(catalog),
            'request_sha256': canonical_sha256(request), 'steps': [instance(n) for n in names]}


def rebind(catalog, request):
    request['catalog_sha256'] = canonical_sha256(catalog)


class TaskFlowValidatorTests(unittest.TestCase):
    def test_seven_manual_positive_witnesses_independently_pass(self):
        cat, _ = fixture()
        count = 0
        for case in read_json(EXAMPLES / 'manual_cases.json')['cases']:
            req = read_json(EXAMPLES / case['request'])
            for witness in case['manual_witnesses']:
                seq = sequence(cat, req, [])
                seq['steps'] = witness
                report = verify_sequence(cat, req, seq)
                self.assertEqual(report['verification_status'], 'passed', report['failure'])
                self.assertTrue(report['independent_verification_executed'])
                self.assertEqual(len(report['trace']), len(witness))
                self.assertEqual(report['external_status'], 'not_run')
                count += 1
        self.assertEqual(count, 7)

    def test_does_not_call_generation_state_or_planner(self):
        cat, req = fixture()
        bad = Mock(side_effect=AssertionError('generation function called by verifier'))
        with patch.multiple('task_flow.state', evaluate_condition=bad, state_view=bad,
                            apply_activity=bad, initial_state=bad, goal_status=bad), \
                patch('task_flow.planner.SearchSession.run', bad):
            report = verify_sequence(cat, req, sequence(cat, req, ['collect', 'approve', 'publish_standard']))
        self.assertEqual(report['verification_status'], 'passed')
        bad.assert_not_called()

    def test_rebuilds_state_and_rules_instead_of_using_prepared_maps(self):
        cat, req = fixture()
        poisoned = prepare_problem(cat, req)
        poisoned.initial_base = {('collected', 'doc-1'): True, ('approved', 'doc-1'): True}
        poisoned.derived_rules = ()
        poisoned.activities = {}
        poisoned.fact_specs = {}
        with patch('task_flow.validator.prepare_problem', return_value=poisoned):
            report = verify_sequence(cat, req, sequence(cat, req, ['approve']))
        self.assertEqual(report['verification_status'], 'rejected')
        self.assertEqual(report['failure']['step_index'], 0)
        self.assertEqual(report['failure']['code'], 'preconditions')

    def test_all_small_raw_sequences_match_independent_exhaustive_reference(self):
        cat, req = fixture()
        req['search']['max_length'] = 3
        valid = set()
        names = [a['id'] for a in cat['activities']]
        for length in range(4):
            for order in product(names, repeat=length):
                seq = sequence(cat, req, order)
                report = verify_sequence(cat, req, seq)
                if report['verification_status'] == 'passed':
                    valid.add(sequence_key(seq['steps']))
        self.assertEqual(len(valid), 2)
        self.assertEqual(valid, exhaustive_sequences(cat, req))

    def test_generated_candidates_from_all_eight_examples(self):
        cat, _ = fixture()
        archive = read_json(ROOT / 'docs/task-flow/phase4/example_results.json')
        recorded = {r['case']: r for r in archive['cases']}
        for case in read_json(EXAMPLES / 'manual_cases.json')['cases']:
            req = read_json(EXAMPLES / case['request'])
            req['search']['mode'] = 'enumerate_bounded'
            source = SearchSession(prepare_problem(cat, req)).run()
            batch = verify_search_report(cat, req, source)
            with self.subTest(case=case['request']):
                saved = recorded[req['request_id']]
                self.assertEqual(saved['counts'], batch['counts'])
                self.assertEqual(saved['candidate_count'], len(source['candidates']))
                self.assertEqual(saved['verification_status'], batch['verification_status'])
                self.assertEqual(archive['catalog_sha256'], canonical_sha256(cat))
                self.assertFalse(batch['search_claims_verified'])
                if source['candidates']:
                    self.assertEqual(batch['verification_status'], 'passed', batch)
                    self.assertEqual(batch['counts'], {'passed': len(source['candidates'])})
                else:
                    self.assertEqual(batch['verification_status'], 'not_run')
                    self.assertEqual(batch['failure']['code'], 'no_candidates')

    def test_swap_delete_and_unknown_activity_are_rejected(self):
        cat, req = fixture()
        for names, index in [(['approve', 'collect', 'publish_standard'], 0),
                             (['collect', 'publish_standard'], 1), (['missing'], 0)]:
            report = verify_sequence(cat, req, sequence(cat, req, names))
            with self.subTest(names=names):
                self.assertEqual(report['verification_status'], 'rejected')
                self.assertEqual(report['failure']['step_index'], index)

    def test_sequence_digest_and_activity_version_are_bound(self):
        cat, req = fixture()
        for mode in ('catalog', 'request', 'activity'):
            seq = sequence(cat, req, ['collect'])
            if mode == 'activity':
                seq['steps'][0]['activity_version'] = 'wrong'
            else:
                seq[mode + '_sha256'] = '0' * 64
            report = verify_sequence(cat, req, seq)
            self.assertEqual(report['verification_status'], 'rejected')
            self.assertEqual(report['failure']['code'], 'activity_version' if mode == 'activity' else 'input_binding')

    def test_missing_step_field_identifies_schema_error_position(self):
        cat, req = fixture()
        seq = sequence(cat, req, ['collect'])
        del seq['steps'][0]['activity_version']
        report = verify_sequence(cat, req, seq)
        self.assertEqual(report['failure']['code'], 'sequence_contract')
        self.assertEqual(report['failure']['step_index'], 0)
        self.assertFalse(report['independent_verification_executed'])

    def test_out_of_domain_binding_and_extra_claim_fields(self):
        cat, req = fixture()
        seq = sequence(cat, req, ['collect'])
        seq['steps'][0]['parameters']['document'] = 'other'
        report = verify_sequence(cat, req, seq)
        self.assertEqual(report['failure']['code'], 'parameter_binding')
        self.assertEqual(report['failure']['step_index'], 0)
        seq = sequence(cat, req, [])
        seq['after_state'] = {'published': True}
        self.assertEqual(verify_sequence(cat, req, seq)['failure']['code'], 'sequence_contract')

    def test_length_occurrence_and_disabled_activity_constraints(self):
        cat, req = fixture()
        req['search']['max_length'] = 0
        self.assertEqual(verify_sequence(cat, req, sequence(cat, req, ['collect']))['failure']['code'], 'length_limit')
        req['search']['max_length'] = 4
        self.assertEqual(verify_sequence(cat, req, sequence(cat, req, ['collect', 'collect']))['failure']['code'], 'occurrence_limit')
        req['activity_limits']['collect'] = 0
        self.assertEqual(verify_sequence(cat, req, sequence(cat, req, ['collect']))['failure']['code'], 'occurrence_limit')

    def test_unknown_precondition_is_not_pass_or_false(self):
        cat, _ = fixture()
        req = read_json(EXAMPLES / 'unknown_source.json')
        report = verify_sequence(cat, req, sequence(cat, req, ['collect']))
        self.assertEqual(report['verification_status'], 'blocked_unknown')
        self.assertIsNone(report['trace'][0]['preconditions']['value'])
        self.assertFalse(report['trace'][0]['committed'])

    def test_known_failure_settles_unknown_applicability(self):
        cat, req = fixture()
        action = cat['activities'][0]
        action['applicable_when'] = deepcopy(action['preconditions'])
        action['preconditions'] = test_condition('collected')
        req['static_facts'][0]['value'] = None
        rebind(cat, req)
        report = verify_sequence(cat, req, sequence(cat, req, ['collect']))
        self.assertEqual(report['verification_status'], 'rejected')
        self.assertIsNone(report['trace'][0]['applicability']['value'])
        self.assertIs(report['trace'][0]['preconditions']['value'], False)

    def test_forbidden_state_rejects_proposed_effect_and_keeps_old_state(self):
        cat, req = fixture()
        req['forbidden_conditions'] = [test_condition('collected')]
        report = verify_sequence(cat, req, sequence(cat, req, ['collect']))
        self.assertEqual(report['verification_status'], 'rejected')
        self.assertFalse(report['trace'][0]['committed'])
        self.assertIs(next(r['value'] for r in report['final_state'] if r['fact']['predicate'] == 'collected'), False)
        self.assertIs(next(r['value'] for r in report['trace'][0]['after_state'] if r['fact']['predicate'] == 'collected'), True)

    def test_initial_forbidden_true_overrides_other_unknown_invariant(self):
        cat, req = fixture()
        req['initial_state'][0]['value'] = None
        req['initial_state'][1]['value'] = True
        req['forbidden_conditions'] = [test_condition('collected'), test_condition('approved')]
        report = verify_sequence(cat, req, sequence(cat, req, []))
        self.assertEqual(report['verification_status'], 'rejected')
        self.assertEqual(report['failure']['phase'], 'initial_state')
        req['initial_state'][1]['value'] = False
        report = verify_sequence(cat, req, sequence(cat, req, []))
        self.assertEqual(report['verification_status'], 'blocked_unknown')

    def test_empty_sequence_mandatory_activity_and_final_goal(self):
        cat, _ = fixture()
        for case, status in [('already_done', 'passed'), ('mandatory_verify', 'rejected'), ('basic', 'rejected')]:
            req = read_json(EXAMPLES / (case + '.json'))
            report = verify_sequence(cat, req, sequence(cat, req, []))
            with self.subTest(case=case):
                self.assertEqual(report['verification_status'], status)
        req = read_json(EXAMPLES / 'mandatory_verify.json')
        report = verify_sequence(cat, req, sequence(cat, req, ['verify']))
        self.assertEqual(report['verification_status'], 'passed')

    def test_goal_achieved_then_destroyed_is_rejected(self):
        cat, req = fixture()
        req['initial_state'][2]['value'] = True
        cat['activities'][0]['effects'] = [{'fact': fact('published', [{'param': 'document'}]), 'set': {'value': False}}]
        rebind(cat, req)
        report = verify_sequence(cat, req, sequence(cat, req, ['collect']))
        self.assertEqual(report['verification_status'], 'rejected')
        self.assertEqual(report['failure']['code'], 'final_goal')
        self.assertIs(report['final_checks']['goal']['value'], False)

    def test_derived_facts_recomputed_on_forget_and_false(self):
        for effect, status, value in [({'forget': True}, 'blocked_unknown', None), ({'set': {'value': False}}, 'rejected', False)]:
            cat, req = fixture()
            req['initial_state'][0]['value'] = req['initial_state'][1]['value'] = True
            req['goal'] = test_condition('ready')
            cat['activities'][0]['effects'] = [dict(fact=fact('collected', [{'param': 'document'}]), **effect)]
            rebind(cat, req)
            report = verify_sequence(cat, req, sequence(cat, req, ['collect']))
            self.assertEqual(report['verification_status'], status)
            self.assertIs(report['final_checks']['goal']['value'], value)
            self.assertEqual(report['trace'][0]['derived_checks'][0]['rule_id'], 'ready-if-collected-and-approved')

    def test_numeric_effects_read_old_state_and_failure_is_atomic(self):
        cat, req = numeric_fixture()
        req['goal'] = test_condition('left', 3, 'eq', 'count')
        rebind(cat, req)
        report = verify_sequence(cat, req, sequence(cat, req, ['collect']))
        self.assertEqual(report['verification_status'], 'passed')
        values = {r['fact']['predicate']: r['value'] for r in report['final_state']}
        self.assertEqual((values['left'], values['right']), (3, 4))
        cat['activities'][0]['effects'][1]['set']['offset'] = 100
        rebind(cat, req)
        report = verify_sequence(cat, req, sequence(cat, req, ['collect']))
        self.assertEqual(report['failure']['code'], 'effect_domain')
        self.assertFalse(report['trace'][0]['committed'])
        values = {r['fact']['predicate']: r['value'] for r in report['final_state']}
        self.assertEqual((values['left'], values['right']), (5, 2))

    def test_unknown_numeric_source_blocks(self):
        cat, req = numeric_fixture()
        req['initial_state'][-1]['value'] = None
        rebind(cat, req)
        report = verify_sequence(cat, req, sequence(cat, req, ['collect']))
        self.assertEqual(report['verification_status'], 'blocked_unknown')
        self.assertEqual(report['failure']['code'], 'effect_source')

    def test_two_object_parameter_binding_replayed_independently(self):
        cat, req = fixture()
        cat['objects']['document'].append('doc-2')
        req['goal'] = {'test': {'fact': fact('published', ['doc-2']), 'op': 'eq', 'value': True}}
        rebind(cat, req)
        seq = sequence(cat, req, ['collect', 'approve', 'publish_backup'])
        for step in seq['steps']:
            step['parameters']['document'] = 'doc-2'
        self.assertEqual(verify_sequence(cat, req, seq)['verification_status'], 'passed')

    def test_integer_derived_chain_is_reordered_and_recomputed(self):
        folder = EXAMPLES.parent / 'counter_transfer'
        cat, req = read_json(folder / 'catalog.json'), read_json(folder / 'request.json')
        for name in ('positive', 'done'):
            cat['facts'].append({'id': name, 'arguments': ['resource'], 'kind': 'derived', 'type': 'boolean', 'source_refs': ['synthetic-test']})
        def resource(name, symbolic=False):
            return {'predicate': name, 'args': [{'param': 'resource'}] if symbolic else ['r-1']}
        cat['derived_rules'] = [
            {'id': 'second', 'parameters': {'resource': 'resource'}, 'head': resource('done', True),
             'when': {'test': {'fact': resource('positive', True), 'op': 'eq', 'value': True}}, 'source_refs': ['synthetic-test']},
            {'id': 'first', 'parameters': {'resource': 'resource'}, 'head': resource('positive', True),
             'when': {'test': {'fact': resource('processed', True), 'op': 'ge', 'value': 3, 'unit': 'count'}}, 'source_refs': ['synthetic-test']}]
        req['goal'] = {'test': {'fact': resource('done'), 'op': 'eq', 'value': True}}
        rebind(cat, req)
        seq = sequence(cat, req, [])
        seq['steps'] = [{'activity_id': 'transfer', 'activity_version': '1.0', 'parameters': {'resource': 'r-1'}}]
        report = verify_sequence(cat, req, seq)
        self.assertEqual(report['verification_status'], 'passed')
        self.assertEqual([r['rule_id'] for r in report['initial_derivations']], ['first', 'second'])
        self.assertIs(report['initial_derivations'][-1]['check']['value'], False)
        self.assertIs(report['trace'][0]['derived_checks'][-1]['check']['value'], True)

    def test_full_three_valued_truth_tables(self):
        cases = [(True, True, True, True), (True, False, False, True), (True, None, None, True),
                 (False, True, False, True), (False, False, False, False), (False, None, False, None),
                 (None, True, None, True), (None, False, False, None), (None, None, None, None)]
        for left, right, conjunction, disjunction in cases:
            for operator_name, expected in [('all', conjunction), ('any', disjunction),
                                            ('not', None if conjunction is None else not conjunction)]:
                cat, req = fixture()
                req['initial_state'][0]['value'], req['initial_state'][1]['value'] = left, right
                children = [test_condition('collected'), test_condition('approved')]
                req['goal'] = {'not': {'all': children}} if operator_name == 'not' else {operator_name: children}
                report = verify_sequence(cat, req, sequence(cat, req, []))
                with self.subTest(left=left, right=right, operator=operator_name):
                    self.assertIs(report['final_checks']['goal']['value'], expected)
                    self.assertEqual(report['verification_status'], 'passed' if expected is True else 'rejected' if expected is False else 'blocked_unknown')

    def test_trace_contains_conditions_effects_derivations_and_sources(self):
        cat, req = fixture()
        report = verify_sequence(cat, req, sequence(cat, req, ['collect', 'approve', 'publish_standard']))
        publish = report['trace'][2]
        self.assertEqual(publish['preconditions']['evidence'][0]['derived_rule_id'], 'ready-if-collected-and-approved')
        self.assertTrue(publish['effects'])
        self.assertTrue(publish['state_delta'])
        self.assertTrue(publish['source_refs'])
        self.assertTrue(report['initial_derivations'])
        self.assertEqual(report['input_sources']['catalog'], cat['source_refs'])
        self.assertEqual(report, read_json(ROOT / 'docs/task-flow/phase4/verified-example.json'))
        self.assertEqual(report, verify_sequence(cat, req, read_json(EXAMPLES / 'sequence-basic.json')))

    def test_replay_does_not_mutate_inputs(self):
        cat, req = fixture()
        seq = sequence(cat, req, ['collect', 'approve', 'publish_backup'])
        before = deepcopy((cat, req, seq))
        report = verify_sequence(cat, req, seq)
        report['trace'][0]['source_refs'].clear()
        self.assertEqual((cat, req, seq), before)

    def test_forged_final_state_capabilities_identity_and_goal_claim(self):
        cat, req = fixture()
        candidate = SearchSession(prepare_problem(cat, req)).run()['candidates'][0]
        for field, expected in [('final_state', 'claimed_final_state'), ('capability_demands', 'claimed_capabilities'),
                                ('candidate_id', 'candidate_identity'), ('generation_goal_satisfied', 'claimed_goal')]:
            changed = deepcopy(candidate)
            changed[field] = 'wrong' if field == 'candidate_id' else False if field == 'generation_goal_satisfied' else []
            report = verify_candidate(cat, req, changed)
            with self.subTest(field=field):
                self.assertEqual(report['verification_status'], 'rejected')
                self.assertEqual(report['failure']['code'], expected)

    def test_boolean_int_claim_mismatch_is_not_hidden_by_python_equality(self):
        cat, req = fixture()
        candidate = SearchSession(prepare_problem(cat, req)).run()['candidates'][0]
        candidate['final_state'][0]['value'] = 1
        self.assertEqual(verify_candidate(cat, req, candidate)['verification_status'], 'rejected')

    def test_passed_flag_does_not_bypass_replay_or_certify_external_work(self):
        cat, req = fixture()
        candidate = SearchSession(prepare_problem(cat, req)).run()['candidates'][0]
        candidate['verification_status'] = candidate['external_status'] = 'passed'
        good = verify_candidate(cat, req, candidate)
        self.assertEqual(good['verification_status'], 'passed')
        self.assertEqual(good['external_status'], 'not_run')
        candidate['sequence']['steps'] = [instance('approve')]
        self.assertEqual(verify_candidate(cat, req, candidate)['verification_status'], 'rejected')

    def test_malformed_candidate_envelope_returns_structured_rejection(self):
        cat, req = fixture()
        report = verify_candidate(cat, req, {})
        self.assertEqual(report['format_version'], 'task-flow-verification-1.0')
        self.assertEqual(report['failure']['code'], 'candidate_shape')

    def test_batch_binding_snapshot_duplicate_and_malformed_headers(self):
        cat, req = fixture()
        source = SearchSession(prepare_problem(cat, req)).run()
        for mode in ('digest', 'snapshot', 'duplicate', 'null_snapshot'):
            changed = deepcopy(source)
            if mode == 'digest':
                changed['request_sha256'] = '0' * 64
            elif mode == 'snapshot':
                changed['input_snapshot']['request']['request_id'] = 'changed'
            elif mode == 'duplicate':
                changed['candidates'] *= 2
            else:
                changed['input_snapshot'] = None
            with self.subTest(mode=mode):
                report = verify_search_report(cat, req, changed)
                self.assertEqual(report['verification_status'], 'rejected')
                self.assertEqual(report['checked_count'], 0)

    def test_batch_limits_keep_checked_results_without_claiming_completeness(self):
        cat, req = fixture()
        req['search']['mode'] = 'enumerate_bounded'
        source = SearchSession(prepare_problem(cat, req)).run()
        partial = verify_search_report(cat, req, source, max_candidates=1)
        self.assertEqual(partial['verification_status'], 'limit_reached')
        self.assertEqual(partial['coverage'], 'partial')
        self.assertEqual(partial['counts'], {'passed': 1})
        traces = verify_search_report(cat, req, source, max_trace_records=1)
        self.assertEqual(traces['verification_status'], 'limit_reached')
        self.assertEqual(traces['coverage'], 'partial')
        self.assertFalse(traces['independent_verification_executed'])

    def test_trace_budget_is_not_semantic_rejection(self):
        cat, req = fixture()
        report = verify_sequence(cat, req, sequence(cat, req, ['collect', 'approve', 'publish_backup']), max_trace_records=1)
        self.assertEqual(report['verification_status'], 'limit_reached')
        self.assertFalse(report['independent_verification_executed'])
        self.assertEqual(report['trace'], [])

    def test_mixed_batch_records_valid_and_invalid_separately(self):
        cat, req = fixture()
        source = SearchSession(prepare_problem(cat, req)).run()
        bad = deepcopy(source['candidates'][0])
        bad['sequence']['steps'] = [instance('approve')]
        bad['candidate_id'] = 'flow-' + canonical_sha256(bad['sequence'])
        source['candidates'].append(bad)
        report = verify_search_report(cat, req, source)
        self.assertEqual(report['counts'], {'passed': 1, 'rejected': 1})
        self.assertEqual(report['verification_status'], 'rejected')

    def test_invalid_catalog_and_verifier_budget_do_not_become_passed(self):
        cat, req = fixture()
        seq = sequence(cat, req, [])
        for budget in (False, 0, 1000001):
            with self.subTest(budget=budget), self.assertRaises(ContractError):
                verify_sequence(cat, req, seq, max_trace_records=budget)
        del cat['activities'][0]['source_refs']
        with self.assertRaises(ContractError):
            verify_sequence(cat, req, seq)

    def test_report_parser_accepts_only_finite_metadata_floats(self):
        cat, req = fixture()
        source = SearchSession(prepare_problem(cat, req)).run()
        source['statistics']['elapsed_ms'] = 1.25
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.json'
            path.write_text(json.dumps(source), encoding='utf-8')
            with self.assertRaises(ContractError):
                read_json(path)
            parsed = read_json(path, allow_finite_floats=True)
            self.assertEqual(verify_search_report(cat, req, parsed)['verification_status'], 'passed')
            parsed['input_snapshot']['request']['search']['max_nodes'] = 1.0
            self.assertEqual(verify_search_report(cat, req, parsed)['verification_status'], 'rejected')
            path.write_text('{"elapsed":NaN}', encoding='utf-8')
            with self.assertRaises(ContractError):
                read_json(path, allow_finite_floats=True)

    def test_cli_plan_verify_and_reverify_saved_report(self):
        base = [sys.executable, '-B', '-m', 'task_flow']
        inputs = ['--catalog', str(EXAMPLES / 'catalog.json'), '--request', str(EXAMPLES / 'basic.json')]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'verified-plan.json'
            planned = subprocess.run(base + ['plan'] + inputs + ['--verify', '--output', str(output)], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(planned.returncode, 0, planned.stderr or planned.stdout)
            source = json.loads(output.read_text(encoding='utf-8'))
            self.assertTrue(source['independent_verification_executed'])
            self.assertEqual(source['candidates'][0]['verification_status'], 'passed')
            checked = subprocess.run(base + ['verify'] + inputs + ['--report', str(output)], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(checked.returncode, 0, checked.stderr or checked.stdout)
            self.assertEqual(json.loads(checked.stdout)['verification_status'], 'passed')

    def test_cli_sequence_rejection_unknown_and_new_output_protection(self):
        cat, req = fixture()
        inputs = ['--catalog', str(EXAMPLES / 'catalog.json'), '--request', str(EXAMPLES / 'basic.json')]
        base = [sys.executable, '-B', '-m', 'task_flow', 'verify']
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'sequence.json'
            output = Path(folder) / 'checked.json'
            source.write_text(json.dumps(sequence(cat, req, ['approve'])), encoding='utf-8')
            command = base + inputs + ['--sequence', str(source), '--output', str(output)]
            bad = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(bad.returncode, 1)
            self.assertEqual(json.loads(bad.stdout)['verification_status'], 'rejected')
            before = output.read_bytes()
            again = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(again.returncode, 2)
            self.assertEqual(output.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
