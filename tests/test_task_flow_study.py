"""Study credibility tests, including negative controls and nonvacuous results."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_flow.contracts import ContractError, prepare_problem, read_json
from task_flow.planner import SearchSession
from workflow_tools.task_flow_study import (PLAN, ROOT, correctness_case, efficiency_case,
                                           model_risks, run, template_cases, ticket_model,
                                           validate_plan, verify)


def small_plan():
    plan = read_json(PLAN)
    plan['repeats'] = 1
    plan['correctness'] = plan['correctness'][:1]
    plan['efficiency'] = plan['efficiency'][:1]
    plan['template_cases'] = plan['template_cases'][:1]
    return plan


class TaskFlowStudyTests(unittest.TestCase):
    def test_template_baseline_and_planner_use_same_semantics(self):
        cases = template_cases(read_json(PLAN))
        self.assertTrue(all(c['passed'] for c in cases))
        self.assertEqual(sum(c['actual']['planner_feasible'] for c in cases), 8)
        self.assertEqual(sum(c['actual']['template_feasible'] for c in cases), 4)
        self.assertTrue(all(len(c['template_verifications']) == 4 for c in cases))
        self.assertFalse(cases[-1]['actual']['planner_feasible'])

    def test_extended_length_correctness_matches_raw_oracle_and_path_count(self):
        result = correctness_case({'depth': 4, 'width': 2, 'constraints': 2, 'max_length': 4})
        self.assertTrue(result['passed'])
        self.assertEqual(result['raw_sequence_count'], 4681)
        self.assertEqual(len(result['actual_sequences']), 16)

    def test_missing_planner_sequence_is_detected_by_independent_reference(self):
        original = SearchSession.run
        def omit(session, **kwargs):
            result = original(session, **kwargs)
            result['candidates'] = []
            return result
        with patch('workflow_tools.task_flow_study.SearchSession.run', omit):
            result = correctness_case({'depth': 2, 'width': 1, 'constraints': 0, 'max_length': 2})
        self.assertFalse(result['passed'])
        self.assertTrue(result['reference_sequences'])

    def test_schema_valid_domain_faults_do_not_get_mislabeled_detected(self):
        cases = model_risks()
        self.assertEqual(len(cases), 6)
        self.assertTrue(all(c['schema_valid'] and c['passed'] for c in cases))
        self.assertEqual([c['outcome'] for c in cases[:3]], ['semantic_contract_rejected'] * 3)
        self.assertEqual(cases[3]['outcome'], 'no_flow_without_model_error_diagnosis')
        self.assertEqual([c['outcome'] for c in cases[4:]], ['formal_pass_domain_error_not_detected'] * 2)
        for case in cases[4:]:
            self.assertTrue(any(not s['increment_is_one'] for s in case['external_manual_domain_check']))

    def test_node_limit_control_is_not_reported_as_no_solution(self):
        config = read_json(PLAN)['efficiency'][-1]
        result = efficiency_case(config, 1)
        self.assertTrue(result['passed'])
        report = result['runs'][0]['search']
        self.assertEqual(report['termination_reason'], 'node_limit')
        self.assertEqual(report['statistics']['nodes'], 2000)
        self.assertEqual(report['conclusion'], 'undetermined')

    def test_plan_rejects_vacuous_and_out_of_bounds_studies(self):
        for key in ['correctness', 'efficiency', 'fixed_templates', 'template_cases']:
            plan = small_plan()
            plan[key] = []
            with self.assertRaises(ContractError):
                validate_plan(plan)
        for count in [0, -1, True, 6]:
            plan = small_plan()
            plan['repeats'] = count
            with self.assertRaises(ContractError):
                validate_plan(plan)
        plan = small_plan()
        plan['correctness'][0].update(depth=8, width=4, max_length=8)
        with self.assertRaises(ContractError):
            validate_plan(plan)

    def test_model_generator_rejects_boolean_and_continuous_dimensions(self):
        for dimensions in [(True, 1, 0), (3, 1.5, 0), (3, 1, 33)]:
            with self.assertRaises(ContractError):
                ticket_model(*dimensions)

    def test_length_axis_keeps_activity_catalog_constant(self):
        configs = [c for c in read_json(PLAN)['efficiency'] if c['axis'] == 'required_length']
        results = [efficiency_case(c, 1) for c in configs]
        self.assertEqual({r['activity_instances'] for r in results}, {12})
        self.assertEqual(len({r['runs'][0]['search']['catalog_sha256'] for r in results}), 1)
        self.assertEqual([r['runs'][0]['search']['limits']['max_length'] for r in results], [2, 4, 6])

    def test_evidence_binds_inputs_and_detects_modified_artifacts(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            output = Path(directory)/'evidence'
            result = run(output, plan=small_plan())
            self.assertEqual(result['status'], 'passed_with_scope_limits')
            self.assertEqual(verify(output)['raw_sequences'], 3)
            cases = json.loads((output/'correctness.json').read_text(encoding='utf-8'))
            self.assertTrue(cases[0]['search']['input_snapshot'])
            with self.assertRaises(ContractError):
                run(output, plan=small_plan())
            with (output/'model-risks.json').open('a', encoding='utf-8') as stream:
                stream.write(' ')
            with self.assertRaisesRegex(ContractError, 'artifact changed'):
                verify(output)

    def test_failed_experiment_propagates_to_overall_status(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            output = Path(directory)/'evidence'
            with patch('workflow_tools.task_flow_study.correctness_case', return_value={
                    'passed': False, 'raw_sequence_count': 1, 'actual_sequences': []}):
                result = run(output, plan=small_plan())
            self.assertEqual(result['status'], 'failed')
            with self.assertRaisesRegex(ContractError, 'failed checks'):
                verify(output)

    def test_empty_manifest_inventory_cannot_claim_verification(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            output = Path(directory)/'evidence'
            run(output, plan=small_plan())
            manifest = json.loads((output/'manifest.json').read_text(encoding='utf-8'))
            for key in ['source_sha256_lf_normalized', 'artifact_sha256']:
                changed = deepcopy(manifest)
                changed[key] = {}
                (output/'manifest.json').write_text(json.dumps(changed), encoding='utf-8')
                with self.assertRaisesRegex(ContractError, 'incomplete'):
                    verify(output)


if __name__ == '__main__':
    unittest.main()
