from copy import deepcopy
import json
import math
from pathlib import Path
import unittest

from fdna.generation_coefficients import run_coefficient_check
from fdna.generation_fitting import audit_generation
from fdna.model import ModelError

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'
def read(n):return json.loads((ROOT/n).read_text(encoding='utf-8'))


class SupportCoefficientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network=read('generation_network.json');cls.nodes=read('generation_network_nodes.json')
        cls.config=read('generation_support_conditions.json')
        cls.config['reference_experiment']['base_experiment'].update(base_cases=30,train_count=24,repetitions=1,noise_std=[0,2])
        cls.g=run_coefficient_check(cls.network,cls.nodes,cls.config)

    def test_formula_and_clean_state_propagation(self):
        self.assertEqual(self.g['status'],'complete');self.assertTrue(all(self.g['checks'].values()))
        for r in self.g['clean_runs']:
            if r['group'] not in self.config['support_groups']:continue
            v=r['node_values_clean'];c=r['conditions_by_node'];a=self.config['support_groups'][r['group']]
            t=100*(1-(1-a['A']*v['A']/100)*(1-a['B']*v['B']/100))*c['T']['C']*(.82+.18*c['T']['M'])*(1-.28*c['T']['E'])
            q=math.sqrt(t*v['C_aux'])*(.55+.45*c['Q']['C'])*(.78+.22*c['Q']['M'])*(1-.35*c['Q']['E'])
            out=(.62*q+.38*v['D_aux'])*c['R']['C']*(.8+.2*c['R']['M'])*(1-.4*c['R']['E'])
            for n,expected in [('T',t),('Q',q),('R',out)]:
                self.assertAlmostEqual(v[n],expected,places=11)
        self.assertEqual(self.g['node_configs_snapshot'],self.nodes)
        audit_generation(self.g)

    def test_direction_and_roots_unchanged(self):
        old={r['base_case_id']:r for r in self.g['clean_runs'] if r['group']=='varying_conditions'}
        for r in self.g['clean_runs']:
            if r['group'] not in self.config['support_groups']:continue
            original=old[r['base_case_id']]['node_values_clean'];a=self.config['support_groups'][r['group']]
            sign=1 if a['A']>.9 or a['B']>.78 else -1
            for n in ('T','Q','R'):self.assertGreaterEqual(sign*(r['node_values_clean'][n]-original[n]),-1e-12)
            for n in self.g['topology']['roots']:self.assertEqual(original[n],r['node_values_clean'][n])
        self.assertEqual(self.g['variant_node_configs']['support_a100']['nodes']['T']['support_coefficients'],{'A':1.,'B':.78})

    def test_rejects_joint_changes_invalid_ranges_and_mixed_kinds(self):
        for value in ({'A':.8,'B':.68},{'A':1.1,'B':.78},{'A':True,'B':.78},
                      {'A':.9,'B':.78},{'A':.8}):
            c=deepcopy(self.config);c['support_groups']['support_a080']=value
            with self.subTest(value=value),self.assertRaises(ModelError):run_coefficient_check(self.network,self.nodes,c)
        c=deepcopy(self.config);c['weight_groups']={}
        with self.assertRaises(ModelError):run_coefficient_check(self.network,self.nodes,c)

    def test_output_and_variant_tampering_rejected(self):
        for kind in ('output','variant'):
            g=deepcopy(self.g)
            if kind=='output':g['observations'][-1]['node_values_clean']['Q']+=.1
            else:g['variant_node_configs']['support_a080']['nodes']['T']['support_coefficients']['A']=.7
            with self.subTest(kind=kind),self.assertRaises(ModelError):audit_generation(g)

    def test_small_fitting_report_and_source_validation(self):
        from fdna.generation_varying import run_varying_network_check
        from fdna.generation_fitting import fit_joint_network
        from fdna.generation_delta_comparison import run_comparison
        from fdna.generation_delta_repeats import summarize_comparison
        from fdna.generation_support_report import build_report
        reference=run_varying_network_check(self.network,self.nodes,self.config['reference_experiment'])
        fc=read('generation_varying_fit.json');fc.update(noise_std=[0,2],repetitions=[1]);fc['settings']['starts']=3
        dc=read('generation_varying_delta.json');dc.update(noise_std=[0,2],repetitions=[1])
        base=fit_joint_network(reference,fc);comparison=run_comparison(reference,base,dc,allow_repeats=True)
        cf=deepcopy(fc);cf['groups']=list(self.config['support_groups']);cd=deepcopy(dc);cd['groups']=cf['groups']
        new=fit_joint_network(self.g,cf);result=run_comparison(self.g,new,cd,allow_repeats=True)
        s=summarize_comparison(result);old_s=summarize_comparison(comparison)
        outputs=build_report(self.g,new,result,s,reference,base,comparison,old_s)
        audit=json.loads(outputs['汇报来源核验.json'])
        self.assertEqual(audit['paired_observation_checks'],240)
        self.assertEqual(audit['structural_checks'],48)
        self.assertEqual(audit['new_receiver_fits'],72)
        text=outputs['结果解释.md']
        self.assertIn('30个基础网络',text);self.assertIn('单次，无SD',text)
        for name in ('support_rmse.png','support_residual.svg','support_bound.png'):self.assertTrue(outputs[name])
        bad=deepcopy(result);bad['metrics'][0]['rmse']+=1
        with self.assertRaises(ModelError):build_report(self.g,new,bad,s,reference,base,comparison,old_s)


if __name__=='__main__':unittest.main()
