"""Independent mechanisms, keyed parents, clipping order and noise isolation."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fdna import ModelError
from fdna.generation import NodeGenerator
from fdna.generation_check import main, run_unit_check

ROOT=Path(__file__).resolve().parents[1]


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/'data/fdna/generation_unit_check.json').read_text(encoding='utf-8'))

    def node(self,name,unit=False):
        config=deepcopy(self.config['presets'][name])
        if unit:
            config['condition_function']['values']=dict(C=1,M=1,E=0)
        return config

    def test_three_presets_match_hand_computed_values(self):
        for name,expected in [('tracking',63.9205146624),('fusion',63.235536),('execution',55.3472)]:
            with self.subTest(name=name):
                actual=NodeGenerator(self.node(name)).generate(dict(A=80,B=80))
                self.assertAlmostEqual(actual['receiver_se_clean'],expected,places=10)
                self.assertEqual(actual['receiver_se_observed'],actual['receiver_se_clean'])
        self.assertAlmostEqual(NodeGenerator(self.node('tracking',True)).generate(dict(A=80,B=80))['support'],.89472)

    def test_geometric_zero_weight_and_zero_input_boundaries(self):
        cfg=self.node('fusion',True)
        self.assertEqual(NodeGenerator(cfg).generate(dict(A=0,B=100))['receiver_se_clean'],0)
        cfg['parent_nodes']=['A','B','C']
        cfg['weights']=dict(A=0,B=1,C=0)
        self.assertAlmostEqual(NodeGenerator(cfg).generate(dict(A=0,B=40,C=0))['receiver_se_clean'],40)

    def test_three_and_five_parents_and_parent_order_invariance(self):
        for count in (3,5):
            cfg=self.node('execution',True)
            cfg['parent_nodes']=[f'P{i}' for i in range(count)]
            cfg['weights']={p:1/count for p in cfg['parent_nodes']}
            parents={p:10*(i+1) for i,p in enumerate(cfg['parent_nodes'])}
            original=NodeGenerator(cfg).generate(parents)
            cfg['parent_nodes'].reverse()
            reverse=NodeGenerator(cfg).generate(dict(reversed(list(parents.items()))))
            self.assertAlmostEqual(original['receiver_se_clean'],10*(count+1)/2)
            self.assertEqual(original,reverse)

    def test_interaction_process_and_observation_follow_the_frozen_order(self):
        generator=NodeGenerator(self.node('execution'))
        result=generator.generate(dict(A=80,B=80),process_noise=5,observation_noise=-3,interaction_gamma=.5)
        self.assertAlmostEqual(result['interaction_deduction'],2)
        self.assertAlmostEqual(result['receiver_se_clean'],58.3472)
        self.assertAlmostEqual(result['receiver_se_observed'],55.3472)
        high=generator.generate(dict(A=80,B=80),process_noise=200,observation_noise=-10)
        self.assertEqual(high['receiver_se_clean'],100)
        self.assertEqual(high['receiver_se_observed'],90)
        low=generator.generate(dict(A=80,B=80),process_noise=-200,observation_noise=10)
        self.assertEqual(low['receiver_se_clean'],0)
        self.assertEqual(low['receiver_se_observed'],10)

    def test_config_snapshot_and_unknown_inputs_are_not_silently_repaired(self):
        cfg=self.node('execution'); generator=NodeGenerator(cfg)
        cfg['weights']['A']=0
        snapshot=generator.config; snapshot['weights']['A']=0
        self.assertAlmostEqual(generator.generate(dict(A=80,B=80))['receiver_se_clean'],55.3472)
        for parents in [dict(A=80),dict(A=80,B=None),dict(A=True,B=80),dict(A=101,B=80),
                        dict(A=80,B=80,C=80),dict(A=float('nan'),B=80)]:
            with self.subTest(parents=parents),self.assertRaises(ModelError): generator.generate(parents)
        for noise in [True,float('inf')]:
            with self.assertRaises(ModelError): generator.generate(dict(A=80,B=80),observation_noise=noise)

    def test_invalid_keys_weights_coefficients_and_self_edges_are_rejected(self):
        for kind in ('keys','sum','negative','condition','unused','self','duplicate','infinite'):
            cfg=self.node('execution')
            if kind=='keys': cfg['weights']={'A':1,'C':0}
            if kind=='sum': cfg['weights']={'A':.3,'B':.3}
            if kind=='negative': cfg['weights']={'A':-.1,'B':1.1}
            if kind=='condition': cfg['condition_function']['task_gain']=.8
            if kind=='unused': cfg['support_coefficients']={'A':1,'B':1}
            if kind=='self': cfg['node_id']='A'
            if kind=='duplicate': cfg['parent_nodes']=['A','A']
            if kind=='infinite': cfg['weights']['A']=float('inf')
            with self.subTest(kind=kind),self.assertRaises(ModelError): NodeGenerator(cfg)

    def small_config(self):
        config=deepcopy(self.config)
        config.update(base_cases=20,train_count=16,noise_std=[0,2])
        return config

    def test_unit_experiment_is_reproducible_and_does_not_call_fdna(self):
        cfg=self.small_config()
        with patch('fdna.engine.evaluate',side_effect=AssertionError('FDNA forward forbidden')), \
             patch('fdna.fitting.fit_parameters',side_effect=AssertionError('FDNA fitting forbidden')):
            report=run_unit_check(cfg)
        self.assertEqual(report,run_unit_check(cfg))
        self.assertEqual(len(report['rows']),240)
        self.assertTrue(all(r['passed'] for r in report['checks']))
        self.assertFalse(report['policies']['network_connected'])
        self.assertEqual(len({r['base_case_id'] for r in report['rows']}),20)

    def test_noise_grid_does_not_change_roots_clean_states_or_shared_versions(self):
        cfg=self.small_config(); first=run_unit_check(cfg)
        cfg['noise_std']=[0,8,2]; second=run_unit_check(cfg)
        def shared(rows):
            return {(r['base_case_id'],r['preset'],r['group'],r['noise_sigma']):r
                    for r in rows if r['noise_sigma'] in (0,2)}
        self.assertEqual(shared(first['rows']),shared(second['rows']))
        for row in second['rows']:
            if row['noise_sigma']==0:
                self.assertEqual(row['receiver_se_clean'],row['receiver_se_observed'])
        groups={}
        for row in second['rows']:
            groups.setdefault((row['base_case_id'],row['preset'],row['group']),[]).append(row)
        self.assertTrue(all(len({r['receiver_se_clean'] for r in values})==1 for values in groups.values()))

    def test_process_noise_switch_preserves_roots_and_observation_draws(self):
        cfg=self.small_config(); first=run_unit_check(cfg)
        cfg['process_std']=2; second=run_unit_check(cfg)
        self.assertTrue(any(a['receiver_se_clean']!=b['receiver_se_clean'] for a,b in zip(first['rows'],second['rows'])))
        self.assertTrue(all((a['A'],a['B'],a['observation_noise'],a['split'])==
                            (b['A'],b['B'],b['observation_noise'],b['split'])
                            for a,b in zip(first['rows'],second['rows'])))

    def test_cli_exports_without_overwriting_and_rejects_bad_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'config.json'
            path.write_text(json.dumps(self.small_config()),encoding='utf-8')
            args=['--config',str(path),'--output-dir',str(root/'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args),0)
                self.assertEqual(main(args),2)
            self.assertTrue((root/'out/unit_samples.csv').is_file())
        cfg=self.small_config(); cfg['train_count']=20
        with self.assertRaises(ModelError): run_unit_check(cfg)


if __name__=='__main__':
    unittest.main()
