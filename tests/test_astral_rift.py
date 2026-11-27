"""Fictional workflow acceptance: targets, unknowns, history, budgets, replay."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from task_flow.contracts import ContractError,read_json
from tests.astral_rift_reference import replay
from workflow_tools.astral_rift_experiment import (ROOT,EXAMPLES,SCENARIO,build,enumeration_case,
    generation_case,run,verification_probes,verify)


class AstralRiftTests(unittest.TestCase):
    def setUp(self):
        self.scenario=read_json(SCENARIO)
        self.cases={c['id']:c for c in self.scenario['cases']}

    def test_seventeen_generation_expectations(self):
        records=[generation_case(self.scenario,c) for c in self.scenario['cases']]
        self.assertEqual(len(records),17)
        for r in records:
            self.assertTrue(r['expectation_met'],(r['case']['id'],r['actual'],r['case']['expected']))
        self.assertEqual(sum(r['actual'].get('candidate_count',0)>0 for r in records),10)

    def test_different_targets_have_expected_lengths_and_business_delays(self):
        for name,length,seconds in [('T1',2,5),('T2',4,14),('T3',6,22),('T4',4,10)]:
            record=generation_case(self.scenario,self.cases[name])
            self.assertEqual(len(record['actual']['first_steps']),length)
            self.assertEqual(record['actual']['first_delay_seconds'],seconds)
            self.assertEqual(record['search']['candidates'][0]['external_status'],'not_run')

    def test_history_is_explicit_not_silently_inferred_and_cost_is_new_work_only(self):
        with self.assertRaisesRegex(ContractError,'inconsistent initial history'):
            build(self.scenario,self.cases['inconsistent_history'])
        before=deepcopy(self.cases['plan_start'])
        record=generation_case(self.scenario,self.cases['plan_start'])
        self.assertEqual(record['actual']['first_steps'],['A1','A2'])
        self.assertEqual(record['actual']['first_delay_seconds'],8)
        self.assertEqual(self.cases['plan_start'],before)
        empty=generation_case(self.scenario,self.cases['goal_met'])
        self.assertEqual(empty['actual']['first_steps'],[])
        self.assertEqual(empty['actual']['first_delay_seconds'],0)

    def test_false_unknown_and_irrelevant_permission_are_distinguished(self):
        false=generation_case(self.scenario,self.cases['permission_false'])
        unknown=generation_case(self.scenario,self.cases['permission_unknown'])
        irrelevant=generation_case(self.scenario,self.cases['T1_permission_false'])
        self.assertEqual(false['actual']['conclusion'],'bounded_no_solution')
        self.assertEqual(unknown['actual']['conclusion'],'blocked_unknown')
        self.assertEqual(irrelevant['actual']['first_steps'],['S1','S2'])

    def test_delay_budget_is_not_search_runtime_and_equal_bound_is_inclusive(self):
        equal=generation_case(self.scenario,self.cases['delay_equal'])
        low=generation_case(self.scenario,self.cases['delay_insufficient'])
        self.assertEqual(equal['actual']['first_delay_seconds'],22)
        self.assertEqual(low['actual']['conclusion'],'bounded_no_solution')
        self.assertEqual(low['search']['coverage'],'complete')
        clock=generation_case(self.scenario,self.cases['time_limit'])
        self.assertEqual(clock['actual']['termination_reason'],'time_limit')
        self.assertEqual(clock['clock_kind'],'deterministic_test_clock')

    def test_wrong_workflows_reject_but_legal_optional_extension_passes(self):
        records=verification_probes(self.scenario)
        self.assertEqual(len(records),8)
        self.assertTrue(all(r['expectation_met'] for r in records),[(r['id'],r['verification'].get('failure')) for r in records])
        self.assertEqual(sum(r['verification']['verification_status']=='rejected' for r in records),7)
        self.assertEqual(next(r for r in records if r['id']=='valid_optional_extension')['verification']['verification_status'],'passed')

    def test_reference_uses_literal_rules_without_production_execution(self):
        catalog,request,initial=build(self.scenario,self.cases['T3'])
        steps=[{'activity_id':n,'activity_version':'1.0','parameters':{'rift':'rift-01'}} for n in ['S1','S2','D1','D2','A1','A2']]
        with patch('task_flow.state.apply_activity',side_effect=AssertionError('production execution used')):
            result=replay(initial,self.cases['T3']['goal_facts'],steps)
        self.assertTrue(result['passed'])
        self.assertEqual(result['delay_seconds'],22)

    def test_complete_enumeration_keeps_longer_legal_variants(self):
        result=enumeration_case(self.scenario,self.cases['T3'])
        self.assertTrue(result['expectation_met'])
        self.assertEqual(result['raw_sequences_checked'],109601)
        self.assertEqual(len(result['actual_sequences']),21)
        self.assertEqual({len(s) for s in result['actual_sequences']},{6,7,8})
        self.assertEqual(result['reference_min_length'],6)
        self.assertEqual(result['reference_min_delay_seconds'],22)

    def test_empty_goals_and_boolean_budgets_reject(self):
        for field,value in [('goal_facts',[]),('delay_budget_seconds',True)]:
            case=deepcopy(self.cases['T3']);case[field]=value
            with self.assertRaises(ContractError):
                build(self.scenario,case)

    def test_exported_data_runs_existing_cli_without_planner_changes(self):
        command=[sys.executable,'-B','-m','task_flow','plan','--catalog',str(EXAMPLES/'catalog.json'),
                 '--request',str(EXAMPLES/'requests/T3.json'),'--verify']
        result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout)
        self.assertEqual(len(report['candidates'][0]['sequence']['steps']),6)

    def test_complete_evidence_verifies_and_changed_records_are_detected(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            output=Path(directory)/'evidence'
            result=run(output)
            self.assertEqual(result['status'],'expectations_met_with_scope_limits')
            self.assertEqual(result['accepted_sequences'],{'T1':55,'T2':46,'T3':21})
            self.assertEqual(verify(output)['raw_reference_sequences_checked'],328803)
            with self.assertRaises(ContractError):
                run(output)
            manifest=json.loads((output/'manifest.json').read_text(encoding='utf-8'))
            modified=deepcopy(manifest);modified['source_sha256_lf_normalized']={}
            (output/'manifest.json').write_text(json.dumps(modified),encoding='utf-8')
            with self.assertRaisesRegex(ContractError,'incomplete'):
                verify(output)
            (output/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
            with (output/'generation-T3.json').open('a',encoding='utf-8') as stream:
                stream.write(' ')
            with self.assertRaisesRegex(ContractError,'artifact changed'):
                verify(output)


if __name__=='__main__':
    unittest.main()
