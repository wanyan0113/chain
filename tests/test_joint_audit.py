"""Post-publication audit probes for validator/producer contract parity."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import hashlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from design.engine import condition_result
from task_flow.joint import run_joint
from task_flow.joint_verify import _value
from tests.test_joint_integration import inputs,ROOT
from workflow_tools.audit_joint_implementation import export_snapshot,probe


class NumericContractAuditTests(unittest.TestCase):
    def test_quantity_equality_matches_producer_without_float_tolerance(self):
        values=[({'value':1,'unit':'1'},{'value':1.0,'unit':'1'}),
                ({'value':1.0,'unit':'1'},{'value':1,'unit':'1'}),
                ({'value':1,'unit':'1'},{'value':1.0000000000000002,'unit':'1'}),
                ({'value':1,'unit':'m'},{'value':1,'unit':'km'}),
                (True,1),(1,1.0),('1',1),(False,False),('x','x')]
        for left,right in values:
            expected=condition_result(dict(field='task.a',op='eq',value=right),{'task':{'a':left}})[0]=='pass'
            self.assertEqual(_value(left,right,'eq'),expected,(left,right))

    def test_no_hidden_epsilon_or_unit_conversion_in_order_comparisons(self):
        for op in ('lt','le','gt','ge'):
            for left,right in [({'value':1,'unit':'1'},{'value':1.0,'unit':'1'}),
                               ({'value':1,'unit':'1'},{'value':1.0000000000000002,'unit':'1'}),
                               ({'value':1,'unit':'m'},{'value':1,'unit':'km'})]:
                expected=condition_result(dict(field='task.a',op=op,value=right),{'task':{'a':left}})[0]=='pass'
                self.assertEqual(_value(left,right,op),expected,(op,left,right))


class SnapshotAuditTests(unittest.TestCase):
    def test_exact_revision_export_does_not_change_branch_or_real_index(self):
        head=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,check=True).stdout
        index_path=ROOT/subprocess.run(['git','rev-parse','--git-path','index'],cwd=ROOT,
                                      capture_output=True,text=True,check=True).stdout.strip()
        before=hashlib.sha256(index_path.read_bytes()).hexdigest()
        (ROOT/'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            target=Path(directory)/'snapshot'
            commit=export_snapshot('c17c515',target)
            self.assertTrue(commit.startswith('c17c515'))
            self.assertFalse((target/'.git').exists())
            self.assertFalse((target/'.publish').exists())
            observed=probe(target)
            self.assertEqual(observed['producer'],'pass')
            self.assertFalse(observed['verifier'])
        self.assertEqual(subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,check=True).stdout,head)
        self.assertEqual(hashlib.sha256(index_path.read_bytes()).hexdigest(),before)

    def test_snapshot_export_refuses_existing_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            with self.assertRaises(RuntimeError):export_snapshot('c17c515',Path(directory))


@unittest.skipUnless(importlib.util.find_spec('owlready2') and shutil.which('java'),'real HermiT/Java required')
class RealQuantityRuleAuditTests(unittest.TestCase):
    def test_integer_fact_and_decimal_quantity_rule_complete_real_pipeline(self):
        jobs,scene,mapping=inputs()
        (ROOT/'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            knowledge=Path(directory)
            for name in ('rift-multistep.ttl','flow-design-bridge.ttl','flow-design-bridge.rules.json'):
                shutil.copyfile(ROOT/'knowledge'/name,knowledge/name)
            path=knowledge/'flow-design-bridge.rules.json'
            rules=json.loads(path.read_text(encoding='utf-8'))
            next(r for r in rules['artifact_rules'] if r['id']=='WideSurvey')['when'].append(
                dict(field='task.target_count',op='eq',value={'value':1.0,'unit':'1'}))
            path.write_text(json.dumps(rules,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            with patch('design.knowledge.KNOWLEDGE_ROOT',knowledge):
                result=run_joint(jobs,scene,mapping)
            self.assertEqual(result['integration_status'],'selected')
            self.assertEqual(result['global_optimality'],'bounded_global_optimum')
            self.assertEqual(result['verification']['status'],'passed')
            self.assertEqual(result['verification']['verifier_version'],'tfdo-joint-verifier-1.0.1')


if __name__=='__main__':unittest.main()
