from copy import deepcopy
import json
from pathlib import Path
import unittest

from fdna.generation_coefficients import run_coefficient_check
from fdna.generation_fitting import audit_generation, fit_joint_network
from fdna.generation_delta_comparison import run_comparison
from fdna.generation_delta_repeats import summarize_comparison
from fdna.generation_varying import run_varying_network_check
from fdna.generation_condition_coefficient_report import build_report
from fdna.model import ModelError

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'
def read(n):return json.loads((ROOT/n).read_text(encoding='utf-8'))


class ConditionCoefficientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network=read('generation_network.json');cls.nodes=read('generation_network_nodes.json')
        cls.config=read('generation_condition_coefficients.json')
        cls.config['reference_experiment']['base_experiment'].update(base_cases=30,train_count=24,repetitions=1,noise_std=[0,2])
        cls.g=run_coefficient_check(cls.network,cls.nodes,cls.config)

    def test_formula_pair_constraint_and_invariant_nodes(self):
        self.assertEqual(self.g['status'],'complete');self.assertTrue(all(self.g['checks'].values()))
        originals={r['base_case_id']:r for r in self.g['clean_runs'] if r['group']=='varying_conditions'}
        for r in self.g['clean_runs']:
            if r['group'] not in self.config['condition_groups']:continue
            before=originals[r['base_case_id']];v=r['node_values_clean'];c=r['conditions_by_node']['R']
            f=self.g['variant_node_configs'][r['group']]['nodes']['R']['condition_function']
            expected=(.62*v['Q']+.38*v['D_aux'])*c['C']*(f['task_base']+f['task_gain']*c['M'])*(1-f['environment_penalty']*c['E'])
            self.assertAlmostEqual(v['R'],expected,places=11)
            self.assertAlmostEqual(f['task_base']+f['task_gain'],1,places=14)
            self.assertEqual({n:x for n,x in v.items() if n!='R'},
                {n:x for n,x in before['node_values_clean'].items() if n!='R'})
            self.assertEqual(r['conditions_by_node'],before['conditions_by_node'])
            # Increasing either penalty or gain (at fixed normalized endpoint) cannot improve output for M<=1,E>=0.
            spec=self.config['condition_groups'][r['group']];baseline=.2 if spec['factor']=='task_gain' else .4
            sign=1 if spec['value']<baseline else -1
            self.assertGreaterEqual(sign*(v['R']-before['node_values_clean']['R']),-1e-12)
        audit_generation(self.g)

    def test_invalid_or_unchanged_configs_rejected(self):
        for change in ({'node':'missing'},{'factor':'unknown_gain'},{'value':1.1},{'value':True},{'value':.2},{'extra':1}):
            c=deepcopy(self.config);c['condition_groups']['condition_task010'].update(change)
            with self.subTest(change=change),self.assertRaises(ModelError):run_coefficient_check(self.network,self.nodes,c)
        c=deepcopy(self.config);c['support_groups']={}
        with self.assertRaises(ModelError):run_coefficient_check(self.network,self.nodes,c)

    def test_replay_detects_output_and_pair_tampering(self):
        for kind in ('output','pair'):
            g=deepcopy(self.g)
            if kind=='output':g['observations'][-1]['node_values_clean']['R']+=.1
            else:g['variant_node_configs']['condition_task010']['nodes']['R']['condition_function']['task_base']=.8
            with self.subTest(kind=kind),self.assertRaises(ModelError):audit_generation(g)

    def test_small_fitting_and_report_pipeline(self):
        ref=run_varying_network_check(self.network,self.nodes,self.config['reference_experiment'])
        fc=read('generation_varying_fit.json');fc.update(noise_std=[0,2],repetitions=[1]);fc['settings']['starts']=3
        dc=read('generation_varying_delta.json');dc.update(noise_std=[0,2],repetitions=[1])
        b=fit_joint_network(ref,fc);c=run_comparison(ref,b,dc,allow_repeats=True)
        cf=deepcopy(fc);cf['groups']=list(self.config['condition_groups']);cd=deepcopy(dc);cd['groups']=cf['groups']
        new=fit_joint_network(self.g,cf);result=run_comparison(self.g,new,cd,allow_repeats=True)
        summary=summarize_comparison(result);old=summarize_comparison(c)
        outputs=build_report(self.g,new,result,summary,ref,b,c,old)
        audit=json.loads(outputs['汇报来源核验.json'])
        self.assertEqual(audit['new_model_jobs'],24);self.assertEqual(audit['paired_observation_checks'],240)
        self.assertEqual(audit['unchanged_node_metric_checks'],384)
        self.assertIn('T/Q目标不变',outputs['结果解释.md'])
        for name in ('condition_rmse.png','condition_bound.svg','all_condition_delta.csv'):self.assertIn(name,outputs)
        self.assertIn('末端条件函数系数敏感性',outputs['condition_rmse.svg'].decode('utf-8'))
        bad=deepcopy(result);bad['metrics'][0]['rmse']+=1
        with self.assertRaises(ModelError):build_report(self.g,new,bad,summary,ref,b,c,old)


if __name__=='__main__':unittest.main()
