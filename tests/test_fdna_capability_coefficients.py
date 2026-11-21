from copy import deepcopy
import json
import math
from pathlib import Path
import unittest

from fdna.generation import NodeGenerator
from fdna.generation_coefficients import run_coefficient_check, affected_nodes
from fdna.generation_fitting import audit_generation
from fdna.model import ModelError

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'
def read(n):return json.loads((ROOT/n).read_text(encoding='utf-8'))


class CapabilityCoefficientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.net=read('generation_network.json');cls.nodes=read('generation_network_nodes.json')
        cls.config=read('generation_capability_coefficients.json')
        cls.config['reference_experiment']['base_experiment'].update(base_cases=30,train_count=24,repetitions=1,noise_std=[0,2])
        cls.g=run_coefficient_check(cls.net,cls.nodes,cls.config)

    def test_scalar_formulas_and_descendant_invariants(self):
        self.assertEqual(self.g['status'],'complete');self.assertTrue(all(self.g['checks'].values()))
        old={r['base_case_id']:r for r in self.g['clean_runs'] if r['group']=='varying_conditions'}
        for r in self.g['clean_runs']:
            if r['group'] not in self.config['condition_groups']:continue
            spec=self.config['condition_groups'][r['group']];v=r['node_values_clean'];c=r['conditions_by_node']
            factors={n:(1-(spec['value'] if n==spec['node'] else self.nodes['nodes'][n]['condition_function']['capability_gain']))+
                (spec['value'] if n==spec['node'] else self.nodes['nodes'][n]['condition_function']['capability_gain'])*c[n]['C'] for n in ('T','Q','R')}
            t=100*(1-(1-.9*v['A']/100)*(1-.78*v['B']/100))*factors['T']*(.82+.18*c['T']['M'])*(1-.28*c['T']['E'])
            q=math.sqrt(t*v['C_aux'])*factors['Q']*(.78+.22*c['Q']['M'])*(1-.35*c['Q']['E'])
            out=(.62*q+.38*v['D_aux'])*factors['R']*(.8+.2*c['R']['M'])*(1-.4*c['R']['E'])
            for n,expected in [('T',t),('Q',q),('R',out)]:self.assertAlmostEqual(v[n],expected,places=11)
            affected=affected_nodes(self.net,spec['node']);before=old[r['base_case_id']]['node_values_clean']
            for n in set(v)-affected:self.assertEqual(v[n],before[n])
            original=self.nodes['nodes'][spec['node']]['condition_function']['capability_gain']
            sign=1 if spec['value']<original else -1
            for n in affected:self.assertGreaterEqual(sign*(v[n]-before[n]),-1e-12)
        audit_generation(self.g)

    def test_normalized_boundary_and_equalities(self):
        for group,variant in self.g['variant_node_configs'].items():
            spec=self.config['condition_groups'][group];n=spec['node'];f=variant['nodes'][n]['condition_function']
            self.assertAlmostEqual(f['capability_base']+f['capability_gain'],1,places=14)
            self.assertEqual(f['capability_gain'],spec['value'])
            # At C=1 the normalized capability multiplier is exactly 1 regardless of h.
            a=NodeGenerator(variant['nodes'][n]);b=NodeGenerator(self.nodes['nodes'][n])
            inputs={p:60 for p in variant['nodes'][n]['parent_nodes']}
            for C in (1.,):
                self.assertAlmostEqual(a.generate(inputs,condition_values={'C':C,'M':1,'E':0})['receiver_se_clean'],
                    b.generate(inputs,condition_values={'C':C,'M':1,'E':0})['receiver_se_clean'],places=12)
        self.assertEqual(self.config['condition_groups']['cap_T090']['value'],.9)
        self.assertEqual(self.config['condition_groups']['cap_R090']['value'],.9)

    def test_illegal_boundary_and_duplicate_rejected(self):
        for value in (1.1,-.1,True,1.):
            c=deepcopy(self.config);c['condition_groups']['cap_T090']['value']=value
            with self.subTest(value=value),self.assertRaises(ModelError):run_coefficient_check(self.net,self.nodes,c)
        c=deepcopy(self.config);c['condition_groups']['duplicate']=c['condition_groups']['cap_T090']
        with self.assertRaises(ModelError):run_coefficient_check(self.net,self.nodes,c)

    def test_replay_detects_wrong_upstream_or_pair(self):
        for kind in ('output','pair'):
            g=deepcopy(self.g)
            if kind=='output':g['observations'][-1]['node_values_clean']['T']+=1
            else:g['variant_node_configs']['cap_T090']['nodes']['T']['condition_function']['capability_base']=0
            with self.subTest(kind=kind),self.assertRaises(ModelError):audit_generation(g)

    def test_small_complete_fit_and_report(self):
        from fdna.generation_varying import run_varying_network_check
        from fdna.generation_fitting import fit_joint_network
        from fdna.generation_delta_comparison import run_comparison
        from fdna.generation_delta_repeats import summarize_comparison
        from fdna.generation_capability_coefficient_report import build_report
        ref=run_varying_network_check(self.net,self.nodes,self.config['reference_experiment'])
        fc=read('generation_varying_fit.json');fc.update(noise_std=[0,2],repetitions=[1]);fc['settings']['starts']=3
        dc=read('generation_varying_delta.json');dc.update(noise_std=[0,2],repetitions=[1])
        base=fit_joint_network(ref,fc);comp=run_comparison(ref,base,dc,allow_repeats=True)
        cf=deepcopy(fc);cf['groups']=list(self.config['condition_groups']);cd=deepcopy(dc);cd['groups']=cf['groups']
        new=fit_joint_network(self.g,cf);result=run_comparison(self.g,new,cd,allow_repeats=True)
        summary=summarize_comparison(result);old_summary=summarize_comparison(comp)
        outputs=build_report(self.g,new,result,summary,ref,base,comp,old_summary)
        audit=json.loads(outputs['汇报来源核验.json'])
        self.assertEqual(audit['new_model_jobs'],24);self.assertEqual(audit['new_receiver_fits'],72)
        self.assertEqual(audit['invariant_local_fits'],24);self.assertEqual(audit['unchanged_metric_checks'],192)
        self.assertIn('能力条件系数代表性敏感性',outputs['capability_rmse.svg'].decode('utf-8'))
        self.assertIn('单侧',outputs['结果解释.md'])
        self.assertIn('30个基础网络',outputs['结果解释.md']);self.assertIn('3个起点',outputs['结果解释.md'])
        bad=deepcopy(result);bad['metrics'][0]['rmse']+=1
        with self.assertRaises(ModelError):build_report(self.g,new,bad,summary,ref,base,comp,old_summary)


if __name__=='__main__':unittest.main()
