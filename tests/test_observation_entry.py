"""Routing rejection and real native-versus-unified observation equivalence."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow_tools.observation_entry import run, main, V1, V2, TEAM, EntryError
from workflow_tools.team_observation_intake import save


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


class EntryRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def rejected(self, data, code, operation='check', **options):
        path = self.root / 'input.json'
        save(path, data)
        with patch('workflow_tools.observation_entry.dispatch') as backend:
            result = run(operation, path, self.root / 'result', **options)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostics'][0]['code'], code)
        backend.assert_not_called()
        self.assertIsNone(result['backend_report'])

    def test_missing_version_has_no_legacy_fallback(self):
        self.rejected({'flows': []}, 'MISSING_VERSION')

    def test_unknown_version_has_no_backend(self):
        self.rejected({'version': 'future-9'}, 'UNSUPPORTED_VERSION')

    def test_two_version_fields_are_not_guessed(self):
        self.rejected({'version': V1, 'contract_version': TEAM}, 'AMBIGUOUS_VERSION')

    def test_expected_version_cannot_convert_input(self):
        self.rejected({'version': V1}, 'VERSION_MISMATCH', expected_version=TEAM)

    def test_mixed_family_is_rejected_before_native_handler(self):
        self.rejected({'version': V2, 'member_observations': []}, 'MIXED_FORMAT')

    def test_wrong_discriminator_is_not_accepted(self):
        self.rejected({'version': TEAM}, 'VERSION_FIELD_CONFLICT')

    def test_unsupported_operation_cannot_be_silently_downgraded(self):
        self.rejected({'version': V1}, 'UNSUPPORTED_OPERATION', operation='preflight')

    def test_team_import_requires_real_lock_argument(self):
        self.rejected({'contract_version': TEAM}, 'MISSING_OPTION', operation='import',
                      protocol=self.root/'p', sources=self.root/'s')

    def test_wrong_family_options_are_not_ignored(self):
        self.rejected({'version': V1}, 'UNEXPECTED_OPTION', sources=self.root/'s')

    def test_duplicate_version_keys_are_invalid_json(self):
        path=self.root/'bad.json'
        path.write_text('{"version":"'+V1+'","version":"'+V2+'"}',encoding='utf-8')
        result=run('check',path,self.root/'result')
        self.assertEqual(result['status'],'blocked')
        self.assertIsNone(result['input_version'])

    def test_existing_output_preserved_and_cli_exits_two(self):
        out=self.root/'result';out.mkdir();(out/'marker').write_text('keep')
        with self.assertRaises(SystemExit) as caught:
            main(['check','--input',str(self.root/'missing'),'--output-dir',str(out)])
        self.assertEqual(caught.exception.code,2)
        self.assertEqual((out/'marker').read_text(),'keep')

    def test_v1_report_not_presented_as_independent_verifier(self):
        out=self.root/'prior';out.mkdir()
        save(out/'entry-report.json',dict(version='observation-entry-1.0',input_version=V1,operation='import'))
        result=run('verify',out,self.root/'result')
        self.assertEqual(result['diagnostics'][0]['code'],'UNSUPPORTED_VERIFICATION_FORMAT')


class EntryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_accuracy_observations import fixture_protocol, fixture_rows, write_rows
        from workflow_tools.accuracy_observations import freeze_protocol
        from tests.test_observations_v2 import inputs as joint_inputs
        from task_flow.joint import run_joint
        from workflow_tools.observatory_demo import make_simulation
        from workflow_tools.observations_v2 import freeze
        from workflow_tools.team_observation_demo import inputs, write_packet, stamp
        from workflow_tools.team_study_lock import create_lock
        cls.tmp=tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.base=Path(cls.tmp.name)
        p=fixture_protocol();save(cls.base/'v1.json',p)
        write_rows(cls.base/'v1.csv',fixture_rows())
        freeze_protocol(p,cls.base/'v1-lock.json')
        report=run_joint(*joint_inputs());study=make_simulation(report,n=8)
        save(cls.base/'v2.json',study);save(cls.base/'joint.json',report)
        freeze(study,report,cls.base/'v2-lock.json')
        cls.plan,data,cls.protocol,sources=inputs(feasibility_only=True)
        cls.team_data,cls.sources=data,sources
        lock=create_lock(cls.plan,cls.protocol,sources,['synthetic-routing-test'],frozen_at=stamp(10))
        cls.packet=cls.base/'packet'
        write_packet(cls.packet,data,cls.protocol,lock,sources,csv_mode=True)
        save(cls.base/'plan.json',cls.plan)

    def test_v1_import_matches_original_cli_and_preserves_snapshots(self):
        from workflow_tools.validate_accuracy_observations import main as native
        b=self.base;unified=b/'v1-unified';direct=b/'v1-direct'
        args=['--protocol',str(b/'v1.json'),'--observations',str(b/'v1.csv'),
              '--lock',str(b/'v1-lock.json'),'--output-dir',str(direct)]
        with redirect_stdout(io.StringIO()):code=native(args)
        result=run('import',b/'v1.json',unified,observations=b/'v1.csv',lock=b/'v1-lock.json')
        self.assertEqual(result['status'],'passed' if code==0 else 'blocked')
        self.assertEqual(read(direct/'observation_report.json'),read(unified/'backend/observation_report.json'))
        self.assertEqual((direct/'observations_snapshot.csv').read_bytes(),(unified/'backend/observations_snapshot.csv').read_bytes())
        self.assertEqual(result['domain_validity'],'pending')

    def test_v2_import_matches_original_cli_including_independent_endpoints(self):
        from workflow_tools.observations_v2 import main as native
        b=self.base;direct=b/'v2-direct';unified=b/'v2-unified'
        with redirect_stdout(io.StringIO()):
            code=native(['analyze','--study',str(b/'v2.json'),'--joint-report',str(b/'joint.json'),
                         '--lock',str(b/'v2-lock.json'),'--output-dir',str(direct)])
        result=run('import',b/'v2.json',unified,joint_report=b/'joint.json',lock=b/'v2-lock.json')
        self.assertEqual(result['status'],'passed' if code==0 else 'blocked')
        self.assertEqual(read(direct/'report.json'),read(unified/'backend/report.json'))
        self.assertEqual(read(direct/'study-snapshot.json'),read(unified/'backend/study-snapshot.json'))

    def test_team_import_and_replay_keep_native_semantics_with_real_owl(self):
        from workflow_tools.team_observation_intake import run as native
        p=self.packet;b=self.base;direct=b/'team-direct';unified=b/'team-unified'
        with redirect_stdout(io.StringIO()):
            native(p/'dataset.json',p/'protocol.json',p/'lock.json',p/'sources.json',direct)
        result=run('import',p/'dataset.json',unified,protocol=p/'protocol.json',lock=p/'lock.json',sources=p/'sources.json')
        self.assertEqual(result['status'],'passed',result)
        self.assertEqual(read(direct/'normalized.json'),read(unified/'backend/normalized.json'))
        a=read(direct/'verification.json');c=read(unified/'backend/verification.json')
        for key in ('status','issues','counts','classification_counts','first_attempt_plan','domain_validity'):
            self.assertEqual(a[key],c[key],key)
        self.assertEqual(c['ontology']['engine'],'HermiT')
        receipt=read(unified/'backend/intake_report.json')['receipt_sha256']
        verified=run('verify',unified,b/'team-replay',expected_receipt_hash=receipt)
        self.assertEqual(verified['status'],'passed',verified)
        wrong=run('verify',unified,b/'team-wrong-receipt',expected_receipt_hash='0'*64)
        self.assertEqual(wrong['status'],'blocked')
        self.assertIn('INTAKE_EVIDENCE_CHANGED',{x['code'] for x in wrong['diagnostics']})

    def test_format_pass_does_not_hide_missing_member_at_import(self):
        from workflow_tools.team_observation_demo import write_packet
        b=self.base;packet=b/'damaged-packet';data=deepcopy(self.team_data)
        data['member_observations'].pop(0)
        lock=read(self.packet/'lock.json')
        write_packet(packet,data,self.protocol,lock,self.sources)
        options=dict(protocol=packet/'protocol.json',sources=packet/'sources.json')
        checked=run('check',packet/'dataset.json',b/'damaged-format',**options)
        self.assertEqual(checked['status'],'passed',checked)
        imported=run('import',packet/'dataset.json',b/'damaged-import',lock=packet/'lock.json',**options)
        self.assertEqual(imported['status'],'blocked')
        self.assertIn('MISSING_MEMBER_OBSERVATION',{x['code'] for x in imported['diagnostics']})

    def test_native_directory_replay_is_supported_without_wrapper(self):
        # This is a fresh native import, independent of test ordering.
        from workflow_tools.team_observation_intake import run as native
        p=self.packet;b=self.base;direct=b/'native-for-replay'
        with redirect_stdout(io.StringIO()):
            native(p/'dataset.json',p/'protocol.json',p/'lock.json',p/'sources.json',direct)
        result=run('verify',direct,b/'native-replay')
        self.assertEqual(result['status'],'passed',result)

    def test_check_scopes_do_not_pretend_to_be_semantic_verification(self):
        b=self.base;p=self.packet
        cases=[(V1,b/'v1.json',dict(observations=b/'v1.csv')),
               (V2,b/'v2.json',dict(joint_report=b/'joint.json')),
               (TEAM,p/'dataset.json',dict(protocol=p/'protocol.json',sources=p/'sources.json'))]
        for index,(version,path,opts) in enumerate(cases):
            result=run('check',path,b/('check-'+str(index)),**opts)
            self.assertEqual(result['status'],'passed',result)
            self.assertEqual(result['input_version'],version)
            self.assertIn('not_',result['scope'])

    def test_preflight_delegates_without_locking_or_altering_plan(self):
        b=self.base;p=self.packet;before=(b/'plan.json').read_bytes()
        result=run('preflight',b/'plan.json',b/'preflight',protocol=p/'protocol.json',sources=p/'sources.json')
        self.assertEqual(result['status'],'passed',result)
        backend=read(b/'preflight/backend/preflight.json')
        self.assertFalse(backend['study_lock_created'])
        self.assertFalse(backend['real_collection_authorized'])
        self.assertEqual((b/'plan.json').read_bytes(),before)

    def test_legacy_blockers_remain_blockers_at_unified_cli(self):
        from tests.test_accuracy_observations import write_rows
        b=self.base;write_rows(b/'empty.csv',[])
        with redirect_stdout(io.StringIO()):
            code=main(['import','--input',str(b/'v1.json'),'--observations',str(b/'empty.csv'),
                       '--lock',str(b/'v1-lock.json'),'--output-dir',str(b/'empty-result')])
        self.assertEqual(code,2)
        result=read(b/'empty-result/entry-report.json')
        self.assertEqual(result['status'],'blocked')
        self.assertIn('no_observations',{d.get('message') for d in result['diagnostics']})


if __name__=='__main__':unittest.main()
