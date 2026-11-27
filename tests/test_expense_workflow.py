"""Application-level approval policy tests; synthetic data, no external actions."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from task_flow.contracts import ContractError, prepare_problem, read_json
from task_flow.external import IntegrationSession
from task_flow.planner import SearchSession
from tests.expense_policy_reference import check_expense_policy
from workflow_tools.expense_evaluation import (EXAMPLES, ROOT, evaluate_case, policy_mutation,
                                              prepare, run, verify)
from workflow_tools.expense_workflow import adapt


def specs():
    return read_json(EXAMPLES/'cases.json')['cases']


def spec(name):
    return next(c for c in specs() if c['id'] == name)


class ExpenseWorkflowTests(unittest.TestCase):
    def test_all_fifteen_application_expectations(self):
        cases = [evaluate_case(c) for c in specs()]
        self.assertEqual(len(cases), 15)
        for case in cases:
            self.assertTrue(case['passed'], (case['id'], case['expected'], case['actual']))
            self.assertFalse(case['real_payment_executed'])
        self.assertEqual(sum(c['actual']['integration_status'] == 'selected' for c in cases), 9)

    def test_one_cent_across_threshold_adds_manager_step(self):
        boundary, above = evaluate_case(spec('threshold_exact')), evaluate_case(spec('above_threshold'))
        self.assertEqual(boundary['actual']['selected_length'], 4)
        self.assertFalse(boundary['actual']['manager_seen'])
        self.assertEqual(above['actual']['selected_length'], 5)
        self.assertTrue(above['actual']['manager_seen'])

    def test_unknown_receipt_is_distinct_from_explicit_false(self):
        unknown = evaluate_case(spec('receipt_unknown'))
        false = evaluate_case(spec('receipt_invalid'))
        self.assertEqual(unknown['actual']['search_conclusion'], 'blocked_unknown')
        self.assertEqual(false['actual']['search_conclusion'], 'bounded_no_solution')
        self.assertEqual(unknown['actual']['domain_checks_executed'], 0)

    def test_two_claims_keep_separate_parameters_and_final_goals(self):
        raw = spec('two_reviewed_claims')['input']
        prepared = adapt(raw)
        candidate = SearchSession(prepare_problem(prepared['catalog'], prepared['request'])).run()['candidates'][0]
        steps = candidate['sequence']['steps']
        self.assertEqual({s['parameters']['claim'] for s in steps}, {'claim-a', 'claim-b'})
        self.assertTrue(check_expense_policy(raw, steps)['passed'])
        aliased = deepcopy(steps)
        for step in aliased:
            step['parameters']['claim'] = 'claim-a'
        self.assertFalse(check_expense_policy(raw, aliased)['passed'])

    def test_service_failure_tries_human_review_without_global_activity_ban(self):
        result = evaluate_case(spec('automatic_service_offline'))
        attempts = result['integration']['attempts']
        self.assertEqual([a['analysis']['external_status'] for a in attempts], ['rejected', 'passed'])
        first_names = [s['activity_id'] for s in attempts[0]['candidate']['sequence']['steps']]
        second_names = [s['activity_id'] for s in attempts[1]['candidate']['sequence']['steps']]
        self.assertIn('screen_auto', first_names)
        self.assertIn('screen_manual', second_names)
        self.assertEqual(len(result['domain_checks']), 2)

    def test_raw_field_provenance_and_copies_are_preserved(self):
        raw = spec('two_reviewed_claims')['input']
        before = deepcopy(raw)
        result = adapt(raw)
        self.assertEqual(raw, before)
        self.assertEqual(len(result['field_mapping']), 14)
        self.assertEqual({m['claim_id'] for m in result['field_mapping']}, {'claim-a', 'claim-b'})
        self.assertTrue(all(m['source_refs'] and m['raw_sha256'] == result['raw_sha256'] for m in result['field_mapping']))
        raw['claims'][0]['state']['submitted'] = False
        self.assertTrue(next(v for v in result['request']['initial_state'] if v['fact'] == {'predicate': 'submitted', 'args': ['claim-a']})['value'])

    def test_invalid_business_records_reject_before_planning(self):
        original = spec('low_archived')['input']
        mutations = [lambda d: d.update(currency='USD'),
                     lambda d: d['claims'][0].update(amount_cents=True),
                     lambda d: d['claims'][0].update(amount_cents=1.25),
                     lambda d: d['claims'][0].update(amount_cents=0),
                     lambda d: d['claims'][0]['state'].update(screened=True),
                     lambda d: d['claims'][0].update(receipt_valid='unknown'),
                     lambda d: d['claims'].append(deepcopy(d['claims'][0])),
                     lambda d: d['goal'].update(claim_ids=[]),
                     lambda d: d['goal'].update(claim_ids=['missing']),
                     lambda d: d['search'].update(max_nodes=True)]
        for mutation in mutations:
            raw = deepcopy(original)
            mutation(raw)
            with self.assertRaises(ContractError):
                adapt(raw)

    def test_omitted_numeric_policy_passes_formal_model_but_not_independent_monitor(self):
        result = policy_mutation(read_json(EXAMPLES/'sample.json'))
        self.assertTrue(result['passed'])
        self.assertEqual(result['formal_verification']['verification_status'], 'passed')
        self.assertFalse(result['independent_domain_check']['passed'])
        self.assertEqual(result['independent_domain_check']['reason'], 'high amount requires manager approval')

    def test_domain_monitor_does_not_call_model_execution(self):
        raw = spec('above_threshold')['input']
        prepared = adapt(raw)
        steps = SearchSession(prepare_problem(prepared['catalog'], prepared['request'])).run()['candidates'][0]['sequence']['steps']
        bad = Mock(side_effect=AssertionError('model execution called by business monitor'))
        with patch('task_flow.validator.verify_candidate', bad), patch('task_flow.state.apply_activity', bad), patch('task_flow.planner.SearchSession.run', bad):
            self.assertTrue(check_expense_policy(raw, steps)['passed'])
        bad.assert_not_called()

    def test_prepared_files_work_through_existing_integration_cli(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            output = Path(directory)/'prepared'
            prepare(EXAMPLES/'sample.json', output)
            cmd = [sys.executable, '-B', '-m', 'task_flow.integrate']
            for name in ['catalog', 'request', 'mapping', 'context']:
                cmd += ['--'+name, str(output/(name+'.json'))]
            result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertEqual(json.loads(result.stdout)['integration_status'], 'selected')
            trace = read_json(output/'input-trace.json')
            self.assertFalse(trace['real_payment_executed'])
            with self.assertRaises(ContractError):
                prepare(EXAMPLES/'sample.json', output)

    def test_empty_or_ambiguous_experiment_cannot_report_success(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            for suite in [{'format_version': 'expense-study-cases-1.0', 'cases': []},
                          {'format_version': 'expense-study-cases-1.0', 'cases': [spec('low_archived')]*2}]:
                with self.assertRaises(ContractError):
                    run(Path(directory)/'evidence', suite=suite)

    def test_failed_expectations_propagate_and_evidence_mutations_are_detected(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            good = {'format_version': 'expense-study-cases-1.0', 'cases': [spec('low_archived')]}
            output = Path(directory)/'good'
            self.assertEqual(run(output, suite=good)['status'], 'passed_with_scope_limits')
            verify(output)
            manifest = read_json(output/'manifest.json')
            changed = deepcopy(manifest)
            changed['artifact_sha256'] = {}
            (output/'manifest.json').write_text(json.dumps(changed), encoding='utf-8')
            with self.assertRaisesRegex(ContractError, 'incomplete artifact'):
                verify(output)
            (output/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with (output/'case-00.json').open('a', encoding='utf-8') as stream:
                stream.write(' ')
            with self.assertRaisesRegex(ContractError, 'artifact changed'):
                verify(output)
            bad = deepcopy(good)
            bad['cases'][0]['expected']['selected_length'] = 99
            failed = Path(directory)/'failed'
            self.assertEqual(run(failed, suite=bad)['status'], 'failed')
            with self.assertRaisesRegex(ContractError, 'evaluation failed'):
                verify(failed)


if __name__ == '__main__':
    unittest.main()
