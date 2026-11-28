from copy import deepcopy
import csv
import hashlib
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import unittest

from workflow_tools.accuracy_observations import (ROOT,FIELDS,VERSION,analyze,freeze_protocol,read_json,validate_protocol)


def fixture_protocol():
    return dict(version=VERSION,study_id='synthetic-intake-fixture',data_origin='synthetic_fixture',
        registration_mode='retrospective_analysis',target_definition='Fixture binary label; no real-domain evidence',
        label_space=['0','1'],ground_truth_method_review='verified',sampling_design='iid_subject',
        evaluation_population='fixture-balanced',label_sources={'fixture-key':'Deterministic synthetic truth, not external annotation'},
        strata=['all'],flows=[dict(flow_id='flow-A',calibration_population='fixture-balanced',input_from_previous_output=True,
            stages=[dict(stage_instance_id=f'S{i}',activity_id='classify',activity_version='1.0',
                artifact_id=f'fixture-operator-{i}',artifact_version='1.0',calibration_input_condition='correct_reference_label') for i in (1,2)])],
        acceptance=dict(absolute_error_tolerance=.05,alpha=.05,minimum_stage_calibration=2,
                        minimum_complete_evaluation=4,minimum_stratum_class=1))


def fixture_rows(copies=1):
    result=[];protocol=fixture_protocol()
    for split in ('calibration','evaluation'):
        for i in range(4*copies):
            subject=f'{split}-{i}';gold=str(i%2);mask=i%4;previous=gold;all_good=True
            for index,stage in enumerate(protocol['flows'][0]['stages']):
                all_good=all_good and bool(mask&(1<<index))
                good=bool(mask&(1<<index)) if split=='calibration' else all_good
                output=gold if good else str(1-int(gold))
                row=dict(observation_id=f'{subject}-{index}',sample_id=subject,cluster_id=subject,split=split,
                    flow_id='flow-A',stage_instance_id=stage['stage_instance_id'],activity_id=stage['activity_id'],
                    activity_version=stage['activity_version'],artifact_id=stage['artifact_id'],artifact_version=stage['artifact_version'],
                    stratum='all',gold_label=gold,input_label=gold if split=='calibration' else previous,
                    output_label=output,status='observed',label_source_id='fixture-key',recorded_at='2025-01-01T00:00:00Z')
                result.append(row);previous=output
    return result


def write_rows(path,rows):
    with path.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=FIELDS,lineterminator='\n');writer.writeheader();writer.writerows(rows)


class ObservationIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.directory=Path(self.tmp.name)
        self.p=fixture_protocol();self.rows=fixture_rows()
        self.lock=freeze_protocol(self.p,self.directory/'lock.json')
    def tearDown(self):self.tmp.cleanup()
    def report(self,rows=None,p=None,lock=True):
        path=self.directory/'observations.csv';write_rows(path,self.rows if rows is None else rows)
        return analyze(self.p if p is None else p,path,self.lock if lock is True else None if lock is False else lock)

    def test_header_only_template_reports_no_data_and_keeps_unknowns_null(self):
        p=read_json(ROOT/'data/accuracy-observations/templates/protocol.json')
        r=analyze(p,ROOT/'data/accuracy-observations/templates/observations.csv')
        self.assertIn('no_observations',r['blockers']);self.assertIn('no_real_data_collected',r['blockers'])
        self.assertIsNone(r['flows'][0]['statistical_assessment']);self.assertIsNone(r['flows'][0]['endpoint_rate'])
        self.assertEqual(r['domain_validity'],'pending')

    def test_natural_observations_need_no_shadow_or_fault_columns(self):
        r=self.report();self.assertFalse(r['blockers'])
        self.assertEqual(r['flows'][0]['statistical_assessment']['prediction'],.25)
        self.assertEqual(r['flows'][0]['endpoint_rate'],.25)
        self.assertEqual(r['flows'][0]['statistical_assessment']['decision'],'inconclusive')
        self.assertEqual(r['domain_validity'],'pending');self.assertFalse(r['domain_approval_automatic'])

    def test_statistically_equivalent_fixture_never_becomes_domain_pass(self):
        r=self.report(rows=fixture_rows(copies=1000))
        self.assertFalse(r['blockers'])
        self.assertEqual(r['flows'][0]['statistical_assessment']['decision'],'equivalent_within_tolerance')
        self.assertEqual(r['domain_validity'],'pending')

    def test_missing_output_or_unexecuted_stage_never_imputed(self):
        for status in ('missing','not_executed'):
            rows=deepcopy(self.rows);rows[-1].update(status=status,output_label='')
            r=self.report(rows)
            self.assertIn('incomplete_observations',r['blockers'])
            self.assertIsNone(r['flows'][0]['statistical_assessment']);self.assertFalse(r['missing_values_imputed'])
        r=self.report(self.rows[:-1]);self.assertIn('incomplete_evaluation_flow:flow-A',r['blockers'])

    def test_subject_and_cluster_leakage_between_splits_is_detected(self):
        rows=deepcopy(self.rows)
        for row in rows:
            if row['sample_id']=='evaluation-0':row['cluster_id']='calibration-0'
        r=self.report(rows)
        self.assertIn('cluster_leaks_across_splits',{i['code'] for i in r['issues']})
        rows=deepcopy(self.rows)
        for row in rows:
            if row['sample_id']=='evaluation-0':row['sample_id']='calibration-0'
        r=self.report(rows);self.assertIn('subject_leaks_across_splits',{i['code'] for i in r['issues']})

    def test_dependent_clusters_have_no_iid_intervals(self):
        rows=deepcopy(self.rows)
        for row in rows:
            if row['sample_id']=='evaluation-1':row['cluster_id']='evaluation-0'
        r=self.report(rows);self.assertIn('dependent_subject_clusters_require_other_ci_method',r['blockers'])
        self.assertIsNone(r['flows'][0]['endpoint_ci95'])

    def test_versions_duplicate_records_and_ground_truth_conflicts_block(self):
        for field,value,code in [('artifact_version','2.0','activity_or_artifact_version_mismatch'),
                                 ('gold_label','1','subject_ground_truth_or_population_conflict')]:
            rows=deepcopy(self.rows);rows[-2][field]=value
            if field=='gold_label':rows[-2][field]=str(1-int(self.rows[-2][field]))
            r=self.report(rows);self.assertIn(code,{i['code'] for i in r['issues']})
        r=self.report(self.rows+[self.rows[-1]]);self.assertIn('duplicate_observation',{i['code'] for i in r['issues']})

    def test_observed_abstention_is_a_measured_error_not_a_missing_record(self):
        rows=deepcopy(self.rows);rows[-1].update(status='abstained',output_label='')
        r=self.report(rows);self.assertFalse(r['blockers'])
        self.assertEqual(r['counts']['status:abstained'],1)
        self.assertEqual(r['flows'][0]['endpoint_rate'],0)

    def test_input_continuity_and_correct_calibration_condition_checked(self):
        rows=deepcopy(self.rows);rows[-1]['input_label']=str(1-int(rows[-1]['input_label']))
        r=self.report(rows);self.assertIn('serial_input_continuity_mismatch',{i['code'] for i in r['issues']})
        rows=deepcopy(self.rows);rows[0]['input_label']=str(1-int(rows[0]['gold_label']))
        r=self.report(rows);self.assertIn('calibration_input_condition_violated',{i['code'] for i in r['issues']})

    def test_recovery_diagnostic_uses_observed_input_and_makes_no_causal_claim(self):
        rows=deepcopy(self.rows)
        target=next(r for r in rows if r['sample_id']=='evaluation-0' and r['stage_instance_id']=='S2')
        target['output_label']=target['gold_label']
        r=self.report(rows);d=r['flows'][0]['input_output_diagnostics'][1]
        self.assertEqual(d['previous_output_recoveries'],1);self.assertFalse(d['causal_correction_claim'])
        self.assertEqual(d['direct_input_conditionals'][1]['output_correct'],1)

    def test_freeze_tampering_unreviewed_truth_and_population_mismatch_cannot_pass(self):
        p=deepcopy(self.p);p['acceptance']['absolute_error_tolerance']=.1
        r=self.report(p=p);self.assertIn('protocol_lock_mismatch',r['blockers'])
        p=deepcopy(self.p);p['ground_truth_method_review']='pending'
        with self.assertRaises(ValueError):freeze_protocol(p,self.directory/'not-ready.json')
        p=deepcopy(self.p);p['flows'][0]['calibration_population']='different'
        self.assertIn('population_mismatch:flow-A',self.report(p=p)['blockers'])
        changed=deepcopy(self.lock);changed['source_hashes']={}
        self.assertIn('source_changed_since_freeze',self.report(lock=changed)['blockers'])

    def test_prospective_old_observations_and_future_timestamps_block(self):
        p=deepcopy(self.p);p['registration_mode']='prospective'
        lock=freeze_protocol(p,self.directory/'prospective.json')
        r=self.report(p=p,lock=lock)
        self.assertIn('prospective_observation_precedes_freeze',{i['code'] for i in r['issues']})
        rows=deepcopy(self.rows);rows[-1]['recorded_at']='2999-01-01T00:00:00Z'
        self.assertIn('observation_time_in_future',{i['code'] for i in self.report(rows)['issues']})

    def test_unfrozen_protocol_and_missing_calibration_never_get_a_score(self):
        self.assertIn('protocol_not_frozen',self.report(lock=False)['blockers'])
        r=self.report([row for row in self.rows if row['split']!='calibration'])
        self.assertIn('insufficient_stage_calibration:flow-A',r['blockers'])
        self.assertIsNone(r['flows'][0]['calibration'][0]['rate'])
        self.assertIsNone(r['flows'][0]['statistical_assessment'])

    def test_same_stage_names_in_different_flows_remain_separate(self):
        p=deepcopy(self.p);second=deepcopy(p['flows'][0]);second['flow_id']='flow-B'
        second['stages'][1]['artifact_version']='2.0';p['flows'].append(second)
        lock=freeze_protocol(p,self.directory/'two-flows.json')
        second_rows=deepcopy(self.rows)
        for row in second_rows:
            row.update(flow_id='flow-B',observation_id='B:'+row['observation_id'],sample_id='B:'+row['sample_id'],cluster_id='B:'+row['cluster_id'])
            if row['stage_instance_id']=='S2':row['artifact_version']='2.0'
        r=self.report(rows=self.rows+second_rows,p=p,lock=lock)
        self.assertFalse(r['blockers']);self.assertEqual(len(r['flows']),2)
        self.assertEqual([f['calibration'][0]['n'] for f in r['flows']],[4,4])

    def test_duplicate_stage_with_new_observation_id_is_not_deduplicated_silently(self):
        extra=dict(self.rows[-1],observation_id='new-id-same-stage')
        r=self.report(self.rows+[extra]);self.assertIn('duplicate_stage_observation',{i['code'] for i in r['issues']})

    def test_whitespace_or_noncanonical_identity_cannot_hide_split_leakage(self):
        for identity in (' calibration-0 ', 'e\u0301'):
            rows=deepcopy(self.rows);rows[-1]['sample_id']=identity
            r=self.report(rows);self.assertIn('noncanonical_identity',{i['code'] for i in r['issues']})

    def test_hash_and_analysis_use_the_same_immutable_csv_snapshot(self):
        path=self.directory/'snapshot.csv';write_rows(path,self.rows);before=path.read_bytes()
        path.write_text('changed after snapshot',encoding='utf-8')
        r=analyze(self.p,path,self.lock,observations_bytes=before)
        self.assertFalse(r['blockers']);self.assertEqual(r['observations_sha256'],hashlib.sha256(before).hexdigest())
        self.assertEqual(r['flows'][0]['endpoint_rate'],.25)


if __name__=='__main__':unittest.main()
