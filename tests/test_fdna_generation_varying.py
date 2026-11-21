from copy import deepcopy
import json
from pathlib import Path
import unittest

from fdna.generation_network import NetworkGenerator
from fdna.generation_network_check import run_network_check
from fdna.generation_varying import run_varying_network_check
from fdna.generation_fitting import audit_generation, fit_joint_network
from fdna.model import ModelError
from fdna.structure import digest

ROOT = Path(__file__).resolve().parents[1]/'data/fdna'


class VaryingConditionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read(n): return json.loads((ROOT/n).read_text(encoding='utf-8'))
        cls.network=read('generation_network.json'); cls.nodes=read('generation_network_nodes.json')
        cls.config=read('generation_varying_conditions.json')
        cls.config['base_experiment'].update(base_cases=40,train_count=32,repetitions=2,noise_std=[0,2])
        cls.report=run_varying_network_check(cls.network,cls.nodes,cls.config)

    def test_controls_and_pairing(self):
        r=self.report; old=run_network_check(self.network,self.nodes,self.config['base_experiment'])
        self.assertEqual(r['source_baseline_sha256'],digest(old))
        for key in ('root_inputs','clean_runs','observations'):
            self.assertEqual(r[key][:len(old[key])],old[key])
        self.assertTrue(all(r['checks'].values()))
        self.assertEqual(len(r['condition_samples']),240)
        self.assertEqual(len(r['structural_by_split']),12)
        audit_generation(r)

    def test_replay_detects_tampering(self):
        r=deepcopy(self.report); r['condition_samples'][0]['C']+=.01
        with self.assertRaises(ModelError): audit_generation(r)

    def test_rng_noise_order_and_node_declaration_independent(self):
        c=deepcopy(self.config); c['base_experiment']['noise_std'].reverse()
        network=deepcopy(self.network); network['nodes'].reverse(); network['edges'].reverse()
        other=run_varying_network_check(network,self.nodes,c)
        self.assertEqual(self.report['condition_samples'],other['condition_samples'])
        self.assertEqual(self.report['root_inputs'],other['root_inputs'])
        for row in self.report['condition_samples']:
            for key,(lo,hi) in c['condition_sampling']['ranges'].items():
                self.assertTrue(lo<=row[key]<=hi)
        first=[r for r in self.report['condition_samples'] if r['base_case_id']=='rep1:base0']
        self.assertEqual(len({tuple(r[k] for k in ('C','M','E')) for r in first}),3)

    def test_condition_override_isolated_and_validated(self):
        g=NetworkGenerator(self.network,self.nodes); roots={n:70 for n in g.roots}
        before=g.evaluate(roots); c={n:dict(C=.5,M=.4,E=.3) for n in g.receivers}
        after=g.evaluate(roots,condition_values_by_node=c)
        self.assertNotEqual(before['node_values_clean'],after['node_values_clean'])
        self.assertEqual(before,g.evaluate(roots))
        for invalid in ({},dict(c,A=dict(C=1,M=1,E=0))):
            with self.assertRaises(ModelError): g.evaluate(roots,condition_values_by_node=invalid)
        for value in (True,-.1,1.1,float('nan')):
            bad=deepcopy(c); bad['T']['C']=value
            with self.assertRaises(ModelError): g.evaluate(roots,condition_values_by_node=bad)

    def test_invalid_sampling(self):
        for change in ('mode','range','process'):
            c=deepcopy(self.config)
            if change=='mode': c['condition_sampling']['mode']='shared'
            elif change=='range': c['condition_sampling']['ranges']['C']=[1,.4]
            else: c['base_experiment']['process_std']=2
            with self.assertRaises(ModelError): run_varying_network_check(self.network,self.nodes,c)

    def test_small_fit_only_reads_se(self):
        config=json.loads((ROOT/'generation_varying_fit.json').read_text(encoding='utf-8'))
        config.update(noise_std=[0],repetitions=[1]); config['settings']['starts']=4
        result=fit_joint_network(self.report,config)
        self.assertEqual(result['status'],'complete')
        for local in result['jobs'][0]['local_fits']:
            for row in local['fit']['samples_snapshot']['records']:
                self.assertEqual(set(row),{'id','split','node_values'})
                self.assertEqual(set(row['node_values']),set(self.network['nodes']))

    def test_small_paired_delta_comparison(self):
        from fdna.generation_delta_comparison import run_comparison
        from fdna.generation_delta_repeats import summarize_comparison
        fc=json.loads((ROOT/'generation_varying_fit.json').read_text(encoding='utf-8'))
        fc.update(noise_std=[0],repetitions=[1]); fc['settings']['starts']=4
        base=fit_joint_network(self.report,fc)
        dc=json.loads((ROOT/'generation_varying_delta.json').read_text(encoding='utf-8'))
        dc.update(noise_std=[0],repetitions=[1])
        comparison=run_comparison(self.report,base,dc,allow_repeats=True)
        summary=summarize_comparison(comparison)
        self.assertEqual(summary['status'],'complete')
        self.assertEqual(summary['counts']['new_local_fits'],6)
        for mode in ('fixed_delta','free_delta'):
            for local in comparison['fits'][mode]['jobs'][0]['local_fits']:
                row=local['fit']['receivers'][0]
                if mode=='fixed_delta': self.assertEqual(row['delta'],-5)
                else: self.assertTrue(-10<=row['delta']<=10)


if __name__=='__main__': unittest.main()
