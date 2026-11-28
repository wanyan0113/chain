"""Explicit team evaluation, conflict-aware selection and proof boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

from task_flow.contracts import ContractError
from task_flow.team_evaluation import scaffold, raw_metrics, source_binding
from task_flow.team_optimization import run_team_optimization
from task_flow.team_optimization_verify import verify_team_optimization
from workflow_tools.team_optimization_demo import synthetic_registry

ROOT = Path(__file__).resolve().parents[1]


class TeamOptimizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with zipfile.ZipFile(ROOT/'docs/task-flow/complementary-team-phase1-evidence/full-run.zip') as archive:
            cls.source = json.loads(archive.read('team_report.json').decode('utf-8'))
        cls.registry = synthetic_registry(cls.source)
        cls.result = run_team_optimization(cls.source, cls.registry)
        cls.check = verify_team_optimization(cls.result)  # fresh real OWL replay

    def run_cached(self, registry=None, **kwargs):
        # Only unit probes cache an already independently verified source;
        # positive integration above always runs real HermiT verification.
        with patch('task_flow.team_optimization.verify_teams', return_value=deepcopy(self.result['source_verification'])):
            return run_team_optimization(self.source, registry if registry is not None else self.registry, **kwargs)

    def verify_cached(self, result, **kwargs):
        with patch('task_flow.team_optimization_verify.verify_teams', return_value=deepcopy(self.result['source_verification'])):
            return verify_team_optimization(result, **kwargs)

    def test_actual_joint_selection_matches_independent_exhaustive_reference(self):
        self.assertEqual(self.result['status'], 'selected')
        self.assertEqual(self.result['optimality'], 'candidate_set_optimum')
        self.assertEqual(self.result['global_optimality'], 'not_proven')
        self.assertEqual(self.result['network']['score'], .6)
        self.assertEqual(self.check['status'], 'passed')
        self.assertTrue(self.check['optimality_verified'])
        self.assertEqual(self.check['reference_combinations'], 196)
        self.assertEqual(self.check['reference_feasible'], 16)
        chosen = [self.source['candidates'][cid]['plan']['steps'][1]['members'] for cid in self.result['selected_candidate_ids']]
        self.assertIn(['O-Combined'], chosen)
        self.assertIn(['O-Phase-Tuner', 'O-Safety-Auditor'], chosen)
        self.assertEqual(self.result['network']['cost_per_execution'], 25)

    def test_missing_metrics_block_without_eliminating_candidates(self):
        for kind in ('all_empty', 'record_absent', 'quality_absent', 'fusion_absent'):
            r = scaffold(self.source) if kind == 'all_empty' else deepcopy(self.registry)
            if kind == 'record_absent': r['records'].pop()
            elif kind == 'quality_absent': r['records'][0]['quality']['value'] = None
            elif kind == 'fusion_absent': r['records'][0]['fusion']['duration_s'] = None
            got = self.run_cached(r)
            self.assertEqual(got['status'], 'blocked')
            self.assertEqual(got['optimality'], 'not_run')
            self.assertNotIn('evaluation', got)
            self.assertFalse(got['selected_candidate_ids'])
            self.assertEqual(self.verify_cached(got)['status'], 'passed')

    def test_declared_group_quality_is_not_product_of_member_accuracies(self):
        raw, missing = raw_metrics(self.source, self.registry)
        self.assertFalse(missing)
        multi = next(v for c, v in raw.items() if len(self.source['candidates'][c]['plan']['steps'][1]['members']) == 2 and
                     self.source['candidates'][c]['plan']['steps'][1]['members'] == ['O-Phase-Tuner', 'O-Safety-Auditor'])
        self.assertEqual(multi['stages'][1]['accuracy'], .995)
        self.assertEqual(multi['stages'][1]['time'], 10)
        self.assertEqual(multi['stages'][1]['cost'], 5)

    def test_frozen_union_across_flows_and_equal_metric_score(self):
        pools = self.result['evaluation']['pools']
        for p in pools:
            self.assertEqual(len(p['candidate_ids']), 14)
            self.assertEqual(len({self.source['candidates'][c]['flow_id'] for c in p['candidate_ids']}), 2)
            self.assertEqual(p['extrema']['time'], dict(min=40, max=40))
            self.assertTrue(all(self.result['evaluation']['candidates'][c]['normalized']['time'] == .5 for c in p['candidate_ids']))

    def test_foreign_duplicate_misbound_and_wrong_policy_rejected(self):
        for kind in ('foreign', 'duplicate', 'assignment', 'policy', 'source'):
            r = deepcopy(self.registry)
            if kind == 'foreign': r['records'][0]['plan_key'] = 'unknown-plan'
            elif kind == 'duplicate': r['records'].append(deepcopy(r['records'][0]))
            elif kind == 'assignment': r['records'][0]['hub'] = 'R-Survey'
            elif kind == 'policy': r['chain_quality_policy'] = 'multiply_all_member_accuracies'
            else: r['binding']['candidate_set_hash'] = 'wrong'
            with self.assertRaises(ContractError, msg=kind): self.run_cached(r)

    def test_wrong_units_and_malformed_quality_rejected(self):
        for kind in ('unit', 'range', 'nan', 'origin', 'zero_member_time'):
            r = deepcopy(self.registry)
            record = r['records'][0]
            if kind == 'unit': record['fusion']['cost']['unit'] = 'other-unit'
            elif kind == 'range': record['quality']['value'] = 1.1
            elif kind == 'nan': record['quality']['value'] = float('nan')
            elif kind == 'origin': record['quality']['origin'] = 'computed_from_capability_coverage'
            else: next(iter(record['member_metrics'].values()))['duration_s'] = 0
            with self.assertRaises(ContractError, msg=kind): self.run_cached(r)

    def test_duration_mismatch_does_not_reschedule(self):
        r = deepcopy(self.registry)
        r['records'][0]['fusion']['duration_s'] += 1
        with self.assertRaisesRegex(ContractError, 'no automatic scheduling'): self.run_cached(r)

    def test_quality_and_weights_change_evaluation_not_candidate_identity(self):
        r = deepcopy(self.registry)
        r['records'][0]['quality']['value'] = .5
        got = self.run_cached(r, weights=dict(time=.2, accuracy=.5, cost=.3))
        self.assertEqual(set(got['source_report']['candidates']), set(self.source['candidates']))
        self.assertNotEqual(got['evaluation']['evaluation_context_hash'], self.result['evaluation']['evaluation_context_hash'])
        self.assertEqual(got['source_binding'], source_binding(self.source))

    def test_budgeted_incumbent_does_not_inherit_reference_proof(self):
        got = self.run_cached(max_network_nodes=6)
        self.assertEqual(got['status'], 'feasible')
        self.assertEqual(got['optimality'], 'not_proven')
        check = self.verify_cached(got)
        self.assertEqual(check['status'], 'passed')
        self.assertTrue(check['selected_matches_reference'])
        self.assertFalse(check['optimality_verified'])
        forged = deepcopy(got)
        forged['optimality'] = 'candidate_set_optimum'
        forged['network_search']['optimality'] = 'proven'
        self.assertEqual(self.verify_cached(forged)['status'], 'failed')

    def test_budget_without_incumbent_is_undetermined(self):
        got = self.run_cached(max_network_nodes=2)
        self.assertEqual(got['status'], 'undetermined')
        self.assertEqual(got['optimality'], 'not_proven')
        self.assertEqual(self.verify_cached(got)['status'], 'passed')

    def test_tampered_metrics_scores_reservations_and_claims_rejected(self):
        for kind in ('raw', 'score', 'total', 'reservation', 'source_coverage', 'global_claim', 'domain', 'relations'):
            got = deepcopy(self.result)
            cid = got['selected_candidate_ids'][0]
            if kind == 'raw': got['raw_metrics'][cid]['metrics']['accuracy'] = 1
            elif kind == 'score': got['evaluation']['candidates'][cid]['score'] = 1
            elif kind == 'total': got['network']['cost_per_execution'] = 0
            elif kind == 'reservation': got['search_scene']['artifacts'][0]['capacity'] = 999
            elif kind == 'source_coverage': got['source_candidate_search_complete'] = not got['source_candidate_search_complete']
            elif kind == 'global_claim': got['global_optimality'] = 'bounded_global_optimum'
            elif kind == 'relations': got['network']['relations'] = []
            else: got['domain_validity'] = 'passed'
            self.assertEqual(self.verify_cached(got)['status'], 'failed', kind)

    def test_reference_budget_cannot_claim_independent_optimality(self):
        got = self.verify_cached(self.result, max_reference_combinations=1)
        self.assertEqual(got['status'], 'passed')
        self.assertFalse(got['reference_complete'])
        self.assertFalse(got['optimality_verified'])

    def test_registry_order_and_inputs_are_not_mutated(self):
        source, registry = deepcopy(self.source), deepcopy(self.registry)
        registry['records'].reverse()
        got = self.run_cached(registry)
        self.assertEqual(self.source, source)
        self.assertEqual(got['selected_candidate_ids'], self.result['selected_candidate_ids'])
        self.assertEqual(got['network']['score'], self.result['network']['score'])

    def test_source_without_optional_network_node_budget(self):
        from workflow_tools.team_demo import inputs
        from task_flow.team_feasibility import run_teams
        jobs, scene, mapping, config = inputs()
        scene['policies'].pop('max_network_search_nodes')
        source = run_teams(jobs, scene, mapping, config)
        result = run_team_optimization(source, synthetic_registry(source))
        self.assertEqual(result['status'], 'selected')
        self.assertEqual(verify_team_optimization(result)['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
