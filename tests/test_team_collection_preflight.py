from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow_tools.team_collection_preflight import preflight, run
from workflow_tools.team_observation_demo import inputs


class CollectionPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan, cls.observed, cls.protocol, cls.sources=inputs(feasibility_only=True)

    def test_real_registration_owl_does_not_authorize_collection(self):
        before=deepcopy((self.plan,self.protocol))
        report=preflight(self.plan,self.protocol,self.sources)
        self.assertEqual(report['status'],'registration_software_checks_passed')
        self.assertEqual(report['verification']['ontology']['status'],'passed')
        self.assertFalse(report['real_collection_authorized'])
        self.assertFalse(report['study_lock_created'])
        self.assertEqual(report['domain_validity'],'pending')
        self.assertTrue(all(x['status']=='unreviewed' for x in report['human_review']))
        self.assertEqual(before,(self.plan,self.protocol))

    def test_observed_results_cannot_pass_precollection_even_if_consistent(self):
        with patch('workflow_tools.team_collection_preflight.check_package',return_value={'issues':[]}):
            report=preflight(self.observed,self.protocol,self.sources)
        self.assertEqual(report['status'],'blocked')
        codes={x['code'] for x in report['issues']}
        self.assertIn('OBSERVATIONS_BEFORE_REGISTRATION',codes)
        self.assertIn('EXECUTION_ALREADY_STARTED',codes)

    def test_empty_registry_and_retry_are_not_initial_plan(self):
        plan=deepcopy(self.plan)
        plan['samples']=[]
        plan['stage_attempts'][0]['attempt_id']=2
        with patch('workflow_tools.team_collection_preflight.check_package',return_value={'issues':[]}):
            report=preflight(plan,self.protocol,self.sources)
        self.assertTrue({'EMPTY_REGISTRATION','NOT_PLANNED_FIRST_SLOT'}<={x['code'] for x in report['issues']})

    def test_existing_evidence_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'report.json';p.write_text('preserved',encoding='utf-8')
            with self.assertRaises(ValueError):run(p,p,p,p)
            self.assertEqual(p.read_text(encoding='utf-8'),'preserved')

    def test_unreadable_packet_has_machine_readable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            report=run(p/'missing.json',p/'p.json',p/'s.json',p/'result.json')
            self.assertEqual(report['status'],'blocked')
            self.assertEqual(report['issues'][0]['code'],'INPUT_READ')


if __name__=='__main__':unittest.main()
