from copy import deepcopy
from itertools import product
import random
import unittest

from task_flow.joint_network import optimize,network
from tests.test_sequence_table10 import fixture,shift
from tests.joint_reference import feasible


def inputs():
    scene,groups=fixture()
    scene['policies'].update(article_table10=True,network_search_method='branch_and_bound',
        max_network_combinations=10000,max_network_search_nodes=100000,cost_unit='u')
    return scene,groups,dict(communication_model='directed')


class JointNetworkTests(unittest.TestCase):
    def compare(self,scene,groups,mapping):
        expected=None
        for chosen in product(*groups):
            if not feasible(chosen,scene):continue
            key=(-sum(c['score'] for c in chosen)/len(chosen),tuple(c['id'] for c in chosen))
            if expected is None or key<expected:expected=key
        best,stats=optimize(groups,scene,mapping)
        self.assertEqual(None if best is None else best[0],expected)
        self.assertEqual(stats['optimality'],'proven' if expected is not None else 'proven_infeasible')
        return best,stats

    def test_OP01_OP02_local_low_score_resolves_resource_conflict(self):
        scene,groups,mapping=inputs()
        best,stats=self.compare(scene,groups,mapping)
        self.assertEqual({c['plan']['steps'][-1]['artifact'] for c in best[1]},{'act0','act1','act2'})
        self.assertTrue(any(c['score']<1 for c in best[1]))

    def test_OP05_randomized_independent_exhaustive_agreement(self):
        rng=random.Random(173)
        for _ in range(25):
            scene,groups,mapping=inputs()
            for group in groups:
                for c in group:c['score']=rng.choice([0,.2,.5,.8,1])
            if rng.random()<.5:scene['artifacts'][0]['attributes']['max_tracks']['value']=3
            self.compare(scene,groups,mapping)

    def test_OP06_branch_can_certify_without_visiting_all_networks(self):
        scene,groups,mapping=inputs()
        best,stats=self.compare(scene,groups,mapping)
        self.assertLess(stats['evaluated'],27)
        self.assertTrue(stats['search_complete'])

    def test_OP07_no_new_metric_only_dominance_pruning(self):
        scene,groups,mapping=inputs()
        for group in groups:group[2]['score']=.01
        best,_=self.compare(scene,groups,mapping)
        self.assertTrue(any(c['score']==.01 for c in best[1]))

    def test_OP08_network_objective_and_reported_totals_are_distinct(self):
        scene,groups,mapping=inputs()
        for group in groups:
            for index,chain in enumerate(group):
                chain['metrics']=dict(cost=10+index,time=10,accuracy=.9,cost_unit='u')
                chain['plan']['members']={role:list(dict.fromkeys(s['artifact'] for s in chain['plan']['steps'] if s['role']==role))
                                          for role in ('insight','coordination','manifestation')}
        best,_=self.compare(scene,groups,mapping)
        result=network(best[1],best[2],scene)
        self.assertEqual(result['score'],sum(c['score'] for c in best[1])/3)
        self.assertEqual(result['cost_per_execution'],sum(c['metrics']['cost'] for c in best[1]))
        self.assertEqual(result['makespan_s'],10)
        self.assertEqual(result['objective'],'mean_of_per_task_normalized_chain_scores')

    def test_OP09_strict_shared_count(self):
        scene,groups,mapping=inputs()
        scene['artifacts'][0]['attributes']['max_tracks']['value']=3
        best,stats=self.compare(scene,groups,mapping)
        self.assertIsNone(best)

    def test_OP09_cooldown_group_and_dependency_constraints(self):
        for kind in ('capacity','inventory','group','dependency','cooldown'):
            scene,groups,mapping=inputs()
            if kind=='capacity':scene['artifacts'][0]['capacity']=2
            elif kind=='inventory':
                for a in scene['artifacts'][2:]:a.update(consumable=True,inventory=0)
                for g in groups:
                    for c in g:c['plan']['resource_usage'][-1]['inventory']=1
            elif kind=='group':scene['resource_groups']=[dict(id='all-effect',capacity=2,artifacts=['act0','act1','act2'])]
            elif kind=='dependency':scene['dependencies']=[dict(before='t0',after='t1',gap_s=0)]
            else:
                for g in groups:
                    for c in g:
                        for u in c['plan']['resource_usage']:
                            if u['artifact']=='eye':u['end_s']+=3
                scene['artifacts'][0]['cooldown_s']=3;scene['artifacts'][0]['capacity']=3
            self.compare(scene,groups,mapping)

    def test_OP10_all_candidates_exist_but_network_infeasible(self):
        scene,groups,mapping=inputs()
        groups=[g[:1] for g in groups]
        best,stats=self.compare(scene,groups,mapping)
        self.assertIsNone(best)

    def test_PF03_PF04_network_budget_incumbent_and_no_incumbent(self):
        scene,groups,mapping=inputs()
        scene['policies']['max_network_search_nodes']=1
        best,stats=optimize(groups,scene,mapping)
        self.assertIsNone(best);self.assertEqual(stats['optimality'],'not_proven');self.assertFalse(stats['search_complete'])
        found=False
        for limit in range(2,30):
            scene['policies']['max_network_search_nodes']=limit
            best,stats=optimize(groups,scene,mapping)
            if best is not None and not stats['search_complete']:
                found=True;self.assertEqual(stats['optimality'],'not_proven');break
        self.assertTrue(found)

    def test_exhaustive_budget_and_optimizer_agreement(self):
        scene,groups,mapping=inputs()
        expected,_=optimize(groups,scene,mapping)
        scene['policies']['network_search_method']='exhaustive'
        actual,stats=self.compare(scene,groups,mapping)
        self.assertEqual(expected[0],actual[0]);self.assertEqual(stats['evaluated'],27)
        scene['policies']['max_network_combinations']=1
        _,stats=optimize(groups,scene,mapping)
        self.assertFalse(stats['search_complete']);self.assertEqual(stats['optimality'],'not_proven')


if __name__=='__main__':unittest.main()
