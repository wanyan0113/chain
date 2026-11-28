from contextlib import redirect_stdout
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from task_flow.joint import run_joint
from tests.test_joint_integration import inputs
from workflow_tools.design_observation_bridge import build,export,check_pack,main
from workflow_tools.accuracy_observations import FIELDS,analyze,read_json,readiness
from workflow_tools.benchmark_joint import save


class DesignObservationBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs,cls.scene,cls.mapping=inputs()
        cls.report=run_joint(cls.jobs,cls.scene,cls.mapping)
        cls.encoded=json.dumps(cls.report,ensure_ascii=False).encode('utf-8')
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()

    def test_actual_selected_network_keeps_three_types_and_four_step_chains(self):
        before=deepcopy(self.report);protocol,binding,roster=build(self.report)
        self.assertEqual(self.report,before)
        self.assertEqual([len(f['stages']) for f in protocol['flows']],[3,4,4])
        second=[r for r in roster if r['task_id']=='T2']
        self.assertEqual([r['activity_type'] for r in second],['observe','decide','decide','act'])
        self.assertEqual(second[1]['activity_id'],'analyze_secondary')
        self.assertEqual(len({r['stage_instance_id'] for r in second}),4)
        self.assertTrue(all(s['artifact_version']=='UNASSIGNED' for f in protocol['flows'] for s in f['stages']))
        self.assertFalse(binding['measurements_generated']);self.assertEqual(binding['domain_validity'],'pending')

    def test_assignment_specific_flow_identity_preserves_canonical_candidate(self):
        p,b,_=build(self.report,['T2'])
        flow=p['flows'][0];source=next(c for c in self.report['candidates'].values() if c['candidate_id']==flow['flow_id'])
        self.assertEqual(flow['flow_id'],source['candidate_id'])
        self.assertEqual(b['flows'][0]['source_flow_id'],source['flow_id'])
        self.assertEqual(b['flows'][0]['source_candidate_identity'],source['identity_payload'])

    def test_empty_observation_roundtrip_is_blocked_not_fabricated(self):
        output=self.base/'pack';summary=export(self.encoded,output)
        p=read_json(output/'protocol.json');r=analyze(p,output/'observations.csv')
        self.assertIn('no_observations',r['blockers']);self.assertEqual(summary['binding_status'],'passed')
        self.assertTrue(readiness(p));self.assertEqual(r['domain_validity'],'pending')
        with (output/'observations.csv').open(encoding='utf-8',newline='') as handle:
            reader=csv.DictReader(handle);self.assertEqual(reader.fieldnames,FIELDS);self.assertEqual(list(reader),[])
        with self.assertRaises(ValueError):analyze(p,output/'stage-roster.csv')

    def test_working_protocol_accepts_study_details_and_unknown_version_resolution(self):
        output=self.base/'pack';export(self.encoded,output,task_ids=['T2'])
        p=read_json(output/'protocol.json')
        p.update(study_id='editable-planned-study',target_definition='To be independently labelled')
        p['acceptance']['absolute_error_tolerance']=.03
        for stage in p['flows'][0]['stages']:stage['artifact_version']='declared-fixture-version'
        save(output/'protocol.json',p)
        result=check_pack(output);self.assertEqual(result['binding_status'],'passed')
        self.assertFalse(result['observations_statistically_validated'])

    def test_working_assignment_order_and_immutable_snapshot_tampering_rejected(self):
        output=self.base/'pack';export(self.encoded,output,task_ids=['T2'])
        p=read_json(output/'protocol.json');p['flows'][0]['stages'][1:3]=reversed(p['flows'][0]['stages'][1:3])
        save(output/'protocol.json',p)
        with self.assertRaises(ValueError):check_pack(output)
        save(output/'protocol.json',read_json(output/'protocol-template.json'))
        with (output/'source_joint_report.json').open('ab') as handle:handle.write(b' ')
        with self.assertRaises(ValueError):check_pack(output)

    def test_report_passed_flag_is_not_trusted_and_output_is_not_created_on_failure(self):
        report=deepcopy(self.report);cid=report['selected_candidate_ids'][0]
        report['candidates'][cid]['chain']['metrics']['accuracy']=.123
        report['verification']={'status':'passed'}
        with self.assertRaises(ValueError):export(json.dumps(report).encode(),self.base/'bad')
        self.assertFalse((self.base/'bad').exists())
        report=deepcopy(self.report);report['candidates'][cid]['candidate_id']='invented-display-id'
        with self.assertRaises(ValueError):build(report)

    def test_declarations_are_explicit_and_unknown_artifacts_rejected(self):
        _,b,roster=build(self.report,['T2'],{'R-Survey':'fixture-v1'})
        self.assertTrue(b['artifact_versions_are_external_declarations'])
        self.assertEqual(roster[0]['artifact_version'],'fixture-v1')
        self.assertTrue(any(r['artifact_version']=='UNASSIGNED' for r in roster))
        with self.assertRaises(ValueError):build(self.report,['T2'],{'not-used':'v1'})
        with self.assertRaises(ValueError):build(self.report,['not-selected'])

    def test_incumbent_source_does_not_get_upgraded_and_plan_id_is_selection_based(self):
        scene=deepcopy(self.scene);scene['policies']['max_network_search_nodes']=8
        report=run_joint(self.jobs,scene,self.mapping)
        _,binding,_=build(report)
        self.assertEqual(binding['source_global_optimality'],'not_proven')
        _,full,_=build(self.report)
        self.assertEqual(binding['collection_plan_id'],full['collection_plan_id'])
        self.assertNotEqual(binding['source_run_id'],full['source_run_id'])

    def test_real_cli_exports_selected_task_and_refuses_overwrite(self):
        source=self.base/'source.json';source.write_bytes(self.encoded);output=self.base/'pack'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--joint-report',str(source),'--task-id','T2','--output-dir',str(output)]),0)
            self.assertEqual(main(['--check-pack',str(output)]),0)
        with self.assertRaises(ValueError):export(self.encoded,output)


if __name__=='__main__':unittest.main()
