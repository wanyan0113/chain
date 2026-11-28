"""Structural contract acceptance, explicitly separate from a future importer."""
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from workflow_tools.prepare_team_observation_contract import (VERSION, fields, schemas, protocol_schema, specification, template, build)

ROOT = Path(__file__).resolve().parents[1]
T = '2026-11-28T00:00:00+00:00'


def synthetic_shape_example():
    # Fictitious one-stage keys exercise row shapes, not a verified source chain.
    base = dict(observation_id='m1',sample_id='s1',execution_id='e1',actual_label='class-ok',reference_label='class-ok',
        output_reference='synthetic://actual/member',independent_reference='synthetic://truth/member',
        evaluation_method='categorical_equality_v1',observation_status='recorded',reference_status='known',
        missing_reason=None,missing_reason_detail=None,recorded_at=T)
    stage = dict(candidate_id='synthetic-candidate',stage_instance_id='stage1',attempt_id=1)
    member = dict(**base,**stage,artifact_id='memberA',artifact_version='synthetic-fw1',capability_id='synthetic-capability',
                  input_reference='synthetic://baseline',input_group_observation_ids=[])
    group = dict(**{**base,'observation_id':'g1','output_reference':'synthetic://actual/group','independent_reference':'synthetic://truth/group'},
                 **stage,hub='memberA',member_observation_ids=['m1'],input_group_observation_ids=[])
    endpoint = dict(**{**base,'observation_id':'z1','output_reference':'synthetic://actual/endpoint','independent_reference':'synthetic://truth/endpoint'},
                    candidate_id=stage['candidate_id'],endpoint_event_id='task-stable',contributing_group_observation_ids=['g1'])
    return dict(contract_version=VERSION,study_id='synthetic-shape-only',protocol_revision_hash='1'*64,data_origin='synthetic_simulation',
        samples=[dict(sample_id='s1',pair_id='pair1',task_id='task1',population='synthetic',cluster_id='cluster1',split='calibration',
            stratum='all',eligible=True,exclusion_reason=None,baseline_input_reference='synthetic://baseline',registered_at=T)],
        executions=[dict(execution_id='e1',sample_id='s1',arm_id='arm1',task_id='task1',source_report_id='synthetic-source',
            candidate_id=stage['candidate_id'],environment_reference='synthetic://environment',environment_stratum='controlled',
            assigned_order=1,execution_repeat_id=1,actual_order=1,order_deviation_reason=None,
            execution_input_reference='synthetic://baseline',reset_reference=None,expected_stage_instance_ids=['stage1'],
            registered_at=T,started_at=T,ended_at=T)],
        stage_attempts=[dict(execution_id='e1',**stage,registered_at=T,started_at=T,ended_at=T,
            execution_status='completed',reason=None,reason_detail=None)],
        member_observations=[member],group_outputs=[group],task_endpoints=[endpoint])


class TeamObservationContractTests(unittest.TestCase):
    def setUp(self):
        self.draft = json.loads((ROOT/'docs/task-flow/team-statistical-review-evidence/protocol-draft.json').read_text(encoding='utf-8'))
        self.data_schema = schemas()
        self.protocol_schema = protocol_schema()
        self.validator = Draft202012Validator(self.data_schema,format_checker=FormatChecker())

    def test_schemas_are_valid_and_synthetic_rows_are_structural_only(self):
        Draft202012Validator.check_schema(self.data_schema)
        Draft202012Validator.check_schema(self.protocol_schema)
        self.validator.validate(synthetic_shape_example())
        self.assertFalse(specification()['importer_implemented'])
        self.assertFalse(specification()['independent_data_verifier_implemented'])

    def test_draft_remains_uncollected_and_cannot_be_frozen_with_placeholders(self):
        p = template(self.draft)
        v = Draft202012Validator(self.protocol_schema)
        v.validate(p)
        self.assertFalse(p['actual_study_frozen'])
        self.assertTrue(all(m['measurements'] for a in p['arms'] for s in a['stages'] for m in s['members']))
        p.update(study_protocol_status='frozen',actual_study_frozen=True)
        with self.assertRaises(ValidationError): v.validate(p)

    def test_completed_synthetic_protocol_shape_does_not_claim_domain_validity(self):
        p = template(self.draft)
        p.update(study_protocol_status='frozen',actual_study_frozen=True,study_id='synthetic-shape-only',data_origin='synthetic_simulation',
                 maximum_attempts=2,planned_execution_repeats=1,endpoint_window='synthetic task window',
                 retry_task_effect='Endpoint independent of first-attempt conjunction',sampling_design='single_arm')
        p['source_reports'][0].update(source_report_id='synthetic-source',report_sha256='1'*64)
        for k in p['pairing_controls']:
            if type(p['pairing_controls'][k]) is str: p['pairing_controls'][k]='explicit synthetic or not-applicable declaration'
        p['acceptance'].update(alpha=.05,minimum_calibration=10,minimum_evaluation=10,minimum_prefix_support=10,
                               acceptable_absolute_error=.05,subgroup_plan='synthetic all stratum',systematic_bias_rule='prespecified synthetic rule')
        for i,a in enumerate(p['arms']):
            a.update(arm_id=f'arm{i}',source_report_id='synthetic-source',endpoint_definition='independent synthetic event',
                     endpoint_method='categorical_equality_v1',endpoint_reference_method='synthetic independent truth',
                     endpoint_equals_all_stage_success='declared_not_equivalent')
            for s in a['stages']:
                for k in ['measurement_object','direction','scale_meaning','reference_method','population','group_success_event']:
                    s[k]='explicit synthetic declaration'
                s['evaluation_method']='categorical_equality_v1'
                for m in s['members']:
                    m['artifact_version']='synthetic-fw1'
                    for event in m['measurements']:
                        for k in event:
                            if k!='capability_id': event[k]='explicit synthetic declaration'
                        event['evaluation_method']='categorical_equality_v1'
        Draft202012Validator(self.protocol_schema).validate(p)
        self.assertEqual(p['domain_validity'],'pending')
        p['arms'][0]['stages'][0]['members'][0]['artifact_version']='UNASSIGNED'
        with self.assertRaises(ValidationError): Draft202012Validator(self.protocol_schema).validate(p)

    def test_recorded_output_and_known_reference_require_actual_labels(self):
        for field in ['actual_label','reference_label','output_reference','independent_reference']:
            data=synthetic_shape_example();data['group_outputs'][0][field]=None
            with self.assertRaises(ValidationError): self.validator.validate(data)

    def test_missing_is_null_with_reason_not_a_false_label(self):
        data=synthetic_shape_example();r=data['group_outputs'][0]
        r.update(observation_status='missing',actual_label=None,missing_reason='output_lost')
        self.validator.validate(data)
        r['missing_reason']=None
        with self.assertRaises(ValidationError): self.validator.validate(data)
        r.update(missing_reason='output_lost',actual_label='failure')
        with self.assertRaises(ValidationError): self.validator.validate(data)

    def test_unknown_truth_and_unknown_member_version_cannot_be_evaluable(self):
        data=synthetic_shape_example();r=data['member_observations'][0]
        r.update(reference_status='unknown',reference_label=None,missing_reason='truth_missing')
        self.validator.validate(data)
        r['reference_label']='made-up-truth'
        with self.assertRaises(ValidationError): self.validator.validate(data)
        data=synthetic_shape_example();data['member_observations'][0]['artifact_version']='UNASSIGNED'
        with self.assertRaises(ValidationError): self.validator.validate(data)

    def test_attempt_failure_keeps_reason_and_unknown_outcomes_are_separate(self):
        data=synthetic_shape_example();r=data['stage_attempts'][0]
        r.update(execution_status='failed',reason='execution_failure')
        self.validator.validate(data)
        r['reason']=None
        with self.assertRaises(ValidationError): self.validator.validate(data)
        r.update(reason='execution_failure',attempt_id=0)
        with self.assertRaises(ValidationError): self.validator.validate(data)

    def test_uncollected_dataset_cannot_hide_observation_rows(self):
        data=synthetic_shape_example();data['data_origin']='uncollected'
        with self.assertRaises(ValidationError): self.validator.validate(data)
        data=synthetic_shape_example();data['protocol_revision_hash']=None
        with self.assertRaises(ValidationError): self.validator.validate(data)

    def test_formats_and_array_links_are_not_csv_lookalikes(self):
        data=synthetic_shape_example();data['group_outputs'][0]['member_observation_ids']='m1,m2'
        with self.assertRaises(ValidationError): self.validator.validate(data)
        data=synthetic_shape_example();data['samples'][0]['registered_at']='not-a-time'
        with self.assertRaises(ValidationError): self.validator.validate(data)

    def test_shape_validation_is_not_a_referential_integrity_checker(self):
        data=synthetic_shape_example();data['group_outputs'][0]['execution_id']='dangling-execution'
        self.validator.validate(data)  # Intentionally not an importer/semantic verifier.
        spec=specification()
        self.assertTrue(any('same sample, execution' in s for s in spec['required_cross_table_checks']))
        self.assertIn('structural only',spec['future_acceptance']['checks'])
        self.assertEqual(spec['retry_policy']['analysis_attempt_rule'],'first_registered_attempt_only')

    def test_software_seal_is_reproducible_has_only_empty_tables_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            a,b=Path(temp)/'a',Path(temp)/'b'
            original=deepcopy(self.draft)
            first=build(a,self.draft);second=build(b,self.draft)
            self.assertEqual(self.draft,original)
            self.assertEqual(first,second)
            for name,digest in first['files'].items():
                self.assertEqual(hashlib.sha256((a/name).read_bytes()).hexdigest(),digest)
            for name in fields():
                with (a/(name+'.csv')).open(encoding='utf-8',newline='') as f: self.assertEqual(len(list(csv.reader(f))),1)
            empty=json.loads((a/'empty-dataset.json').read_text(encoding='utf-8'))
            self.validator.validate(empty)
            self.assertTrue(all(not empty[name] for name in fields()))
            self.assertFalse(first['actual_study_frozen'])
            with self.assertRaises(ValueError): build(a,self.draft)

    def test_published_baseline_matches_reproduction_and_its_file_hashes(self):
        published=ROOT/'docs/task-flow/team-observation-contract-v1'
        manifest=json.loads((published/'freeze-manifest.json').read_text(encoding='utf-8'))
        for name,digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((published/name).read_bytes()).hexdigest(),digest,name)
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(build(Path(temp)/'reproduction',self.draft),manifest)


if __name__=='__main__': unittest.main()
