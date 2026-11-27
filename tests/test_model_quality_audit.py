"""Model-quality evidence must distinguish counterexamples from lack of evidence."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_flow.contracts import ContractError, canonical_sha256, read_json
from workflow_tools.expense_workflow import adapt
from workflow_tools.model_quality_audit import (ROOT, EXPENSE, EFFICIENCY, audit_expense_model,
                                                capability_semantics_probe, efficiency_rows,
                                                quality_probes, run, verify)


class ModelQualityAuditTests(unittest.TestCase):
    def test_thirteen_probes_report_specific_outcomes_not_blanket_validity(self):
        records = quality_probes()
        self.assertEqual(len(records), 13)
        self.assertTrue(all(p['expectation_met'] for p in records), [(p['id'], p['actual_quality_status']) for p in records])
        counts = {s: sum(p['actual_quality_status'] == s for p in records) for s in
                  ['no_counterexample_within_bounds', 'counterexample_found', 'invalid_model_contract', 'inconclusive']}
        self.assertEqual(counts, {'no_counterexample_within_bounds': 2, 'counterexample_found': 5,
                                  'invalid_model_contract': 3, 'inconclusive': 3})

    def test_zero_workflow_and_unknown_are_not_model_quality_certificates(self):
        records = {p['id']: p['report'] for p in quality_probes()}
        for name in ['contradictory_precondition', 'unknown_receipt', 'limited_search']:
            self.assertEqual(records[name]['quality_status'], 'inconclusive')
            self.assertEqual(records[name]['counterexamples'], [])
        self.assertEqual(records['contradictory_precondition']['reason'], 'no_certified_workflow_is_not_model_quality_proof')
        self.assertEqual(records['limited_search']['reason'], 'search_not_complete')

    def test_candidate_budget_exhaustion_does_not_certify_whole_model(self):
        raw = read_json(EXPENSE/'sample.json')
        prepared = adapt(raw)
        result = audit_expense_model(raw, prepared['catalog'], prepared['request'], max_candidates=1)
        self.assertEqual(result['quality_status'], 'inconclusive')
        self.assertEqual(result['search']['termination_reason'], 'candidate_limit')
        self.assertEqual(len(result['candidate_checks']), 1)

    def test_incomplete_request_reports_contract_error_instead_of_crashing(self):
        raw = read_json(EXPENSE/'sample.json')
        prepared = adapt(raw)
        prepared['request'].pop('search')
        result = audit_expense_model(raw, prepared['catalog'], prepared['request'])
        self.assertEqual(result['quality_status'], 'invalid_model_contract')
        self.assertEqual(result['error_code'], 'input_incomplete')

    def test_counterexamples_are_independently_formally_certified(self):
        for probe in quality_probes():
            report = probe['report']
            if report['quality_status'] == 'counterexample_found':
                ids = {c['candidate_id']: c for c in report['candidate_checks']}
                self.assertTrue(report['counterexamples'])
                for example in report['counterexamples']:
                    self.assertEqual(ids[example['candidate_id']]['formal']['verification_status'], 'passed')
                    self.assertFalse(ids[example['candidate_id']]['domain']['passed'])

    def test_faulty_domain_checker_still_exposes_a_common_error_blind_spot(self):
        raw = read_json(EXPENSE/'sample.json')
        prepared = adapt(raw)
        catalog = prepared['catalog']
        catalog['derived_rules'][0]['when'] = {'test': {'fact': {'predicate': 'screened', 'args': [{'param': 'claim'}]}, 'op': 'eq', 'value': True}}
        prepared['request']['catalog_sha256'] = canonical_sha256(catalog)
        with patch('workflow_tools.model_quality_audit.check_expense_policy', return_value={'passed': True, 'trace': [], 'reason': None}):
            result = audit_expense_model(raw, catalog, prepared['request'])
        self.assertEqual(result['quality_status'], 'no_counterexample_within_bounds')
        self.assertEqual(result['business_requirements_complete'], 'not_established')

    def test_declared_request_is_preserved_and_effective_enumeration_is_explicit(self):
        raw = read_json(EXPENSE/'sample.json')
        prepared = adapt(raw)
        before = deepcopy(prepared)
        result = audit_expense_model(raw, prepared['catalog'], prepared['request'])
        self.assertEqual(prepared, before)
        self.assertEqual(result['declared_request']['search']['mode'], 'first_feasible')
        self.assertEqual(result['effective_request']['search']['mode'], 'enumerate_bounded')

    def test_many_to_many_capabilities_are_conjunctive_and_preserve_sources(self):
        result = capability_semantics_probe()
        self.assertTrue(result['expectation_met'])
        first = result['positive']['steps'][0]
        self.assertEqual({d['capability_uri'] for d in first['demands']}, {'urn:expense:Submit', 'urn:expense:Archive'})
        for demand in first['demands']:
            self.assertTrue(demand['capability_source_refs'])
            self.assertTrue(demand['mapping']['source_refs'])
        self.assertEqual(result['positive']['verification']['verification_status'], 'passed')
        self.assertTrue(result['request']['forbidden_conditions'])
        self.assertEqual(result['missing_mapping']['external_status'], 'unsupported_mapping')
        self.assertEqual(result['one_capability_unavailable']['external_status'], 'rejected')

    def test_first_solution_metrics_do_not_pretend_to_be_enumeration_rates(self):
        data = read_json(EFFICIENCY, allow_finite_floats=True)
        rows = efficiency_rows(data)
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(r['enumeration_completion_rate'] is None for r in rows))
        self.assertTrue(all(r['candidate_counts'] == [1, 1, 1] for r in rows[:-1]))
        self.assertEqual(rows[-1]['candidate_counts'], [0, 0, 0])
        self.assertEqual(rows[-1]['first_solution_depths'], [None, None, None])
        controlled = deepcopy(data[:1])
        for r, t in zip(controlled[0]['runs'], [1, 2, 9]):
            r['search_call_wall_ms'] = t
        metrics = efficiency_rows(controlled)[0]
        self.assertEqual(metrics['mean_search_call_wall_ms'], 4)
        self.assertEqual(metrics['median_search_call_wall_ms'], 2)

    def test_empty_or_invalid_metrics_and_boolean_budgets_reject(self):
        with self.assertRaises(ContractError):
            efficiency_rows([])
        data = read_json(EFFICIENCY, allow_finite_floats=True)[:1]
        data[0]['runs'] = []
        with self.assertRaises(ContractError):
            efficiency_rows(data)
        raw = read_json(EXPENSE/'sample.json')
        prepared = adapt(raw)
        for budget in [0, True, 101]:
            with self.assertRaises(ContractError):
                audit_expense_model(raw, prepared['catalog'], prepared['request'], max_candidates=budget)

    def test_manifest_tampering_and_failed_probe_expectations_are_detected(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            output = Path(directory)/'good'
            self.assertEqual(run(output)['status'], 'expectations_met_with_scope_limits')
            verify(output)
            manifest = read_json(output/'manifest.json')
            changed = deepcopy(manifest)
            changed['source_sha256_lf_normalized'] = {}
            (output/'manifest.json').write_text(json.dumps(changed), encoding='utf-8')
            with self.assertRaisesRegex(ContractError, 'incomplete source'):
                verify(output)
            (output/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with (output/'quality-probes.json').open('a', encoding='utf-8') as stream:
                stream.write(' ')
            with self.assertRaisesRegex(ContractError, 'artifact changed'):
                verify(output)
            with patch('workflow_tools.model_quality_audit.quality_probes', return_value=[{'expectation_met': False, 'actual_quality_status': 'inconclusive'}]):
                self.assertEqual(run(Path(directory)/'bad')['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
