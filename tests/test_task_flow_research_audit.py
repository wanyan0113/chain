"""Evidence harness integrity, failure propagation and bounded audit scope."""
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow_tools import task_flow_research_audit as audit


class ResearchAuditTests(unittest.TestCase):
    @contextmanager
    def environment(self, cases):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'source.py'
            source.write_text('value = 1\n', encoding='utf-8')
            with patch.object(audit, 'ROOT', root), patch.object(audit, 'source_files', return_value=[source]), \
                    patch.object(audit, 'build_cases', return_value=cases), \
                    patch.object(audit.subprocess, 'check_output', return_value='frozen-test-baseline\n'), redirect_stdout(StringIO()):
                yield root, root / 'evidence', source

    def case(self, expected, probe):
        return audit.Case('C00', 'harness fixture', ['R03'], {'known_input': True}, expected, probe)

    def test_boolean_and_integer_results_do_not_falsely_match(self):
        with self.environment([self.case({'value': True}, lambda data: {'value': 1})]) as (_, output, _):
            report = audit.run(output)
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['failed'], 1)
            self.assertEqual(audit.verify(output)['status'], 'failed')

    def test_probe_exception_preserves_failed_evidence(self):
        def broken(data):
            raise RuntimeError('injected audit failure')
        with self.environment([self.case({'value': True}, broken)]) as (_, output, _):
            report = audit.run(output)
            self.assertEqual(report['status'], 'failed')
            record = audit.json.loads((output / 'cases/C00.json').read_text(encoding='utf-8'))
            self.assertEqual(record['actual']['exception'], 'RuntimeError')
            self.assertEqual(record['inputs'], {'known_input': True})

    def test_manifest_detects_changed_and_unexpected_artifacts(self):
        with self.environment([self.case({'value': True}, lambda data: {'value': True})]) as (_, output, _):
            audit.run(output)
            self.assertEqual(audit.verify(output)['status'], 'passed_with_scope_limits')
            path = output / 'acceptance.md'
            original = path.read_bytes()
            path.write_bytes(original + b'changed')
            with self.assertRaisesRegex(ValueError, 'digest mismatch'):
                audit.verify(output)
            path.write_bytes(original)
            (output / 'unexpected.txt').write_text('extra', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'inventory differs'):
                audit.verify(output)

    def test_source_changes_invalidate_current_source_comparison(self):
        with self.environment([self.case({'value': True}, lambda data: {'value': True})]) as (_, output, source):
            audit.run(output)
            source.write_text('value = 2\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'current source differs'):
                audit.verify(output)

    def test_existing_output_is_not_overwritten(self):
        with self.environment([]) as (_, output, _):
            output.mkdir()
            marker = output / 'keep.txt'
            marker.write_text('preserve', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'must not exist'):
                audit.run(output)
            self.assertEqual(marker.read_text(encoding='utf-8'), 'preserve')

    def test_effective_model_and_sequence_inputs_are_saved(self):
        catalog, request = audit.fixture()
        def probe(data):
            changed = audit.deepcopy(request)
            changed['initial_state'][0]['value'] = True
            sequence = audit.seq(catalog, changed, ['approve', 'publish_standard'])
            report = audit.verify_sequence(catalog, changed, sequence)
            return {'status': report['verification_status']}
        with self.environment([self.case({'status': 'passed'}, probe)]) as (_, output, _):
            audit.run(output)
            record = audit.json.loads((output / 'cases/C00.json').read_text(encoding='utf-8'))
            calls = record['effective_inputs_and_calls']
            self.assertEqual(len(calls['models']), 1)
            self.assertEqual(len(calls['subjects']), 1)
            model = next(iter(calls['models'].values()))
            self.assertIs(model['request']['initial_state'][0]['value'], True)
            self.assertEqual(calls['calls'][0]['outcome']['verification_status'], 'passed')

    def test_model_count_cannot_make_audit_vacuously_pass(self):
        for count in (0, -1, True, 129):
            with self.subTest(count=count), self.assertRaises(ValueError):
                audit.mixed_models(count)

    def test_full_search_statistics_and_termination_are_retained(self):
        catalog, request = audit.fixture()
        def probe(data):
            report = audit.SearchSession(audit.prepare_problem(catalog, request)).run()
            return {'conclusion': report['conclusion']}
        with self.environment([self.case({'conclusion': 'candidates_available'}, probe)]) as (_, output, _):
            audit.run(output)
            record = audit.json.loads((output / 'cases/C00.json').read_text(encoding='utf-8'))
            saved = record['effective_inputs_and_calls']['search_runs'][0]['report']
            self.assertEqual(saved['search_status'], 'candidate_found')
            self.assertEqual(saved['termination_reason'], 'first_feasible_pause')
            self.assertGreater(saved['statistics']['nodes'], 0)
            self.assertIn('elapsed_ms', saved['statistics'])
            self.assertTrue(saved['candidates'])
            self.assertEqual(saved['input_snapshot']['request'], request)

    def test_frozen_case_inventory_and_declared_pending_requirements(self):
        cases = audit.build_cases(model_count=1)
        self.assertEqual(len(cases), 23)
        self.assertEqual(len({case.id for case in cases}), 23)
        self.assertTrue(all(case.inputs and case.expected and case.requirements for case in cases))
        boundary = next(case for case in cases if case.id == 'C19')
        self.assertEqual(boundary.expected['external'], 'not_run')


if __name__ == '__main__':
    unittest.main()
