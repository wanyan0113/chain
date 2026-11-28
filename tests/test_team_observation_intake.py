"""New-input compatibility, independent replay, ontology and adversarial linkage."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph,URIRef,RDF
from workflow_tools.team_observation_demo import inputs,write_packet,stamp
from workflow_tools.team_study_lock import create_lock
from workflow_tools.team_observation_verify import check_package,verify_intake
from workflow_tools.team_observation_intake import run as intake,save
from workflow_tools.team_observation_io import load_packet
from workflow_tools.audit_team_intake import mutations


class TeamObservationIntakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan,cls.data,cls.protocol,cls.sources=inputs()
        cls.lock=create_lock(cls.plan,cls.protocol,cls.sources,['synthetic-unit-study-review'],frozen_at=stamp(10))
        cls.actual=check_package(cls.data,cls.protocol,cls.lock,cls.sources)  # Fresh source HermiT + observation HermiT.
        assert cls.actual['status']=='passed',cls.actual['issues']

    def fast(self,data=None,protocol=None,lock=None):
        # Unit checks isolate closed-world linkage from already exercised OWL;
        # the positive and role-conflict integrations use real HermiT separately.
        with (patch('workflow_tools.team_observation_verify.verify_team_optimization',return_value=self.actual['source_verification']['source-1']),
              patch('workflow_tools.team_observation_owl.audit_owl',return_value=deepcopy(self.actual['ontology']))):
            return check_package(data or self.data,protocol or self.protocol,lock or self.lock,self.sources)

    def test_positive_real_ontology_adds_types_without_claiming_statistics(self):
        self.assertEqual(self.actual['ontology']['engine'],'HermiT')
        self.assertTrue(self.actual['ontology']['capability_support_complete'])
        m=self.data['member_observations'][0]
        from urllib.parse import quote
        subject='urn:team-observation:'+quote(m['observation_id'],safe='')
        inferred='urn:team-observation-validation:LinkedContribution'
        self.assertIn(inferred,self.actual['ontology']['types'][subject])
        g=Graph().parse(data=self.actual['ontology']['input_ttl'],format='turtle')
        self.assertNotIn((URIRef(subject),RDF.type,URIRef(inferred)),g)
        self.assertEqual(self.actual['counts']['samples'],6)
        self.assertEqual(self.actual['counts']['executions'],12)
        self.assertIsNone(self.actual['confidence_intervals'])
        self.assertFalse(self.actual['conditional_model_validated'])

    def test_adversarial_cases_have_local_codes_not_just_failure(self):
        for name,data,expected in mutations(self.data):
            if name=='ontology_role_conflict':expected={'MEMBER_NOT_ASSIGNED'}
            result=self.fast(data)
            codes={r['code'] for r in result['issues']}
            self.assertEqual(result['status'],'blocked',name)
            self.assertTrue(expected<=codes,(name,expected,codes))
            self.assertTrue(all('message' in r for r in result['issues']))

    def test_missing_actual_first_attempt_does_not_delete_frozen_slots(self):
        data=deepcopy(self.data);data['stage_attempts'].pop(0)
        result=self.fast(data)
        self.assertIn('MISSING_ACTUAL_FIRST_ATTEMPT_RECORD',{i['code'] for i in result['issues']})
        self.assertEqual(len(result['first_attempt_plan']),48)
        self.assertEqual(len(self.lock['plan']['first_stage_attempts']),48)

    def test_missing_record_can_be_owl_consistent_but_data_is_incomplete(self):
        data=deepcopy(self.data);data['member_observations'].pop(0)
        with patch('workflow_tools.team_observation_verify.verify_team_optimization',return_value=self.actual['source_verification']['source-1']):
            result=check_package(data,self.protocol,self.lock,self.sources)
        self.assertEqual(result['ontology']['status'],'passed')
        self.assertEqual(result['status'],'blocked')
        self.assertIn('MISSING_MEMBER_OBSERVATION',{r['code'] for r in result['issues']})

    def test_real_owl_rejects_cross_role_contribution(self):
        data=deepcopy(self.data)
        next(r for r in data['member_observations'] if r['stage_instance_id']=='s2')['artifact_id']='R-Survey'
        with patch('workflow_tools.team_observation_verify.verify_team_optimization',return_value=self.actual['source_verification']['source-1']):
            result=check_package(data,self.protocol,self.lock,self.sources)
        self.assertEqual(result['ontology']['status'],'failed')
        self.assertIn('OWL_OBSERVATION_CONSISTENCY',{r['code'] for r in result['issues']})

    def test_capability_claim_does_not_manufacture_equipment_capability(self):
        data=deepcopy(self.data)
        r=next(r for r in data['member_observations'] if r['artifact_id']=='O-Phase-Tuner')
        r['capability_id']='urn:observatory-semantic-demo:SafetyAudit'
        with patch('workflow_tools.team_observation_verify.verify_team_optimization',return_value=self.actual['source_verification']['source-1']):
            result=check_package(data,self.protocol,self.lock,self.sources)
        self.assertEqual(result['ontology']['status'],'passed')
        self.assertFalse(result['ontology']['capability_support_complete'])
        self.assertIn('CAPABILITY_NOT_ASSIGNED',{r['code'] for r in result['issues']})
        self.assertIn('OWL_CAPABILITY_SUPPORT_UNKNOWN',{r['code'] for r in result['issues']})

    def test_runtime_retry_is_preserved_and_does_not_replace_first(self):
        data=deepcopy(self.data);first=data['stage_attempts'][0]
        first.update(execution_status='failed',reason='execution_failure')
        retry={**first,'attempt_id':2,'registered_at':stamp(3605),'started_at':stamp(3606),'ended_at':stamp(3607),'execution_status':'completed','reason':None}
        data['stage_attempts'].append(retry)
        original_members=[r for r in data['member_observations'] if r['execution_id']==first['execution_id'] and r['stage_instance_id']==first['stage_instance_id']]
        newids=[]
        for r in original_members:
            r['actual_label']='incorrect'
            clone=deepcopy(r);clone.update(observation_id=r['observation_id']+'-retry',attempt_id=2,actual_label='correct',recorded_at=stamp(3608),output_reference=r['output_reference']+'-retry')
            newids.append(clone['observation_id']);data['member_observations'].append(clone)
        original_group=next(g for g in data['group_outputs'] if g['execution_id']==first['execution_id'] and g['stage_instance_id']==first['stage_instance_id'])
        original_group['actual_label']='incorrect'
        group={**deepcopy(original_group),'observation_id':original_group['observation_id']+'-retry','attempt_id':2,
               'actual_label':'correct','member_observation_ids':newids,'recorded_at':stamp(3608),'output_reference':original_group['output_reference']+'-retry'}
        data['group_outputs'].append(group)
        for r in data['member_observations']:
            if r['execution_id']==first['execution_id'] and r['stage_instance_id']=='s2':r.update(input_reference=group['output_reference'],input_group_observation_ids=[group['observation_id']])
        for r in data['group_outputs']:
            if r['execution_id']==first['execution_id'] and r['stage_instance_id']=='s2':r['input_group_observation_ids']=[group['observation_id']]
        result=self.fast(data)
        self.assertEqual(result['status'],'passed',result['issues'])
        self.assertTrue(any(i['code']=='RETRY_LINEAGE_OUTSIDE_FIRST_ATTEMPT_MODEL' for i in result['analysis_blockers']))
        self.assertFalse(result['conditional_event_data_ready'])
        self.assertEqual(result['classification_counts']['member_observations']['n_correct'],59)
        retry['registered_at']=stamp(0)
        self.assertIn('RETRY_REGISTERED_BEFORE_PREVIOUS_END',{r['code'] for r in self.fast(data)['issues']})

    def test_failure_unknown_and_classification_error_are_distinct(self):
        missing=deepcopy(self.data);missing['task_endpoints'][0].update(observation_status='missing',actual_label=None,output_reference=None,missing_reason='output_lost')
        unknown=self.fast(missing)
        wrong=self.fast()
        self.assertEqual(unknown['classification_counts']['task_endpoints']['n_evaluable'],11)
        self.assertEqual(wrong['classification_counts']['task_endpoints']['n_evaluable'],12)
        self.assertEqual(unknown['classification_counts']['task_endpoints']['n_missing'],1)
        self.assertIn('OBSERVATION_UNASSESSABLE',{i['code'] for i in unknown['issues']})
        unexecuted=deepcopy(self.data)
        unexecuted['stage_attempts'][0].update(execution_status='not_executed',reason='planned_not_executed',started_at=None,ended_at=None)
        self.assertIn('RECORDED_OUTPUT_WITHOUT_EXECUTION',{r['code'] for r in self.fast(unexecuted)['issues']})

    def test_no_inferred_serial_data_dependency_from_stage_order(self):
        data=deepcopy(self.data);r=next(r for r in data['member_observations'] if r['stage_instance_id']=='s2')
        eid=r['execution_id'];base=next(e['execution_input_reference'] for e in data['executions'] if e['execution_id']==eid)
        for row in data['member_observations']:
            if row['execution_id']==eid and row['stage_instance_id']=='s2':row.update(input_reference=base,input_group_observation_ids=[])
        next(g for g in data['group_outputs'] if g['execution_id']==eid and g['stage_instance_id']=='s2')['input_group_observation_ids']=[]
        result=self.fast(data)
        self.assertEqual(result['status'],'passed',result['issues'])
        self.assertTrue(any(i['code']=='SERIAL_DEPENDENCY_NOT_ESTABLISHED' for i in result['analysis_blockers']))
        data=deepcopy(self.data)
        row=next(r for r in data['member_observations'] if r['stage_instance_id']=='s3');eid=row['execution_id']
        upstream=[g['observation_id'] for g in data['group_outputs'] if g['execution_id']==eid and g['stage_instance_id'] in ('s1','s2')]
        row.update(input_reference='synthetic://bundle-input',input_group_observation_ids=upstream)
        next(g for g in data['group_outputs'] if g['execution_id']==eid and g['stage_instance_id']=='s3')['input_group_observation_ids']=upstream
        result=self.fast(data)
        self.assertEqual(result['status'],'passed',result['issues'])
        self.assertTrue(any(i['code']=='INPUT_BUNDLE_CONTENT_UNVERIFIED' for i in result['analysis_blockers']))

    def test_study_lock_cannot_be_created_after_observing_outputs(self):
        with self.assertRaisesRegex(ValueError,'observed outputs'):create_lock(self.data,self.protocol,self.sources,['synthetic-review'],frozen_at=stamp(10))
        empty=deepcopy(self.plan);empty['samples']=[]
        with self.assertRaisesRegex(ValueError,'nonempty'):create_lock(empty,self.protocol,self.sources,['synthetic-review'],frozen_at=stamp(10))

    def test_old_version_and_malformed_csv_are_format_errors(self):
        for kind in ('old','header','boolean','arrays'):
            with tempfile.TemporaryDirectory() as temp:
                packet=Path(temp)/'packet';write_packet(packet,self.data,self.protocol,self.lock,self.sources,csv_mode=True)
                if kind=='old':
                    meta=json.loads((packet/'dataset.json').read_text());meta['contract_version']='old-single-equipment';save(packet/'dataset.json',meta)
                elif kind=='header':(packet/'samples.csv').write_text('wrong-header\n',encoding='utf-8')
                elif kind=='boolean':
                    text=(packet/'samples.csv').read_text(encoding='utf-8');(packet/'samples.csv').write_text(text.replace(',true,',',True,',1),encoding='utf-8')
                else:
                    text=(packet/'group_outputs.csv').read_text(encoding='utf-8');(packet/'group_outputs.csv').write_text(text.replace('[','{',1),encoding='utf-8')
                result=intake(packet/'dataset.json',packet/'protocol.json',packet/'lock.json',packet/'sources.json',Path(temp)/'out')
                self.assertEqual(result['status'],'format_error',kind)

    def test_json_csv_independent_replay_and_snapshot_tamper_detection(self):
        for csv_mode in (False,True):
            with tempfile.TemporaryDirectory() as temp:
                packet=Path(temp)/'packet';output=Path(temp)/'intake'
                data=deepcopy(self.data);data['member_observations'][0].update(actual_label='0',reference_label='0')
                write_packet(packet,data,self.protocol,self.lock,self.sources,csv_mode=csv_mode)
                with (patch('workflow_tools.team_observation_verify.verify_team_optimization',return_value=self.actual['source_verification']['source-1']),
                      patch('workflow_tools.team_observation_owl.audit_owl',return_value=deepcopy(self.actual['ontology']))):
                    result=intake(packet/'dataset.json',packet/'protocol.json',packet/'lock.json',packet/'sources.json',output)
                self.assertEqual(result['semantic_verification'],'passed')
                normalized=json.loads((output/'normalized.json').read_text(encoding='utf-8'))
                self.assertEqual(normalized['dataset']['member_observations'][0]['actual_label'],'0')
                normalized['dataset']['member_observations'][0]['actual_label']='incorrect'
                save(output/'normalized.json',normalized)
                check=verify_intake(output,expected_receipt_hash=result['receipt_sha256'])
                self.assertEqual(check['issues'][0]['code'],'INTAKE_EVIDENCE_CHANGED')

    def test_source_and_receipt_changes_are_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            packet=Path(temp)/'packet';write_packet(packet,self.data,self.protocol,self.lock,self.sources)
            d,p,l,s,_,issues=load_packet(packet/'dataset.json',packet/'protocol.json',packet/'lock.json',packet/'sources.json')
            self.assertFalse(issues)
            s['source-1']['run_id']='changed-source'
            with patch('workflow_tools.team_observation_verify.verify_team_optimization',return_value=self.actual['source_verification']['source-1']):
                checked=check_package(d,p,l,s)
            self.assertIn('SOURCE_REPORT_HASH',{r['code'] for r in checked['issues']})
        data=deepcopy(self.data);data['samples'][0]['population']='another-population'
        self.assertIn('MEASUREMENT_POPULATION_MISMATCH',{r['code'] for r in self.fast(data)['issues']})

    def test_frozen_contract_tampering_is_rejected_by_both_paths(self):
        import shutil
        from workflow_tools.team_observation_io import CONTRACT,InputError
        with tempfile.TemporaryDirectory() as temp:
            contract=Path(temp)/'contract';shutil.copytree(CONTRACT,contract)
            (contract/'schema.json').write_text('{}',encoding='utf-8')
            with patch('workflow_tools.team_observation_io.CONTRACT',contract):
                with self.assertRaises(InputError) as caught:
                    load_packet(Path('unused'),Path('unused'),Path('unused'),Path('unused'))
                self.assertEqual(caught.exception.issue['code'],'CONTRACT_CHANGED')
            with patch('workflow_tools.team_observation_verify.CONTRACT',contract):
                result=check_package(self.data,self.protocol,self.lock,self.sources)
            self.assertEqual(result['issues'][0]['code'],'CONTRACT_CHANGED')

    def test_unknown_to_incorrect_normalized_rewrite_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            packet=Path(temp)/'packet';output=Path(temp)/'intake'
            data=deepcopy(self.data)
            data['task_endpoints'][0].update(observation_status='missing',actual_label=None,output_reference=None,missing_reason='output_lost')
            write_packet(packet,data,self.protocol,self.lock,self.sources)
            with (patch('workflow_tools.team_observation_verify.verify_team_optimization',return_value=self.actual['source_verification']['source-1']),
                  patch('workflow_tools.team_observation_owl.audit_owl',return_value=deepcopy(self.actual['ontology']))):
                imported=intake(packet/'dataset.json',packet/'protocol.json',packet/'lock.json',packet/'sources.json',output)
            self.assertEqual(imported['semantic_verification'],'blocked')
            normalized=json.loads((output/'normalized.json').read_text(encoding='utf-8'))
            normalized['dataset']['task_endpoints'][0].update(observation_status='recorded',actual_label='incorrect',
                output_reference='invented-output',missing_reason=None)
            save(output/'normalized.json',normalized)
            check=verify_intake(output,expected_receipt_hash=imported['receipt_sha256'])
            self.assertEqual(check['issues'][0]['code'],'INTAKE_EVIDENCE_CHANGED')
            self.assertIn('task_endpoints',check['issues'][0]['message'])

    def test_feasible_unscored_source_is_sufficient_for_collection(self):
        plan,data,p,sources=inputs(feasibility_only=True)
        self.assertTrue(all(c['metrics'] is None for c in sources['source-1']['candidates'].values()))
        lock=create_lock(plan,p,sources,['synthetic-feasibility-study-review'],frozen_at=stamp(10))
        result=check_package(data,p,lock,sources)
        self.assertEqual(result['status'],'passed',result['issues'])
        self.assertEqual(result['ontology']['status'],'passed')
        self.assertFalse(result['production_writeback'])


if __name__=='__main__':unittest.main()
