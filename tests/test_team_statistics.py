"""Statistical meaning review; no model or real-data validity assertions."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
import zipfile

from workflow_tools.team_statistics_reference import group_levels, serial_events, paired_endpoints
from workflow_tools.audit_team_statistics import probes, protocol_draft

ROOT = Path(__file__).resolve().parents[1]


class TeamStatisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with zipfile.ZipFile(ROOT/'docs/task-flow/team-quality-semantics-evidence/full-run.zip') as archive:
            cls.report = json.loads(archive.read('delivery/team-optimization.json').decode('utf-8'))
        cls.cases, cls.fixture = probes(cls.report)

    def test_member_group_and_task_endpoint_have_separate_references(self):
        got = group_levels(self.fixture['member_group_endpoint'], ['A', 'B'])
        self.assertEqual(got['member_counts'], dict(A=8, B=8))
        self.assertEqual((got['group_correct'], got['endpoint_correct']), (9, 7))
        self.assertNotEqual(got['member_rate_product'], got['group_rate'])
        self.assertEqual(got['group_correct_despite_member_failure'], 1)
        self.assertFalse(got['causal_correction_proven'])

    def test_missing_output_or_truth_is_not_silently_failure(self):
        for level in ('group_output', 'task_endpoint'):
            rows = deepcopy(self.fixture['member_group_endpoint'])
            rows[0][level]['reference'] = None
            with self.assertRaises(ValueError): group_levels(rows, ['A', 'B'])
        rows = deepcopy(self.fixture['member_group_endpoint'])
        rows[0]['member_outputs']['A']['actual'] = None
        with self.assertRaises(ValueError): group_levels(rows, ['A', 'B'])

    def test_conditional_full_prefix_chain_rule_under_perfect_dependence(self):
        result = serial_events(self.fixture['correlated_serial'], ['s1', 's2', 's3'])
        self.assertEqual(result['conditional_product'], .8)
        self.assertEqual(result['joint_event_rate'], .8)
        self.assertNotEqual(result['marginal_product'], .8)
        self.assertEqual([x['denominator'] for x in result['conditionals']], [10, 8, 8])
        self.assertEqual(result['conditionals'][2]['conditioned_on'], ['s1', 's2'])
        self.assertFalse(result['independence_tested'])

    def test_adjacent_success_is_not_the_whole_prefix(self):
        rows = [dict(sample_id='a', events=dict(s1=False, s2=True, s3=False), endpoint=False),
                dict(sample_id='b', events=dict(s1=True, s2=True, s3=True), endpoint=True)]
        result = serial_events(rows, ['s1', 's2', 's3'])
        self.assertEqual(result['conditionals'][2]['value'], 1)
        self.assertEqual(result['conditionals'][2]['denominator'], 1)
        self.assertEqual(result['joint_event_rate'], .5)

    def test_conditional_values_do_not_establish_endpoint_equivalence(self):
        result = serial_events(self.fixture['repaired_endpoint'], ['s1', 's2', 's3'])
        self.assertEqual(result['conditional_product'], .8)
        self.assertEqual(result['endpoint_rate'], .9)
        self.assertEqual(result['endpoint_joint_disagreements'], 1)
        self.assertFalse(result['endpoint_equivalence_proven'])

    def test_zero_support_and_unknown_labels_are_not_filled(self):
        rows = [dict(sample_id='x', events=dict(s1=False, s2=True), endpoint=False)]
        got = serial_events(rows, ['s1', 's2'])
        self.assertEqual(got['joint_event_rate'], 0)
        self.assertIsNone(got['conditional_product'])
        self.assertEqual(got['zero_support_stages'], ['s2'])
        rows[0]['events']['s2'] = None
        with self.assertRaises(ValueError): serial_events(rows, ['s1', 's2'])

    def test_same_type_stages_remain_different_instances(self):
        rows = [dict(sample_id='a', events=dict(decide_B=True, decide_C=False), endpoint=False),
                dict(sample_id='b', events=dict(decide_B=True, decide_C=True), endpoint=True)]
        got = serial_events(rows, ['decide_B', 'decide_C'])
        self.assertEqual([x['value'] for x in got['marginals']], [1, .5])
        with self.assertRaises(ValueError): serial_events(rows, ['decide_B', 'decide_B'])

    def test_paired_comparison_and_enrolled_denominator(self):
        f = self.fixture
        complete = paired_endpoints(f['enrollments'], f['short_arm'], f['long_arm'])
        partial = paired_endpoints(f['enrollments'], f['short_arm'], f['missing_long_arm'])
        self.assertEqual(complete['complete_pair_rate_difference'], .1)
        self.assertFalse(complete['causal_effect_proven'])
        self.assertEqual(partial['n_enrolled'], 10)
        self.assertEqual(partial['n_complete_pairs'], 9)
        self.assertEqual(partial['missing_pairs'], ['p-9'])
        self.assertFalse(partial['coverage_complete'])

    def test_changed_population_sample_and_duplicate_pair_rejected(self):
        f = self.fixture
        for field in ('population', 'sample_id'):
            changed = deepcopy(f['long_arm'])
            changed[0][field] = 'different'
            with self.assertRaises(ValueError): paired_endpoints(f['enrollments'], f['short_arm'], changed)
        with self.assertRaises(ValueError): paired_endpoints(f['enrollments'], f['short_arm'], f['long_arm']+[f['long_arm'][0]])
        with self.assertRaises(ValueError): group_levels(f['member_group_endpoint']+[f['member_group_endpoint'][0]], ['A','B'])

    def test_observed_missing_pair_needs_a_reason(self):
        f = self.fixture
        long = deepcopy(f['long_arm'])
        long[0]['success'] = None
        with self.assertRaises(ValueError): paired_endpoints(f['enrollments'], f['short_arm'], long)
        long[0]['missing_reason'] = 'output_lost'
        result = paired_endpoints(f['enrollments'], f['short_arm'], long)
        self.assertEqual(result['n_enrolled'], 10)
        self.assertEqual(result['missing_pairs'], ['p-0'])

    def test_audit_confirms_limits_without_claiming_they_are_fixed(self):
        self.assertEqual(len(self.cases), 9)
        by_case = {r['case']: r['result'] for r in self.cases}
        self.assertTrue(by_case['same_scale_different_construct']['declaration_accepted'])
        self.assertEqual(by_case['same_scale_different_construct']['measurement_validity'], 'pending')
        self.assertFalse(by_case['conditional_basis_does_not_require_independence']['probability_validated'])

    def test_source_bound_draft_preserves_real_members_and_uncollected_status(self):
        draft = protocol_draft(self.report)
        self.assertEqual(draft['implementation_status'], 'design_draft_not_collection_interface')
        self.assertEqual(draft['data_origin'], 'uncollected')
        self.assertEqual(draft['domain_validity'], 'pending')
        stages = [s for f in draft['flows'] for s in f['stages']]
        self.assertEqual(len(stages), 8)
        self.assertTrue(any(len(s['members']) == 2 for s in stages))
        self.assertTrue(all(m['artifact_version'] == 'UNASSIGNED' for s in stages for m in s['members']))
        self.assertTrue(all(not s['measurement_object'] and not s['evaluation_method'] for s in stages))
        self.assertTrue(all(s['member_to_group_mapping']['policy'] is None for s in stages))
        repeat = deepcopy(self.report)
        repeat['run_id'] = 'another-run'
        repeat['source_report']['run_id'] = 'another-source-run'
        self.assertEqual(protocol_draft(repeat)['draft_design_id'], draft['draft_design_id'])


if __name__ == '__main__':
    unittest.main()
