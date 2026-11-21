from copy import deepcopy
import json
import math
from pathlib import Path
import unittest

from fdna.generation import NodeGenerator
from fdna.generation_coefficients import run_coefficient_check
from fdna.generation_fitting import audit_generation, fit_joint_network
from fdna.generation_delta_comparison import run_comparison
from fdna.generation_delta_repeats import summarize_comparison
from fdna.generation_varying import run_varying_network_check
from fdna.generation_upstream_coefficient_report import build_report
from fdna.model import ModelError

ROOT = Path(__file__).resolve().parents[1] / 'data/fdna'
def read(name): return json.loads((ROOT/name).read_text(encoding='utf-8'))


class UpstreamCoefficientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.net = read('generation_network.json'); cls.nodes = read('generation_network_nodes.json')
        cls.config = read('generation_upstream_coefficients.json')
        cls.config['reference_experiment']['base_experiment'].update(base_cases=30, train_count=24, repetitions=1, noise_std=[0, 2])
        cls.g = run_coefficient_check(cls.net, cls.nodes, cls.config)

    def test_matches_previous_prespecified_plan_and_protocol(self):
        planned = read('generation_condition_sensitivity_plan.json')['batches']['upstream_task_environment']
        self.assertEqual(self.config['condition_groups'], planned)
        fc = read('generation_upstream_coefficients_fit.json'); dc = read('generation_upstream_coefficients_delta.json')
        for field in ('settings', 'noise_std', 'repetitions'):
            self.assertEqual(fc[field], read('generation_varying_fit.json')[field])
        for field in ('noise_std', 'repetitions', 'fixed_delta', 'free_delta_bounds'):
            self.assertEqual(dc[field], read('generation_varying_delta.json')[field])
        self.assertEqual(fc['groups'], list(planned)); self.assertEqual(dc['groups'], list(planned))

    def test_independent_scalar_formulas_directions_and_invariants(self):
        self.assertEqual(self.g['status'], 'complete')
        old = {r['base_case_id']: r for r in self.g['clean_runs'] if r['group'] == 'varying_conditions'}
        affected = {'T': {'T', 'Q', 'R'}, 'Q': {'Q', 'R'}}
        for row in self.g['clean_runs']:
            if row['group'] not in self.config['condition_groups']: continue
            spec = self.config['condition_groups'][row['group']]; v = row['node_values_clean']; c = row['conditions_by_node']
            task = {'T': .18, 'Q': .22, 'R': .2}; env = {'T': .28, 'Q': .35, 'R': .4}
            (task if spec['factor'] == 'task_gain' else env)[spec['node']] = spec['value']
            multipliers = {n: (1-task[n]+task[n]*c[n]['M'])*(1-env[n]*c[n]['E']) for n in task}
            t = 100*(1-(1-.9*v['A']/100)*(1-.78*v['B']/100))*c['T']['C']*multipliers['T']
            q = math.sqrt(t*v['C_aux'])*(.55+.45*c['Q']['C'])*multipliers['Q']
            r = (.62*q+.38*v['D_aux'])*c['R']['C']*multipliers['R']
            for n, expected in [('T', t), ('Q', q), ('R', r)]: self.assertAlmostEqual(v[n], expected, places=11)
            before = old[row['base_case_id']]
            self.assertEqual(c, before['conditions_by_node']); self.assertEqual(row['split'], before['split'])
            for n in set(v)-affected[spec['node']]: self.assertEqual(v[n], before['node_values_clean'][n])
            original = self.nodes['nodes'][spec['node']]['condition_function'][spec['factor']]
            sign = 1 if spec['value'] < original else -1
            for n in affected[spec['node']]: self.assertGreaterEqual(sign*(v[n]-before['node_values_clean'][n]), -1e-12)
        audit_generation(self.g)

    def test_endpoint_equalities_and_only_one_function_change(self):
        for group, variant in self.g['variant_node_configs'].items():
            spec = self.config['condition_groups'][group]; n = spec['node']; f = variant['nodes'][n]['condition_function']
            self.assertAlmostEqual(f['task_base']+f['task_gain'], 1, places=14)
            # At M=1 or E=0 the corresponding factor is invariant to its coefficient.
            values = {'C': .7, 'M': 1., 'E': 0.} if spec['factor'] == 'task_gain' else {'C': .7, 'M': .3, 'E': 0.}
            inputs = {p: 60 for p in variant['nodes'][n]['parent_nodes']}
            a = NodeGenerator(variant['nodes'][n]).generate(inputs, condition_values=values)['receiver_se_clean']
            b = NodeGenerator(self.nodes['nodes'][n]).generate(inputs, condition_values=values)['receiver_se_clean']
            self.assertAlmostEqual(a, b, places=12)
            expected = deepcopy(self.nodes); expected['nodes'][n]['condition_function'][spec['factor']] = spec['value']
            if spec['factor'] == 'task_gain': expected['nodes'][n]['condition_function']['task_base'] = 1-spec['value']
            self.assertEqual(variant, expected)

    def test_replay_rejects_wrong_linked_base_and_upstream_output(self):
        for kind in ('base', 'output'):
            bad = deepcopy(self.g)
            if kind == 'base': bad['variant_node_configs']['T_task_gain_008']['nodes']['T']['condition_function']['task_base'] = .82
            else: bad['observations'][-1]['node_values_clean']['T'] += 1
            with self.subTest(kind=kind), self.assertRaises(ModelError): audit_generation(bad)

    def test_small_complete_fit_report_and_tampering(self):
        ref = run_varying_network_check(self.net, self.nodes, self.config['reference_experiment'])
        fc = read('generation_varying_fit.json'); fc.update(noise_std=[0, 2], repetitions=[1]); fc['settings']['starts'] = 3
        dc = read('generation_varying_delta.json'); dc.update(noise_std=[0, 2], repetitions=[1])
        base = fit_joint_network(ref, fc); comp = run_comparison(ref, base, dc, allow_repeats=True)
        cf = deepcopy(fc); cf['groups'] = list(self.config['condition_groups']); cd = deepcopy(dc); cd['groups'] = cf['groups']
        new = fit_joint_network(self.g, cf); result = run_comparison(self.g, new, cd, allow_repeats=True)
        summary = summarize_comparison(result); old = summarize_comparison(comp)
        outputs = build_report(self.g, new, result, summary, ref, base, comp, old)
        audit = json.loads(outputs['汇报来源核验.json'])
        self.assertEqual(audit['new_model_jobs'], 48); self.assertEqual(audit['new_receiver_fits'], 144)
        self.assertEqual(audit['invariant_local_fits'], 24); self.assertEqual(audit['unchanged_metric_checks'], 192)
        self.assertEqual(audit['paired_observation_checks'], 480); self.assertEqual(audit['structural_checks'], 96)
        self.assertIn('T/Q条件系数敏感性', outputs['upstream_rmse_sigma0.svg'].decode('utf-8'))
        self.assertIn('30个基础网络', outputs['结果解释.md']); self.assertIn('3个起点', outputs['结果解释.md'])
        for kind in ('metric', 'generation', 'summary'):
            bad_g = deepcopy(self.g); bad_c = deepcopy(result); bad_s = deepcopy(summary)
            if kind == 'metric': bad_c['metrics'][0]['rmse'] += 1
            elif kind == 'generation': bad_g['coefficient_config_snapshot']['condition_groups']['T_task_gain_008']['value'] = .09
            else: bad_s['comparison_sha256'] = 'invalid'
            with self.subTest(kind=kind), self.assertRaises(ModelError):
                build_report(bad_g, new, bad_c, bad_s, ref, base, comp, old)


if __name__ == '__main__': unittest.main()
