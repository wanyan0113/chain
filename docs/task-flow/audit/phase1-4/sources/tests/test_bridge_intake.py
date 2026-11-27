"""A role-based draft is never silently promoted into a reviewed dependency graph."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fdna.bridge_intake import draft_mapping, draft_bundle, inspect_mapping, compile_bundle, main
from fdna.equipment_bridge import bridge
from fdna.model import ModelError
from fdna.structure import digest


class BridgeIntakeTests(unittest.TestCase):
    def setUp(self):
        directory=Path(__file__).resolve().parents[1]/'data/fdna/bridge_full'
        self.report=json.loads((directory/'source_report.json').read_text(encoding='utf-8'))
        self.ready=json.loads((directory/'mapping.json').read_text(encoding='utf-8'))
        self.ready.update(format_version='bridge-review-1',source_report_sha256=digest(self.report),
            review_gate=dict(status='approved_assumption',reviewed_by='合成示例登记',note='继承已登记的模拟假设，非专家实证审查'))
        for item in self.ready['activities']+self.ready['state_sharing']:
            item.update(review_status='approved_assumption',review_note='仅采用预先登记的合成产品状态假设')

    def test_draft_covers_selected_roles_with_pending_assumptions(self):
        draft=draft_mapping(self.report);check=inspect_mapping(self.report,draft)
        self.assertEqual(len(draft['activities']),9);self.assertEqual(len(draft['reviews']),6)
        self.assertEqual(len(draft['state_sharing']),2)
        self.assertEqual(check['status'],'blocked')
        self.assertEqual(check['counts'],dict(pending_instances=9,pending_shared_policies=2,pending_candidates=6))
        self.assertTrue(check['source_bound'])

    def test_unselected_candidate_is_not_extracted(self):
        self.report['tasks'][0]['chains'].append(dict(id='not-selected'))
        self.assertEqual(len(draft_mapping(self.report)['activities']),9)

    def test_instance_ids_stable_when_report_order_changes(self):
        draft=draft_mapping(self.report)
        self.report['tasks'].reverse();self.report['network']['chains'].reverse()
        other=draft_mapping(self.report)
        self.assertEqual(draft['activities'],other['activities'])

    def test_draft_cannot_compile_or_call_fitter(self):
        with tempfile.TemporaryDirectory() as directory,patch('fdna.network_workflow.fit_network') as fitter:
            out=Path(directory)/'blocked'
            with self.assertRaises(ModelError):compile_bundle(self.report,draft_mapping(self.report),out)
            self.assertFalse(out.exists());fitter.assert_not_called()

    def test_source_change_or_missing_binding_blocks_ready_mapping(self):
        changed=deepcopy(self.report);changed['scope']+=' changed'
        self.assertEqual(inspect_mapping(changed,self.ready)['status'],'blocked')
        self.ready.pop('source_report_sha256')
        self.assertEqual(inspect_mapping(self.report,self.ready)['status'],'blocked')

    def test_top_level_approval_does_not_override_pending_instance(self):
        self.ready['activities'][0]['review_status']='pending'
        self.assertEqual(inspect_mapping(self.report,self.ready)['status'],'blocked')

    def test_unfilled_metrics_or_shared_policy_block_approval(self):
        for kind in ('metric','shared','identity'):
            ready=deepcopy(self.ready)
            if kind=='metric':ready['activities'][0]['metric']['definition']='待填写：指标'
            elif kind=='identity':ready['activities'][0]['context']='待确认：场景'
            else:ready['state_sharing'][0]['coupling']='待确认：共同来源'
            with self.subTest(kind=kind):self.assertEqual(inspect_mapping(self.report,ready)['status'],'blocked')

    def test_ready_mapping_compiles_exact_original_topology_without_mutation(self):
        before=deepcopy(self.ready);original,_=bridge(self.report,self.ready)
        with tempfile.TemporaryDirectory() as directory:
            result=compile_bundle(self.report,self.ready,Path(directory)/'ready')
            self.assertEqual(result['status'],'ready');self.assertEqual(result['counts']['edges'],6)
            actual=json.loads((Path(directory)/'ready/输入_拓扑.json').read_text(encoding='utf-8'))
            self.assertEqual(actual,original)
        self.assertEqual(self.ready,before)

    def test_draft_bundle_has_review_tables_and_no_topology_or_model(self):
        with tempfile.TemporaryDirectory() as directory:
            draft_bundle(self.report,Path(directory)/'draft')
            names={p.name for p in (Path(directory)/'draft').iterdir()}
            self.assertIn('能力实例待审.csv',names);self.assertIn('preflight.json',names)
            self.assertNotIn('输入_拓扑.json',names);self.assertNotIn('workflow_report.json',names)
            with self.assertRaises(ModelError):draft_bundle(self.report,Path(directory)/'draft')

    def test_unsupported_plan_or_inconsistent_member_is_rejected(self):
        for mode in ('plan','member'):
            report=deepcopy(self.report)
            chain=next(c for t in report['tasks'] for c in t['chains'] if c['id']==report['network']['chains'][0])
            if mode=='plan':chain['plan']['mode']='multi_activity'
            else:chain['plan']['members']['insight']=['other']
            with self.subTest(mode=mode),self.assertRaises(ModelError):draft_mapping(report)

    def test_legacy_mapping_requires_explicit_upgrade_for_new_intake(self):
        self.ready.pop('format_version')
        self.assertEqual(inspect_mapping(self.report,self.ready)['status'],'blocked')
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'legacy'
            with self.assertRaises(ModelError):compile_bundle(self.report,self.ready,out)
            self.assertFalse(out.exists())

    def test_cli_reports_blocked_status_without_exporting_topology(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'report.json';mapping=root/'draft.json'
            source.write_text(json.dumps(self.report),encoding='utf-8')
            mapping.write_text(json.dumps(draft_mapping(self.report)),encoding='utf-8')
            for command in ('check','compile'):
                out=root/command
                argv=['bridge_intake',command,'--report',str(source),'--mapping',str(mapping),'--output-dir',str(out)]
                with patch('sys.argv',argv),redirect_stdout(io.StringIO()):self.assertEqual(main(),2)
                self.assertFalse((out/'输入_拓扑.json').exists())
                self.assertEqual((out/'preflight.json').exists(),command=='check')


if __name__=='__main__':unittest.main()
