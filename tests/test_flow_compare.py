from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from fdna.flow_compare import JSON_FILES,compare
from fdna.flow_scenario import file_inventory


class FlowCompareTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.left=Path(self.temp.name)/'left';self.right=Path(self.temp.name)/'right'
        for root in (self.left,self.right):
            for name in JSON_FILES:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
                p.write_text(json.dumps(dict(jobs=[dict(local_fits=[dict(fit=dict(solver=dict(numpy='version-a',nfev=10)))])]) if name.endswith('workflow_report.json') else {}),encoding='utf-8')
            (root/'00_冻结输入/场景设计.md').write_text('frozen plan',encoding='utf-8')
            np.savez_compressed(root/'03_事件与SE/微观事件.npz',events=np.asarray([0,1]))
            self.refresh(root)

    def refresh(self,root):
        (root/'完整包校验.json').write_text(json.dumps(file_inventory(root)),encoding='utf-8')

    def test_only_explicit_environment_fields_may_differ(self):
        p=self.right/'04_FDNA拟合/workflow_report.json';r=json.loads(p.read_text(encoding='utf-8'));r['jobs'][0]['local_fits'][0]['fit']['solver']['numpy']='version-b'
        p.write_text(json.dumps(r),encoding='utf-8');self.refresh(self.right)
        result=compare(self.left,self.right)
        self.assertEqual(result['status'],'passed');self.assertEqual(len(result['environment_differences']),1)

    def test_changed_numerical_result_is_not_hidden_by_normalization(self):
        p=self.right/'04_FDNA拟合/workflow_report.json';r=json.loads(p.read_text(encoding='utf-8'));r['jobs'][0]['local_fits'][0]['fit']['solver']['nfev']=11
        p.write_text(json.dumps(r),encoding='utf-8');self.refresh(self.right)
        self.assertEqual(compare(self.left,self.right)['status'],'different')

    def test_micro_events_are_compared_as_arrays(self):
        np.savez_compressed(self.right/'03_事件与SE/微观事件.npz',events=np.asarray([1,1]));self.refresh(self.right)
        self.assertIn('微观事件.npz/events',compare(self.left,self.right)['differences'])

    def test_unrecorded_file_change_fails_before_comparison(self):
        (self.right/'03_事件与SE/观测SE.json').write_text('[]',encoding='utf-8')
        with self.assertRaises(ValueError):compare(self.left,self.right)


if __name__=='__main__':unittest.main()
