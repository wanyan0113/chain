from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from task_flow.joint import run_joint
from tests.test_joint_integration import inputs
from workflow_tools.accuracy_observations import FIELDS,read_json
from workflow_tools.benchmark_joint import save
from workflow_tools.design_observation_bridge import export
from workflow_tools.collection_study import freeze,assess


def prepare_fixture(pack):
    p=read_json(pack/'protocol.json')
    p.update(study_id='source-bound-software-fixture',data_origin='synthetic_fixture',registration_mode='retrospective_analysis',
        target_definition='Synthetic binary labels for gateway verification only',ground_truth_method_review='verified',
        sampling_design='iid_subject',evaluation_population='fixture-population',label_sources={'fixture':'Known synthetic test labels'})
    p['acceptance'].update(absolute_error_tolerance=.05,minimum_stage_calibration=2,minimum_complete_evaluation=4,minimum_stratum_class=1)
    for flow in p['flows']:
        flow.update(calibration_population='fixture-population',input_from_previous_output=True)
        for stage in flow['stages']:stage['artifact_version']='fixture-runtime-v1'
    save(pack/'protocol.json',p)
    return p


def write_fixture(pack,p):
    with (pack/'observations.csv').open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=FIELDS,lineterminator='\n');writer.writeheader()
        for flow in p['flows']:
            for split in ('calibration','evaluation'):
                for i in range(4):
                    sample=f"{flow['flow_id']}:{split}:{i}";gold=str(i%2);previous=gold
                    for index,stage in enumerate(flow['stages']):
                        correct=(i%2==0) if split=='calibration' else (i%2==0 if index==0 else i==0)
                        output=gold if correct else str(1-int(gold))
                        row=dict(observation_id=f'{sample}:{index}',sample_id=sample,cluster_id=sample,split=split,
                            flow_id=flow['flow_id'],stage_instance_id=stage['stage_instance_id'],activity_id=stage['activity_id'],
                            activity_version=stage['activity_version'],artifact_id=stage['artifact_id'],artifact_version=stage['artifact_version'],
                            stratum='all',gold_label=gold,input_label=gold if split=='calibration' else previous,
                            output_label=output,status='observed',label_source_id='fixture',recorded_at='2025-01-01T00:00:00Z')
                        writer.writerow(row);previous=output


class CollectionStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report=run_joint(*inputs());cls.encoded=json.dumps(cls.report,ensure_ascii=False).encode()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.pack=self.root/'pack'
        export(self.encoded,self.pack,task_ids=['T2']);self.p=prepare_fixture(self.pack);self.lock=self.root/'collection-lock.json'
    def tearDown(self):self.tmp.cleanup()

    def test_bound_protocol_freeze_then_actual_labels_link_to_candidate(self):
        before=(self.pack/'source_joint_report.json').read_bytes();freeze(self.pack,self.lock);write_fixture(self.pack,self.p)
        r=assess(self.pack,self.lock,self.root/'result')
        self.assertEqual(r['status'],'analyzed');self.assertEqual(r['domain_validity'],'pending')
        linked=r['linked_candidates'][0];self.assertEqual(linked['task_id'],'T2');self.assertEqual(len(linked['stage_bindings']),4)
        self.assertEqual(linked['candidate_id'],self.p['flows'][0]['flow_id'])
        self.assertEqual(linked['statistical_assessment']['decision'],'inconclusive')
        self.assertFalse(r['production_accuracy_updated']);self.assertEqual((self.pack/'source_joint_report.json').read_bytes(),before)
        self.assertEqual(r['observations_sha256'],hashlib.sha256((self.root/'result/observations.csv').read_bytes()).hexdigest())

    def test_source_assignment_changes_cannot_bypass_the_gateway(self):
        freeze(self.pack,self.lock);write_fixture(self.pack,self.p)
        self.p['flows'][0]['stages'][1]['artifact_id']='other-artifact';save(self.pack/'protocol.json',self.p)
        with self.assertRaises(ValueError):assess(self.pack,self.lock,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())

    def test_frozen_research_field_change_withholds_statistical_output(self):
        freeze(self.pack,self.lock);write_fixture(self.pack,self.p)
        self.p['acceptance']['absolute_error_tolerance']=.2;save(self.pack/'protocol.json',self.p)
        r=assess(self.pack,self.lock,self.root/'changed')
        self.assertIn('protocol_changed_since_collection_freeze',r['blockers'])
        self.assertIsNone(r['linked_candidates'][0]['statistical_assessment'])
        self.assertFalse((self.root/'changed/observation_report.json').exists())

    def test_empty_observations_stay_blocked_after_a_ready_protocol_is_frozen(self):
        freeze(self.pack,self.lock)
        r=assess(self.pack,self.lock,self.root/'empty')
        self.assertIn('no_observations',r['blockers']);self.assertEqual(r['status'],'blocked')
        self.assertIsNone(r['linked_candidates'][0]['statistical_assessment'])

    def test_different_collection_context_and_code_hashes_cannot_reuse_lock(self):
        freeze(self.pack,self.lock);write_fixture(self.pack,self.p)
        lock=read_json(self.lock);lock['collection_context']['source_report_sha256']='wrong';save(self.lock,lock)
        r=assess(self.pack,self.lock,self.root/'context-mismatch')
        self.assertIn('collection_context_changed_since_freeze',r['blockers'])
        lock=read_json(self.lock);lock['gateway_sources']={};save(self.lock,lock)
        r=assess(self.pack,self.lock,self.root/'code-mismatch')
        self.assertIn('gateway_source_changed_since_freeze',r['blockers'])

    def test_missing_stage_is_not_a_successful_end_to_end_dataset(self):
        freeze(self.pack,self.lock);write_fixture(self.pack,self.p)
        lines=(self.pack/'observations.csv').read_text(encoding='utf-8').splitlines()
        (self.pack/'observations.csv').write_text('\n'.join(lines[:-1])+'\n',encoding='utf-8')
        r=assess(self.pack,self.lock,self.root/'missing')
        self.assertEqual(r['status'],'blocked');self.assertIsNone(r['linked_candidates'][0]['statistical_assessment'])

    def test_existing_outputs_and_incomplete_protocol_never_overwritten(self):
        freeze(self.pack,self.lock)
        with self.assertRaises(ValueError):freeze(self.pack,self.lock)
        assess(self.pack,self.lock,self.root/'result')
        with self.assertRaises(ValueError):assess(self.pack,self.lock,self.root/'result')
        self.p['ground_truth_method_review']='pending';save(self.pack/'protocol.json',self.p)
        with self.assertRaises(ValueError):freeze(self.pack,self.root/'not-ready.json')
        self.assertFalse((self.root/'not-ready.json').exists())


if __name__=='__main__':unittest.main()
