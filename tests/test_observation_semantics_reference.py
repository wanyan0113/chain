from copy import deepcopy
import tempfile
from pathlib import Path
import unittest

from workflow_tools.audit_observation_semantics import protocol,fixture,execute
from workflow_tools.observation_semantics_reference import grade


class SemanticsReferenceTests(unittest.TestCase):
    def setUp(self):self.p=protocol();self.rows,self.registry=fixture(self.p)

    def test_repeated_type_has_distinct_denominators_and_success_counts(self):
        r=grade(self.p,self.rows,self.registry)['flows'][0]
        evaluation=[s for s in r['stage_statistics'] if s['split']=='evaluation']
        self.assertEqual([s['counts']['evaluable'] for s in evaluation],[4,4,4,4])
        self.assertEqual([s['correct'] for s in evaluation],[3,1,3,3])

    def test_missing_middle_stage_does_not_remove_other_stage_measurements(self):
        rows=deepcopy(self.rows)
        target=next(r for r in rows if r['sample_id']=='F4:evaluation:1' and r['stage_instance_id']=='s2')
        target.update(status='not_executed',output_label='')
        r=grade(self.p,rows,self.registry)['flows'][0]
        stages=[s for s in r['stage_statistics'] if s['split']=='evaluation']
        self.assertEqual([s['counts'].get('evaluable',0) for s in stages],[4,3,4,4])
        self.assertEqual(r['complete_evaluation'],3);self.assertEqual(r['last_stage_evaluable'],4)
        self.assertAlmostEqual(r['complete_case_last_output_rate'],2/3)
        self.assertEqual(r['last_stage_descriptive_rate'],.75)

    def test_registry_exposes_wholly_missing_subject(self):
        rows=[r for r in self.rows if r['sample_id']!='F4:evaluation:3']
        r=grade(self.p,rows,self.registry)['flows'][0]
        self.assertEqual(r['registered_endpoint_eligible'],4)
        self.assertEqual(r['wholly_unrecorded_subjects'],['F4:evaluation:3'])

    def test_same_label_cannot_prove_same_input_origin(self):
        target=next(r for r in self.rows if r['sample_id']=='F4:evaluation:2' and r['stage_instance_id']=='s2')
        source=next(r for r in self.rows if r['sample_id']=='F4:evaluation:0' and r['stage_instance_id']=='s1')
        self.assertEqual(target['input_label'],source['output_label'])
        r=grade(self.p,self.rows,self.registry,input_sources={target['observation_id']:source['observation_id']})
        self.assertFalse(r['lineage_checks'][0]['valid'])

    def test_independent_final_event_has_separate_denominator_and_missing_coverage(self):
        endpoints={('F4',f'F4:evaluation:{i}'):dict(success=i==0,evaluation_method='independent-fixture-event') for i in range(4)}
        r=grade(self.p,self.rows,self.registry,final_outcomes=endpoints)['flows'][0]
        self.assertEqual(r['independent_final_rate'],.25)
        self.assertEqual(r['last_stage_descriptive_rate'],.75)
        endpoints.pop(('F4','F4:evaluation:3'))
        r=grade(self.p,self.rows,self.registry,final_outcomes=endpoints)['flows'][0]
        self.assertEqual(r['independent_final_evaluable'],3)
        self.assertFalse(r['independent_final_coverage_complete'])
        self.assertEqual(r['independent_final_missing'],['F4:evaluation:3'])

    def test_duplicates_not_turned_into_more_independent_samples(self):
        with self.assertRaises(ValueError):grade(self.p,self.rows+[self.rows[-1]],self.registry)
        with self.assertRaises(ValueError):grade(self.p,self.rows,self.registry+[self.registry[-1]])

    def test_full_audit_reproduces_guards_and_open_semantics_findings(self):
        with tempfile.TemporaryDirectory() as directory:r=execute(Path(directory)/'audit')
        self.assertEqual(r['status'],'conditional_pass_with_open_findings')
        self.assertEqual(len(r['cases']),11);self.assertTrue(all(c['passed'] for c in r['cases']))
        self.assertEqual(len(r['open_findings']),5);self.assertFalse(r['historical_contract_changed'])
        self.assertEqual(r['domain_validity'],'pending')


if __name__=='__main__':unittest.main()
