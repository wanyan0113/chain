from copy import deepcopy
import json
import math
from pathlib import Path
import unittest

from fdna.generation_network import NetworkGenerator
from fdna.generation_process import run_process_network_check
from fdna.generation_varying import run_varying_network_check
from fdna.generation_fitting import audit_generation,fit_joint_network
from fdna.generation_delta_comparison import run_comparison
from fdna.generation_delta_repeats import summarize_comparison
from fdna.model import ModelError
from fdna.structure import digest

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'
def read(name): return json.loads((ROOT/name).read_text(encoding='utf-8'))


class ProcessGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network=read('generation_network.json');cls.nodes=read('generation_network_nodes.json')
        cls.config=read('generation_process_conditions.json')
        cls.config['reference_experiment']['base_experiment'].update(base_cases=40,train_count=32,repetitions=2,noise_std=[0,2])
        cls.report=run_process_network_check(cls.network,cls.nodes,cls.config)

    def test_frozen_reference_replay_and_counts(self):
        r=self.report;ref=run_varying_network_check(self.network,self.nodes,self.config['reference_experiment'])
        self.assertEqual(r['source_varying_generation_sha256'],digest(ref))
        for key in ('root_inputs','clean_runs','observations'): self.assertEqual(r[key][:len(ref[key])],ref[key])
        self.assertEqual(len(r['process_samples']),240)
        self.assertEqual(len(r['process_structural_by_split']),12)
        self.assertTrue(all(r['checks'].values()));audit_generation(r)

    def test_process_rng_exact_and_independent_from_noise_order(self):
        import numpy as np
        c=deepcopy(self.config);c['reference_experiment']['base_experiment']['noise_std'].reverse()
        other=run_process_network_check(self.network,self.nodes,c)
        self.assertEqual(self.report['process_samples'],other['process_samples'])
        rep=np.random.SeedSequence(c['reference_experiment']['base_experiment']['seed']).spawn(2)[0]
        rng=np.random.default_rng(rep.spawn(5)[2])
        first={r['node']:r['process_noise'] for r in self.report['process_samples'] if r['base_case_id']=='rep1:base0'}
        for node in sorted(first): self.assertEqual(first[node],float(2*rng.standard_normal(40)[0]))

    def test_task_formula_recomputed_on_process_parents(self):
        group=self.config['group_id']
        rows=[r for r in self.report['clean_runs'] if r['group']==group]
        for r in rows:
            v=r['node_values_clean'];c=r['conditions_by_node'];noise=r['process_noise_by_node']
            factors={'T':c['T']['C']*(.82+.18*c['T']['M'])*(1-.28*c['T']['E']),
                'Q':(.55+.45*c['Q']['C'])*(.78+.22*c['Q']['M'])*(1-.35*c['Q']['E']),
                'R':c['R']['C']*(.8+.2*c['R']['M'])*(1-.4*c['R']['E'])}
            formulas={'T':100*(1-(1-.9*v['A']/100)*(1-.78*v['B']/100))*factors['T'],
                'Q':math.sqrt(v['T']*v['C_aux'])*factors['Q'],
                'R':(.62*v['Q']+.38*v['D_aux'])*factors['R']}
            for n,formula in formulas.items(): self.assertAlmostEqual(v[n],min(100,max(0,formula+noise[n])),places=11)

    def test_process_propagates_measurement_does_not(self):
        g=NetworkGenerator(self.network,self.nodes);roots={n:70 for n in g.roots}
        base=g.evaluate(roots)
        process=g.evaluate(roots,process_noise={'T':10})
        obs=g.evaluate(roots,observation_noise={'T':10})
        self.assertEqual(obs['node_values_clean'],base['node_values_clean'])
        for n in ('T','Q','R'): self.assertGreater(process['node_values_clean'][n],base['node_values_clean'][n])
        clipped=g.evaluate(roots,process_noise={'T':-1000,'Q':1000})
        self.assertEqual(clipped['node_values_clean']['T'],0)
        self.assertEqual(clipped['node_values_clean']['Q'],100)
        self.assertTrue(clipped['node_details']['T']['process_clipped'])

    def test_tampered_innovation_rejected(self):
        r=deepcopy(self.report);r['process_samples'][0]['process_noise']+=1
        with self.assertRaises(ModelError): audit_generation(r)

    def test_invalid_configuration(self):
        for value in (0,-2,True,float('inf')):
            c=deepcopy(self.config);c['process_std']=value
            with self.assertRaises(ModelError): run_process_network_check(self.network,self.nodes,c)
        c=deepcopy(self.config);c['source_group']='fixed_conditions'
        with self.assertRaises(ModelError): run_process_network_check(self.network,self.nodes,c)

    def test_small_fit_delta_and_matching_bound(self):
        fc=read('generation_process_fit.json');fc.update(noise_std=[0],repetitions=[1]);fc['settings']['starts']=4
        base=fit_joint_network(self.report,fc)
        self.assertEqual(base['status'],'complete')
        bounds={(r['node'],r['split']):r['clean_local_base_rmse_lower_bound'] for r in self.report['process_structural_by_split'] if r['repetition']==1}
        for m in base['jobs'][0]['metrics']:
            if m['target']=='clean' and m['mode']=='local': self.assertAlmostEqual(m['rmse_lower_bound'],bounds[m['node'],m['split']])
        dc=read('generation_process_delta.json');dc.update(noise_std=[0],repetitions=[1])
        summary=summarize_comparison(run_comparison(self.report,base,dc,allow_repeats=True))
        self.assertEqual(summary['status'],'complete');self.assertEqual(summary['counts']['new_local_fits'],6)

    def test_report_pairs_by_identity_and_rejects_missing_or_duplicate_rows(self):
        from fdna.generation_process_report import paired_process_changes
        ref={'metrics':[dict(model='base',group='varying_conditions',noise_sigma=0,repetition=i,node='R',
            split='holdout',target='clean',prediction='recursive',count=8,rmse=i+2,mae=i+1,mean_residual=i) for i in (1,2)]}
        current=deepcopy(ref)
        for r in current['metrics']:r.update(group=self.config['group_id'],rmse=r['rmse']+1)
        current['metrics'].reverse()
        rows=paired_process_changes(current,ref,'varying_conditions')
        self.assertEqual([r['rmse_change'] for r in rows],[1,1])
        for bad in ({'metrics':current['metrics'][:1]}, {'metrics':current['metrics']*2}):
            with self.assertRaises(ModelError):paired_process_changes(bad,ref,'varying_conditions')


if __name__=='__main__': unittest.main()
