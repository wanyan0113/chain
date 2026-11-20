"""Joint DAG generation: continuity, noise separation, paired sampling and splits."""
from contextlib import redirect_stdout
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fdna import ModelError
from fdna.generation_network import NetworkGenerator
from fdna.generation_network_check import main, network_csv, run_network_check

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'


class GenerationNetworkTests(unittest.TestCase):
    def setUp(self):
        self.network=json.loads((ROOT/'generation_network.json').read_text(encoding='utf-8'))
        self.nodes=json.loads((ROOT/'generation_network_nodes.json').read_text(encoding='utf-8'))
        self.config=json.loads((ROOT/'generation_network_check.json').read_text(encoding='utf-8'))
        self.config.update(base_cases=20,train_count=16,noise_std=[0,2])
        self.roots=dict(A=80,B=80,C_aux=80,D_aux=80)

    def generator(self):
        return NetworkGenerator(self.network,self.nodes)

    def test_manual_three_layer_values_and_exact_parent_handoff(self):
        result=self.generator().evaluate(self.roots)
        clean=result['node_values_clean']
        self.assertAlmostEqual(clean['T'],63.9205146624,places=10)
        self.assertAlmostEqual(clean['Q'],56.52444959945873,places=10)
        self.assertAlmostEqual(clean['R'],45.277578630751506,places=10)
        self.assertEqual(result['node_details']['Q']['parent_clean']['T'],clean['T'])
        self.assertEqual(result['node_details']['R']['parent_clean']['Q'],clean['Q'])
        self.assertEqual(result['node_values_observed'],clean)
        self.assertEqual(result['topological_order'],['A','B','C_aux','D_aux','T','Q','R'])

    def test_observation_noise_on_roots_and_intermediate_nodes_does_not_propagate(self):
        generator=self.generator(); clean=generator.evaluate(self.roots)
        noisy=generator.evaluate(self.roots,observation_noise=dict(A=100,T=-200,Q=10,R=-5))
        self.assertEqual(clean['node_values_clean'],noisy['node_values_clean'])
        self.assertEqual(noisy['node_values_observed']['A'],100)
        self.assertEqual(noisy['node_values_observed']['T'],0)
        self.assertTrue(noisy['node_details']['T']['observation_clipped'])
        self.assertEqual(noisy['node_details']['Q']['parent_clean']['T'],clean['node_values_clean']['T'])

    def test_effective_process_noise_propagates_only_along_dependencies(self):
        generator=self.generator(); baseline=generator.evaluate(self.roots)
        changed=generator.evaluate(self.roots,process_noise={'T':1})
        for n in ('T','Q','R'):
            self.assertGreater(changed['node_values_clean'][n],baseline['node_values_clean'][n])
        for n in self.roots:
            self.assertEqual(changed['node_values_clean'][n],baseline['node_values_clean'][n])

    def test_declaration_order_does_not_change_evaluation_or_joint_samples(self):
        baseline=self.generator().evaluate(self.roots)
        original=run_network_check(self.network,self.nodes,self.config)
        self.network['nodes'].reverse(); self.network['edges'].reverse()
        self.nodes['nodes']=dict(reversed(list(self.nodes['nodes'].items())))
        self.config['groups']=dict(reversed(list(self.config['groups'].items())))
        self.assertEqual(baseline,self.generator().evaluate(self.roots))
        reordered=run_network_check(self.network,self.nodes,self.config)
        self.assertEqual(original['observations'],reordered['observations'])
        self.assertEqual(original['clean_runs'],reordered['clean_runs'])

    def test_rejects_cycles_unknown_edges_and_mismatched_node_configs(self):
        for kind in ('cycle','unknown','duplicate','self','missing_config','extra_config','parents','inner_id'):
            network,nodes=deepcopy((self.network,self.nodes))
            if kind=='cycle': network['edges'].append(dict(feeder='R',receiver='A'))
            if kind=='unknown': network['edges'][0]['feeder']='absent'
            if kind=='duplicate': network['edges'].append(deepcopy(network['edges'][0]))
            if kind=='self': network['edges'][0]['receiver']='A'
            if kind=='missing_config': nodes['nodes'].pop('Q')
            if kind=='extra_config': nodes['nodes']['extra']=deepcopy(nodes['nodes']['T'])
            if kind=='parents': nodes['nodes']['Q']['parent_nodes']=['A','B']
            if kind=='inner_id': nodes['nodes']['Q']['node_id']='QQ'
            with self.subTest(kind=kind),self.assertRaises(ModelError): NetworkGenerator(network,nodes)

    def test_rejects_missing_root_values_and_invalid_noise(self):
        generator=self.generator()
        for roots in [dict(A=80),dict(self.roots,A=None),dict(self.roots,A=True),dict(self.roots,A=101)]:
            with self.assertRaises(ModelError): generator.evaluate(roots)
        for kw in [dict(process_noise={'A':2}),dict(observation_noise={'absent':1}),
                   dict(process_noise={'T':float('nan')}),dict(observation_noise={'T':True})]:
            with self.assertRaises(ModelError): generator.evaluate(self.roots,**kw)

    def test_joint_sampling_is_paired_grouped_and_independent_of_fdna(self):
        with patch('fdna.engine.evaluate',side_effect=AssertionError('FDNA forward forbidden')), \
             patch('fdna.fitting.fit_parameters',side_effect=AssertionError('FDNA fit forbidden')):
            r=run_network_check(self.network,self.nodes,self.config)
        self.assertTrue(all(r['checks'].values()))
        self.assertEqual(r['counts']['network_observation_versions'],80)
        self.assertEqual(r['counts']['node_observation_rows'],560)
        self.assertEqual(r['root_category_counts'],dict(normal=10,single=4,double=4,all=2))
        for o in r['observations']:
            if o['noise_sigma']==0: self.assertEqual(o['node_values_clean'],o['node_values_observed'])
        splits={b['base_case_id']:b['split'] for b in r['root_inputs']}
        self.assertEqual(sum(v=='train' for v in splits.values()),16)
        self.assertTrue(all(o['split']==splits[o['base_case_id']] for o in r['observations']))
        self.assertEqual(len(list(csv.DictReader(io.StringIO(network_csv(r))))),560)

    def test_noise_grid_and_process_switch_preserve_paired_roots_and_draws(self):
        first=run_network_check(self.network,self.nodes,self.config)
        self.config['noise_std']=[8,0,2]
        changed=run_network_check(self.network,self.nodes,self.config)
        self.assertEqual(first['clean_runs'],changed['clean_runs'])
        self.assertEqual(first['root_inputs'],changed['root_inputs'])
        old={r['observation_id']:r for r in first['observations']}
        self.assertEqual(old,{r['observation_id']:r for r in changed['observations'] if r['noise_sigma'] in (0,2)})
        self.config['process_std']=2
        process=run_network_check(self.network,self.nodes,self.config)
        self.assertEqual(process['root_inputs'],changed['root_inputs'])
        self.assertNotEqual(process['clean_runs'],changed['clean_runs'])
        self.assertEqual([r['observation_noise'] for r in process['observations']],
                         [r['observation_noise'] for r in changed['observations']])

    def test_repetitions_reproduce_first_run_and_snapshot_does_not_alias_inputs(self):
        first=run_network_check(self.network,self.nodes,self.config)
        self.config['repetitions']=2
        second=run_network_check(self.network,self.nodes,self.config)
        self.assertEqual(first['observations'],[r for r in second['observations'] if r['repetition']==1])
        self.network['nodes'].reverse()
        self.nodes['basis']='changed'
        self.config['seed']=1
        self.assertNotEqual(second['network_snapshot'],self.network)
        self.assertNotEqual(second['node_configs_snapshot'],self.nodes)
        self.assertNotEqual(second['config_snapshot']['seed'],self.config['seed'])

    def test_invalid_experiment_and_cli_no_overwrite(self):
        for kind in ('split','fraction','interval','zero_noise'):
            config=deepcopy(self.config)
            if kind=='split': config['train_count']=20
            if kind=='fraction': config['root_sampling']['fractions']['normal']=.4
            if kind=='interval': config['root_sampling']['normal']=[.98,.65]
            if kind=='zero_noise': config['noise_std']=[2]
            with self.subTest(kind=kind),self.assertRaises(ModelError): run_network_check(self.network,self.nodes,config)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); args=[]
            for name,value in [('network',self.network),('nodes',self.nodes),('config',self.config)]:
                path=root/(name+'.json'); path.write_text(json.dumps(value),encoding='utf-8')
                args+=['--'+name,str(path)]
            args+=['--output-dir',str(root/'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args),0)
                self.assertEqual(main(args),2)
            self.assertTrue((root/'out/node_samples.csv').is_file())


if __name__=='__main__':
    unittest.main()
