from copy import deepcopy
import json
import math
from pathlib import Path
import unittest

from fdna.generation_interaction import run_interaction_network_check
from fdna.generation_varying import run_varying_network_check
from fdna.generation_network import NetworkGenerator
from fdna.generation_fitting import audit_generation,fit_joint_network
from fdna.generation_delta_comparison import run_comparison
from fdna.generation_delta_repeats import summarize_comparison
from fdna.model import ModelError
from fdna.structure import digest

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'
def read(name):return json.loads((ROOT/name).read_text(encoding='utf-8'))


class InteractionGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network=read('generation_network.json');cls.nodes=read('generation_network_nodes.json')
        cls.config=read('generation_interaction_conditions.json')
        cls.config['reference_experiment']['base_experiment'].update(base_cases=40,train_count=32,repetitions=2,noise_std=[0,2])
        cls.report=run_interaction_network_check(cls.network,cls.nodes,cls.config)

    def test_frozen_reference_and_replay(self):
        ref=run_varying_network_check(self.network,self.nodes,self.config['reference_experiment'])
        self.assertEqual(self.report['source_varying_generation_sha256'],digest(ref))
        for key in ('root_inputs','clean_runs','observations'):self.assertEqual(self.report[key][:len(ref[key])],ref[key])
        self.assertTrue(all(self.report['checks'].values()))
        self.assertEqual(len(self.report['interaction_samples']),720)
        audit_generation(self.report)

    def test_independent_formula_recomputation(self):
        for row in self.report['clean_runs']:
            if row['group'] not in self.config['gamma_groups']:continue
            v=row['node_values_clean'];c=row['conditions_by_node'];gamma=row['interaction_gamma']
            support={'T':100*(1-(1-.9*v['A']/100)*(1-.78*v['B']/100)),
                'Q':math.sqrt(v['T']*v['C_aux']),'R':.62*v['Q']+.38*v['D_aux']}
            factors={'T':c['T']['C']*(.82+.18*c['T']['M'])*(1-.28*c['T']['E']),
                'Q':(.55+.45*c['Q']['C'])*(.78+.22*c['Q']['M'])*(1-.35*c['Q']['E']),
                'R':c['R']['C']*(.8+.2*c['R']['M'])*(1-.4*c['R']['E'])}
            for n in ('T','Q','R'):
                expected=min(100,max(0,support[n]*factors[n]-100*gamma*c[n]['E']*(1-c[n]['C'])))
                self.assertAlmostEqual(v[n],expected,places=11)
                self.assertEqual(row['node_details'][n]['process_noise'],0)

    def test_gamma_order_monotone_and_noise_pairing(self):
        by_key={(r['group'],r['base_case_id']):r for r in self.report['clean_runs']}
        groups=['varying_conditions']+[g for g,v in sorted(self.config['gamma_groups'].items(),key=lambda pair:pair[1])]
        for base in self.report['root_inputs']:
            rows=[by_key[g,base['base_case_id']] for g in groups]
            for n in self.report['topology']['receivers']:
                values=[r['node_values_clean'][n] for r in rows]
                self.assertTrue(all(a+1e-10>=b for a,b in zip(values,values[1:])))
        other=deepcopy(self.config)
        other['gamma_groups']=dict(reversed(list(other['gamma_groups'].items())))
        other['reference_experiment']['base_experiment']['noise_std'].reverse()
        result=run_interaction_network_check(self.network,self.nodes,other)
        self.assertEqual(self.report['interaction_samples'],result['interaction_samples'])

    def test_zero_interaction_and_local_boundary_conditions(self):
        g=NetworkGenerator(self.network,self.nodes);roots={n:60 for n in g.roots}
        for values in (dict(C=1,M=.5,E=.7),dict(C=.5,M=.5,E=0)):
            c={n:values.copy() for n in g.receivers}
            zero=g.evaluate(roots,condition_values_by_node=c)
            interaction=g.evaluate(roots,condition_values_by_node=c,interaction_gamma=.3)
            self.assertEqual(zero['node_values_clean'],interaction['node_values_clean'])
        c={n:dict(C=.4,M=.5,E=.8) for n in g.receivers};c['Q']['C']=1
        zero=g.evaluate(roots,condition_values_by_node=c)
        result=g.evaluate(roots,condition_values_by_node=c,interaction_gamma=.3)
        self.assertEqual(result['node_details']['Q']['interaction_deduction'],0)
        self.assertLess(result['node_values_clean']['Q'],zero['node_values_clean']['Q'])

    def test_clipping_and_observation_isolation(self):
        g=NetworkGenerator(self.network,self.nodes);roots={n:0 for n in g.roots}
        c={n:dict(C=.4,M=0,E=.8) for n in g.receivers}
        result=g.evaluate(roots,condition_values_by_node=c,interaction_gamma=.3)
        self.assertTrue(all(result['node_values_clean'][n]==0 for n in g.receivers))
        self.assertTrue(all(result['node_details'][n]['process_clipped'] for n in g.receivers))
        observed=g.evaluate(roots,condition_values_by_node=c,interaction_gamma=.3,observation_noise={'T':10})
        self.assertEqual(result['node_values_clean'],observed['node_values_clean'])

    def test_tampering_and_invalid_configurations(self):
        r=deepcopy(self.report);r['interaction_samples'][0]['local_interaction_deduction']+=1
        with self.assertRaises(ModelError):audit_generation(r)
        for value in (0,-.1,True,float('inf')):
            c=deepcopy(self.config);c['gamma_groups']['interaction_015']=value
            with self.assertRaises(ModelError):run_interaction_network_check(self.network,self.nodes,c)
        for change in ('primary','duplicate','process'):
            c=deepcopy(self.config)
            if change=='primary':c['primary_group']='unknown'
            elif change=='duplicate':c['gamma_groups']['interaction_015']=.05
            else:c['reference_experiment']['base_experiment']['process_std']=2
            with self.assertRaises(ModelError):run_interaction_network_check(self.network,self.nodes,c)

    def test_primary_fit_and_delta_only_select_requested_group(self):
        fc=read('generation_interaction_fit.json');fc.update(noise_std=[0],repetitions=[1]);fc['settings']['starts']=4
        base=fit_joint_network(self.report,fc)
        self.assertEqual(base['status'],'complete');self.assertEqual(len(base['jobs']),1)
        self.assertEqual(base['jobs'][0]['group'],'interaction_015')
        dc=read('generation_interaction_delta.json');dc.update(noise_std=[0],repetitions=[1])
        summary=summarize_comparison(run_comparison(self.report,base,dc,allow_repeats=True))
        self.assertEqual(summary['status'],'complete');self.assertEqual(summary['counts']['new_local_fits'],6)

    def test_report_pairing_uses_sample_group_identity(self):
        from fdna.generation_interaction_report import paired_interaction_changes
        ref={'metrics':[dict(model='base',group='varying_conditions',noise_sigma=0,repetition=i,node='R',
            split='holdout',target='clean',prediction='recursive',count=8,rmse=i+2,mae=i+1,mean_residual=i) for i in (1,2)]}
        current=deepcopy(ref)
        for r in current['metrics']:r.update(group='interaction_015',rmse=r['rmse']+1)
        current['metrics'].reverse()
        result=paired_interaction_changes(current,ref,'varying_conditions')
        self.assertEqual([r['rmse_change'] for r in result],[1,1])
        for bad in ({'metrics':current['metrics'][:1]},{'metrics':current['metrics']*2}):
            with self.assertRaises(ModelError):paired_interaction_changes(bad,ref,'varying_conditions')


if __name__=='__main__':unittest.main()
