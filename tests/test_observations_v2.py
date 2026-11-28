from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

from task_flow.joint import run_joint
from workflow_tools.observatory_demo import EXAMPLE,make_simulation
from workflow_tools.observations_v2 import freeze,assess


def inputs():
    read=lambda name:json.loads((EXAMPLE/name).read_text(encoding='utf-8'))
    jobs=[dict(task_id=j['task_id'],catalog=read(j['catalog_file']),request=read(j['request_file'])) for j in read('jobs.json')['jobs']]
    return jobs,read('scene.json'),read('mapping.json')


class ObservationV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report=run_joint(*inputs());cls.study=make_simulation(cls.report,n=8)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.data=deepcopy(self.study);self.lock=freeze(self.data,self.report,self.base/'lock.json')
        self.entry=next(e for e in self.data['enrollments'] if e['split']=='evaluation' and e['flow_id']==self.data['protocol']['flows'][1]['flow_id'])
    def tearDown(self):self.tmp.cleanup()
    def report_for(self):return assess(self.data,self.report,self.lock)
    def flow(self,result):return next(f for f in result['flows'] if f['flow_id']==self.entry['flow_id'])
    def row(self,sid):return next(r for r in self.data['observations'] if r['enrollment_id']==self.entry['enrollment_id'] and r['stage_instance_id']==sid and r['attempt_id']==1)

    def test_four_five_six_stages_and_full_enrolled_cohort_are_analyzed(self):
        r=self.report_for();self.assertEqual(r['status'],'analyzed')
        self.assertEqual([f['group']['stage_count'] for f in r['flows']],[4,5,6])
        self.assertEqual(r['domain_validity'],'pending');self.assertFalse(r['formula_changed'])

    def test_entire_enrolled_sample_absent_is_found(self):
        eid=self.entry['enrollment_id'];self.data['observations']=[r for r in self.data['observations'] if r['enrollment_id']!=eid]
        self.data['endpoints']=[r for r in self.data['endpoints'] if r['enrollment_id']!=eid]
        r=self.report_for();f=self.flow(r)
        self.assertIn(eid,f['endpoint']['wholly_unrecorded_enrollments']);self.assertEqual(f['endpoint']['n_registered_eligible'],8)
        self.assertEqual(f['endpoint']['n_evaluable'],7);self.assertEqual(r['status'],'blocked')

    def test_middle_missing_does_not_change_later_stage_denominators(self):
        row=self.row('s2');self.data['observations'].remove(row)
        r=self.report_for();stats=[s for s in self.flow(r)['stage_statistics'] if s['split']=='evaluation']
        self.assertEqual([s['counts']['n_evaluable'] for s in stats],[8,7,8,8,8])
        self.assertTrue(all(f['statistical_assessment'] is None for f in r['flows']))

    def test_equal_label_from_other_subject_is_rejected_by_references(self):
        target=self.row('s2')
        source=next(r for r in self.data['observations'] if r['enrollment_id']!=target['enrollment_id'] and r['flow_id']==target['flow_id'] and r['stage_instance_id']=='s1' and r['output_label']==target['input_label'])
        target.update(input_reference=source['output_reference'],input_source_observation_id=source['observation_id'])
        self.assertIn('input_producer_wrong_subject_or_stage',self.report_for()['integrity_issues'])

    def test_same_subject_wrong_input_content_is_rejected(self):
        self.row('s2')['input_reference']='sim://different-content'
        self.assertIn('input_output_reference_mismatch',self.report_for()['integrity_issues'])

    def test_final_outcome_is_not_inferred_from_last_stage(self):
        endpoint=next(r for r in self.data['endpoints'] if r['enrollment_id']==self.entry['enrollment_id'])
        self.row('s5').update(output_label=self.row('s5')['gold_label'],stage_outcome='correct')
        self.assertEqual(endpoint['final_outcome'],'failure')
        r=self.report_for();self.assertEqual(r['status'],'analyzed')
        self.assertEqual(self.flow(r)['endpoint']['n_success'],6)

    def test_missing_final_outcome_has_no_default_failure_or_success(self):
        self.data['endpoints']=[r for r in self.data['endpoints'] if r['enrollment_id']!=self.entry['enrollment_id']]
        f=self.flow(self.report_for());self.assertEqual(f['endpoint']['n_evaluable'],7)
        self.assertEqual(f['endpoint']['n_success'],6)
        self.assertIn(self.entry['enrollment_id'],f['endpoint']['missing_enrollments'])

    def test_retry_is_separate_attempt_not_another_sample_or_best_attempt(self):
        r=self.report_for();f=next(f for f in r['flows'] if f['task_id']=='T3')
        s=next(s for s in f['stage_statistics'] if s['split']=='evaluation' and s['stage_instance_id']=='s3')
        self.assertEqual(s['counts']['n_attempt_records'],9);self.assertEqual(s['counts']['n_evaluable'],8)
        changed=deepcopy(self.data);changed['protocol']['attempt_rule']='last_attempt'
        lock=freeze(changed,self.report,self.base/'last-lock.json');last=assess(changed,self.report,lock)
        new=next(s for f in last['flows'] if f['task_id']=='T3' for s in f['stage_statistics'] if s['split']=='evaluation' and s['stage_instance_id']=='s3')
        self.assertEqual(new['counts']['n_correct'],s['counts']['n_correct']+1)

    def test_missing_reason_is_required_and_state_consistent(self):
        r=self.row('s2');r.update(stage_outcome='unassessable',missing_reason='',gold_label='')
        self.assertIn('unassessable_stage_needs_reason',self.report_for()['integrity_issues'])
        r['missing_reason']='truth_missing'
        self.assertNotIn('unassessable_stage_needs_reason',self.report_for()['integrity_issues'])
        r.update(execution_status='not_reached',missing_reason='truth_missing')
        self.assertIn('execution_status_reason_or_output_inconsistent',self.report_for()['integrity_issues'])

    def test_registry_cannot_be_edited_to_hide_dropout(self):
        self.data['enrollments'].remove(self.entry)
        r=self.report_for();self.assertIn('enrolled_cohort_changed_since_freeze',r['blockers'])
        self.assertIn('observation_not_in_registered_cohort',r['integrity_issues'])

    def test_predeclared_exclusion_is_preserved_not_counted(self):
        e=deepcopy(self.entry);e.update(enrollment_id='excluded',sample_id='excluded',cluster_id='excluded',execution_id='excluded-execution',
            eligible=False,exclusion_reason='predeclared screening exclusion',expected_stages=[])
        self.data['enrollments'].append(e);lock=freeze(self.data,self.report,self.base/'exclude-lock.json')
        r=assess(self.data,self.report,lock);self.assertEqual(r['status'],'analyzed')
        self.assertEqual(self.flow(r)['endpoint']['n_registered_eligible'],8)
        accounting=self.flow(r)['enrollment_accounting']['evaluation']
        self.assertEqual(accounting['n_registered'],9);self.assertEqual(accounting['excluded'][0]['enrollment_id'],'excluded')

    def test_truth_or_version_inconsistent_with_declared_evaluation_blocks(self):
        self.row('s2')['artifact_version']='other-version'
        self.assertIn('stage_identity_version_or_method_mismatch',self.report_for()['integrity_issues'])

    def test_duplicate_attempt_and_leakage_do_not_increase_evidence(self):
        duplicate=deepcopy(self.row('s2'));duplicate['observation_id']='different-id-same-attempt'
        self.data['observations'].append(duplicate)
        self.assertIn('duplicate_stage_attempt',self.report_for()['integrity_issues'])
        e=next(e for e in self.data['enrollments'] if e['split']=='calibration')
        self.entry['cluster_id']=e['cluster_id']
        self.assertIn('calibration_evaluation_leakage',self.report_for()['integrity_issues'])

    def test_frozen_tolerance_and_wrong_lock_version_block_statistics(self):
        self.data['protocol']['acceptance']['tolerance']=.2
        self.assertIn('protocol_changed_since_freeze',self.report_for()['blockers'])
        self.lock['version']='legacy'
        self.assertIn('wrong_lock_contract_version',self.report_for()['blockers'])

    def test_failed_execution_is_an_attempt_but_not_an_evaluable_success(self):
        row=self.row('s2');row.update(execution_status='execution_failed',stage_outcome='unassessable',
            output_label='',missing_reason='execution_failure')
        r=self.report_for();s=next(s for s in self.flow(r)['stage_statistics'] if s['split']=='evaluation' and s['stage_instance_id']=='s2')
        self.assertEqual(s['counts']['n_executed'],8);self.assertEqual(s['counts']['n_evaluable'],7)
        self.assertEqual(s['counts']['n_attempted_executions'],8);self.assertEqual(r['status'],'blocked')

    def test_output_cannot_also_claim_to_be_its_independent_truth(self):
        self.row('s2')['ground_truth_reference']=self.row('s2')['output_reference']
        self.assertIn('stage_truth_reference_is_its_output',self.report_for()['integrity_issues'])
        endpoint=next(r for r in self.data['endpoints'] if r['enrollment_id']==self.entry['enrollment_id'])
        endpoint['ground_truth_reference']=endpoint['result_reference']
        self.assertIn('endpoint_truth_reference_is_its_result',self.report_for()['integrity_issues'])

    def test_isolated_calibration_does_not_silently_become_a_chained_fit(self):
        row=next(r for r in self.data['observations'] if r['enrollment_id'].split(':')[1]=='calibration')
        row['input_source_observation_id']='some-other-observation'
        self.assertIn('isolated_calibration_has_undeclared_producer',self.report_for()['integrity_issues'])


if __name__=='__main__':unittest.main()
