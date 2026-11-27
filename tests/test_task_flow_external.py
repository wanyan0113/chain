"""Finite nonmilitary integration and context-bound feedback acceptance tests."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from task_flow.contracts import ContractError, canonical_sha256, prepare_problem, read_json
from task_flow.external import (FeedbackStore, IntegrationSession, analyze_candidate,
                                prepare_external)
from task_flow.planner import SearchSession
from tests.test_task_flow_contracts import EXAMPLES, ROOT


def inputs(name='feedback_fallback'):
    cat, req = read_json(EXAMPLES / 'catalog.json'), read_json(EXAMPLES / (name + '.json'))
    return (prepare_problem(cat, req), read_json(EXAMPLES / 'service_mapping.json'),
            read_json(EXAMPLES / 'services_backup_offline.json'))


def first(problem):
    return SearchSession(problem).run()['candidates'][0]


class ExternalIntegrationTests(unittest.TestCase):
    def test_rejected_first_flow_resumes_to_backup_and_preserves_evidence(self):
        problem, mapping, context = inputs()
        report = IntegrationSession(problem, mapping, context).run()
        self.assertEqual(report['integration_status'], 'selected')
        self.assertEqual(len(report['attempts']), 2)
        rejected, accepted = report['attempts']
        self.assertEqual(rejected['analysis']['external_status'], 'rejected')
        self.assertEqual(accepted['analysis']['external_status'], 'passed')
        self.assertEqual(rejected['analysis']['failures'][0]['step_index'], 2)
        self.assertEqual(rejected['candidate']['sequence']['steps'][-1]['activity_id'], 'publish_backup')
        self.assertEqual(accepted['candidate']['sequence']['steps'][-1]['activity_id'], 'publish_standard')
        for item in report['attempts']:
            analysis = item['analysis']
            self.assertEqual(analysis['verification']['verification_status'], 'passed')
            for step, expected in zip(analysis['steps'], item['candidate']['sequence']['steps']):
                self.assertEqual(step['instance'], expected)
                self.assertTrue(step['source_refs'])
                self.assertTrue(step['demands'][0]['capability_source_refs'])
                self.assertTrue(step['demands'][0]['mapping']['source_refs'])
                self.assertTrue(step['demands'][0]['service_snapshot']['source_refs'])
        self.assertFalse(report['execution_confirmed'])
        self.assertEqual(report['legacy_integration_status'], 'not_implemented')

    def test_lengths_zero_one_two_three_four_pass_generic_adapter(self):
        for name, length in [('already_done', 0), ('mandatory_verify', 1), ('collected', 2),
                             ('basic', 3), ('verified', 4)]:
            with self.subTest(name=name):
                problem, mapping, context = inputs(name)
                for service in context['services']:
                    service['available'] = True
                report = IntegrationSession(problem, mapping, context).run()
                self.assertEqual(report['integration_status'], 'selected')
                self.assertEqual(len(report['attempts'][0]['analysis']['steps']), length)

    def test_same_context_reuses_only_exact_candidate_rejection(self):
        problem, mapping, context = inputs()
        store = FeedbackStore()
        initial = IntegrationSession(problem, mapping, context, feedback=store).run()
        replayed = IntegrationSession(problem, mapping, context, feedback=store).run()
        self.assertEqual(initial['selected_candidate_id'], replayed['selected_candidate_id'])
        self.assertTrue(replayed['attempts'][0]['feedback_reused'])
        self.assertFalse(replayed['attempts'][1]['feedback_reused'])

    def test_changed_availability_same_context_label_invalidates_rejection(self):
        problem, mapping, context = inputs()
        store = FeedbackStore()
        old = IntegrationSession(problem, mapping, context, feedback=store).run()
        for service in context['services']:
            service['available'] = True
        new = IntegrationSession(problem, mapping, context, feedback=store).run()
        self.assertEqual(new['selected_candidate_id'], old['attempts'][0]['candidate']['candidate_id'])
        self.assertFalse(new['attempts'][0]['feedback_reused'])
        self.assertNotEqual(new['attempts'][0]['analysis']['binding']['context_sha256'],
                            old['attempts'][0]['analysis']['binding']['context_sha256'])

    def test_mapping_changes_and_analyzer_version_invalidate_cache(self):
        problem, mapping, context = inputs()
        store = FeedbackStore()
        IntegrationSession(problem, mapping, context, feedback=store).run()
        changed = deepcopy(mapping)
        changed['version'] = '2.0'
        self.assertFalse(IntegrationSession(problem, changed, context, feedback=store).run()['attempts'][0]['feedback_reused'])
        with patch('task_flow.external.ANALYZER', {'id': 'document-service-availability', 'version': '2.0'}):
            self.assertFalse(IntegrationSession(problem, mapping, context, feedback=store).run()['attempts'][0]['feedback_reused'])

    def test_catalog_and_request_are_part_of_feedback_scope(self):
        problem, mapping, context = inputs()
        store = FeedbackStore()
        IntegrationSession(problem, mapping, context, feedback=store).run()
        for which in ('request', 'catalog'):
            cat, req = deepcopy(problem.catalog), deepcopy(problem.request)
            if which == 'request':
                req['request_id'] += '-new'
            else:
                cat['version'] = req['catalog_version'] = '1.1'
                req['catalog_sha256'] = canonical_sha256(cat)
            changed = prepare_problem(cat, req)
            self.assertFalse(IntegrationSession(changed, mapping, context, feedback=store).run()['attempts'][0]['feedback_reused'])

    def test_missing_mapping_is_not_logic_failure_and_retains_original_demands(self):
        problem, mapping, context = inputs()
        mapping['entries'] = []
        candidate = first(problem)
        result = analyze_candidate(problem, candidate, mapping, context)
        self.assertEqual(result['external_status'], 'unsupported_mapping')
        self.assertEqual(result['verification']['verification_status'], 'passed')
        self.assertTrue(result['steps'][0]['demands'][0]['capability_uri'])
        report = IntegrationSession(problem, mapping, context).run()
        self.assertEqual(report['conclusion'], 'undetermined')

    def test_legacy_backend_explicitly_unsupported_even_empty_sequence(self):
        for name in ('already_done', 'collected', 'basic', 'verified'):
            problem, mapping, context = inputs(name)
            mapping['backend'] = 'legacy-design'
            candidate = first(problem)
            result = analyze_candidate(problem, candidate, mapping, context)
            self.assertEqual(result['external_status'], 'unsupported_mapping')
            self.assertFalse(result['external_check_executed'])
            self.assertEqual(len(result['steps']), len(candidate['sequence']['steps']))

    def test_missing_and_unknown_availability_never_mean_unavailable(self):
        for missing in (True, False):
            problem, mapping, context = inputs()
            if missing:
                context['services'] = []
            else:
                for service in context['services']:
                    service['available'] = 'unknown'
            report = IntegrationSession(problem, mapping, context).run()
            self.assertEqual(report['conclusion'], 'undetermined')
            self.assertTrue(all(a['analysis']['external_status'] == 'blocked_unknown' for a in report['attempts']))

    def test_unknown_and_unsupported_results_are_not_reused_as_rejections(self):
        for status in ('unknown', 'unsupported'):
            problem, mapping, context = inputs()
            if status == 'unknown':
                context['services'] = []
            else:
                mapping['entries'] = []
            store = FeedbackStore()
            IntegrationSession(problem, mapping, context, feedback=store).run()
            report = IntegrationSession(problem, mapping, context, feedback=store).run()
            self.assertTrue(report['attempts'])
            self.assertFalse(any(a['feedback_reused'] for a in report['attempts']))

    def test_feedback_lookup_returns_copy_and_does_not_accept_general_scope(self):
        problem, mapping, context = inputs()
        store = FeedbackStore()
        report = IntegrationSession(problem, mapping, context, feedback=store).run()
        original = report['attempts'][0]['analysis']
        cached = store.lookup(original['binding'])
        cached['external_status'] = 'passed'
        self.assertEqual(store.lookup(original['binding'])['external_status'], 'rejected')
        broadened = deepcopy(original)
        broadened['scope'] = 'all_candidates_with_this_activity'
        with self.assertRaises(ContractError):
            store._record(broadened)

    def test_analysis_budget_resume_is_cumulative_and_does_not_skip_pending(self):
        session = IntegrationSession(*inputs())
        paused = session.run(max_checks=1)
        self.assertEqual(paused['integration_status'], 'analysis_limit')
        again = session.run(max_checks=1)
        self.assertEqual(again['attempts'], paused['attempts'])
        resumed = session.run(max_checks=2)
        self.assertEqual(resumed['integration_status'], 'selected')
        self.assertEqual(len(resumed['attempts']), 2)
        with self.assertRaises(ContractError):
            session.run(max_checks=1)

    def test_exhausted_rejections_distinct_from_search_and_analysis_limits(self):
        problem, mapping, context = inputs()
        for service in context['services']:
            service['available'] = False
        exhausted = IntegrationSession(problem, mapping, context).run()
        self.assertEqual(exhausted['integration_status'], 'search_exhausted')
        self.assertEqual(exhausted['conclusion'], 'no_supported_flow_in_declared_bounds_and_context')
        self.assertEqual(exhausted['search']['conclusion'], 'candidates_available')
        session = IntegrationSession(problem, mapping, context)
        limited = session.run(max_checks=1)
        self.assertEqual(limited['integration_status'], 'analysis_limit')
        self.assertEqual(limited['conclusion'], 'undetermined')
        resumed = session.run(max_checks=100)
        self.assertEqual(resumed['integration_status'], 'search_exhausted')
        self.assertEqual(len({a['candidate']['candidate_id'] for a in resumed['attempts']}), len(resumed['attempts']))
        cat, req = deepcopy(problem.catalog), deepcopy(problem.request)
        req['search']['max_nodes'] = 1
        limited = IntegrationSession(prepare_problem(cat, req), mapping, context)
        self.assertEqual(limited.run()['integration_status'], 'search_limit')
        self.assertEqual(limited.run(max_nodes=1000)['integration_status'], 'search_exhausted')

    def test_known_no_solution_and_unknown_branches_have_different_conclusions(self):
        problem, mapping, context = inputs('too_short')
        report = IntegrationSession(problem, mapping, context).run()
        self.assertEqual(report['search']['conclusion'], 'bounded_no_solution')
        self.assertEqual(report['conclusion'], 'no_supported_flow_in_declared_bounds_and_context')
        problem, mapping, context = inputs('unknown_source')
        report = IntegrationSession(problem, mapping, context).run()
        self.assertEqual(report['search']['conclusion'], 'blocked_unknown')
        self.assertEqual(report['conclusion'], 'undetermined')

    def test_enumeration_input_handles_already_generated_pending_candidates(self):
        problem, mapping, context = inputs()
        req = deepcopy(problem.request)
        req['search']['mode'] = 'enumerate_bounded'
        report = IntegrationSession(prepare_problem(problem.catalog, req), mapping, context).run()
        self.assertEqual(report['integration_status'], 'selected')
        self.assertEqual(len(report['attempts']), 2)
        self.assertEqual(report['search']['coverage'], 'complete')

    def test_tampered_final_state_and_invalid_sequence_do_not_reach_external_checks(self):
        problem, mapping, context = inputs()
        for field in ('final_state', 'sequence'):
            candidate = first(problem)
            if field == 'final_state':
                candidate[field] = []
            else:
                candidate[field]['steps'][0]['parameters'] = {'document': 'does-not-exist'}
            result = analyze_candidate(problem, candidate, mapping, context)
            self.assertEqual(result['verification']['verification_status'], 'rejected')
            self.assertEqual(result['external_status'], 'not_run')
            self.assertFalse(result['external_check_executed'])

    def test_claimed_passed_flags_are_not_trusted(self):
        problem, mapping, context = inputs()
        candidate = first(problem)
        candidate['verification_status'] = candidate['external_status'] = 'passed'
        candidate['capability_demands'] = []
        result = analyze_candidate(problem, candidate, mapping, context)
        self.assertEqual(result['verification']['verification_status'], 'rejected')
        self.assertEqual(result['external_status'], 'not_run')

    def test_inputs_results_and_cache_are_owned_copies(self):
        problem, mapping, context = inputs()
        session = IntegrationSession(problem, mapping, context)
        context['services'] = []
        mapping['entries'] = []
        report = session.run()
        selected = report['selected_candidate_id']
        report['attempts'].clear()
        report['input_snapshot']['context']['services'].clear()
        again = session.run()
        self.assertEqual(again['selected_candidate_id'], selected)
        self.assertEqual(len(again['attempts']), 2)
        self.assertTrue(again['input_snapshot']['context']['services'])

    def test_strict_contracts_reject_ambiguous_or_unversioned_snapshots(self):
        _, mapping, context = inputs()
        mutations = [lambda m,c: c.update(extra=True), lambda m,c: c.pop('version'),
                     lambda m,c: c['services'][0].update(available=1),
                     lambda m,c: c['services'].append(deepcopy(c['services'][0])),
                     lambda m,c: m['entries'].append(deepcopy(m['entries'][0])),
                     lambda m,c: m['entries'][0].update(source_refs=[])]
        for mutate in mutations:
            m, c = deepcopy(mapping), deepcopy(context)
            mutate(m, c)
            with self.assertRaises(ContractError):
                prepare_external(m, c)
        for budget in (True, 0, -1, 10001):
            with self.assertRaises(ContractError):
                IntegrationSession(*inputs()).run(max_checks=budget)

    def test_cli_end_to_end_and_no_overwrite(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
            output = Path(directory) / 'result.json'
            cmd = [sys.executable, '-B', '-m', 'task_flow.integrate', '--catalog', str(EXAMPLES / 'catalog.json'),
                   '--request', str(EXAMPLES / 'feedback_fallback.json'), '--mapping', str(EXAMPLES / 'service_mapping.json'),
                   '--context', str(EXAMPLES / 'services_backup_offline.json'), '--output', str(output)]
            result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(report['integration_status'], 'selected')
            before = output.read_bytes()
            self.assertEqual(subprocess.run(cmd, cwd=ROOT, capture_output=True).returncode, 2)
            self.assertEqual(output.read_bytes(), before)

    def test_reproducible_acceptance_runner_saves_all_sixteen_reports(self):
        from workflow_tools.task_flow_integration_checks import run
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
            output = Path(directory) / 'evidence'
            result = run(output)
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(len(result['cases']), 16)
            for case in result['cases']:
                saved = json.loads((output / case['file']).read_text(encoding='utf-8'))
                self.assertTrue(saved['passed'])
                self.assertTrue(saved['report']['search']['input_snapshot'])
            with self.assertRaises(ContractError):
                run(output)


if __name__ == '__main__':
    unittest.main()
