"""Compare optimized selection with exhaustive selection, including ties."""
from copy import deepcopy
from itertools import product
import json
from pathlib import Path
import random
import unittest

from design.engine import design, network_conflicts, SearchLimitError
from design.plans import attach_plan, member_ids
from design.search import branch_and_bound

ROOT = Path(__file__).resolve().parents[1]


class NetworkSearchTests(unittest.TestCase):
    def fixture(self):
        return json.loads((ROOT / 'design/examples/canonical_network_demo.json').read_text(encoding='utf-8'))

    def test_engine_modes_preserve_scores_metrics_ties_and_plan_evidence(self):
        doc = self.fixture()
        original = deepcopy(doc)
        baseline = design(doc)
        self.assertEqual(doc, original)
        doc['policies']['network_search_method'] = 'branch_and_bound'
        optimized = design(doc)
        self.assertEqual(baseline['network'], optimized['network'])
        self.assertEqual(baseline['tasks'], optimized['tasks'])
        self.assertEqual(optimized['network_search']['optimality'], 'proven')
        chain = optimized['tasks'][0]['chains'][0]
        self.assertEqual(chain['plan']['members']['manifestation'], [chain['artifacts']['manifestation']])
        self.assertEqual(len(chain['plan']['resource_usage']), 3)
        self.assertEqual(member_ids(chain), set(chain['artifacts'].values()))

    def test_auto_switch_and_explicit_node_limit_never_claim_partial_optimality(self):
        doc = self.fixture()
        doc['policies']['max_network_combinations'] = 1
        report = design(doc)
        self.assertEqual(report['network_search']['algorithm'], 'branch_and_bound')
        self.assertEqual(report['network_search']['optimality'], 'proven')
        doc['policies']['max_network_search_nodes'] = 1
        with self.assertRaises(SearchLimitError):
            design(doc)

    def test_randomized_search_matches_exhaustive_with_capacity_inventory_time_and_dependencies(self):
        rng = random.Random(906)
        for trial in range(65):
            artifacts = [{'id': str(i), 'capacity': rng.randint(1, 3), 'inventory': rng.randint(1, 3),
                          'consumable': bool(rng.randrange(2)), 'cooldown_s': rng.choice([0, 1])} for i in range(4)]
            doc = {'artifacts': artifacts, 'policies': {'max_network_search_nodes': 10000},
                   'dependencies': [{'before': 't2', 'after': 't0', 'gap_s': 0}] if trial % 3 == 0 else [],
                   'resource_groups': [{'id': 'shared', 'capacity': rng.randint(1, 3), 'artifacts': ['0', '1']}]}
            groups = []
            for task in range(3):
                group = []
                for option in range(4):
                    start = rng.choice([0, 2, 4])
                    group.append({'id': f'c{task}-{option}', 'task': f't{task}',
                                  'artifacts': {'any_role': str(rng.randrange(4))},
                                  'start_s': start, 'end_s': start + rng.choice([1, 2]),
                                  'score': rng.choice([0., .2, .2, .6, .6000000000000001, 1.])})
                groups.append(group)
            best = None
            for chosen in product(*groups):
                if network_conflicts(chosen, doc):
                    continue
                score = sum(c['score'] for c in chosen) / len(groups)
                key = (-score, tuple(c['id'] for c in chosen))
                if best is None or key < best:
                    best = key
            optimized, stats = branch_and_bound(groups, doc, network_conflicts)
            self.assertEqual(None if optimized is None else optimized[0], best, trial)
            self.assertEqual(stats['optimality'], 'proven' if best else 'proven_infeasible')

    def test_generic_plan_reservations_support_multiple_members_and_touching_intervals(self):
        doc = {'artifacts': [{'id': x, 'capacity': 1, 'consumable': True, 'inventory': 1, 'cooldown_s': 0} for x in ('a', 'b')], 'policies': {}}
        chain = {'task': 't0', 'start_s': 0, 'end_s': 1,
                 'plan': {'members': {'manifestation': ['a', 'b']}, 'resource_usage': [
                     {'artifact': x, 'capacity': 1, 'inventory': 1, 'start_s': 0, 'end_s': 1} for x in ('a', 'b')]}}
        self.assertEqual(member_ids(chain), {'a', 'b'})
        self.assertFalse(network_conflicts([chain], doc))
        other = deepcopy(chain)
        other['task'] = 't1'
        for use in other['plan']['resource_usage']:
            use.update(start_s=1, end_s=2)
        conflicts = network_conflicts([chain, other], doc)
        self.assertEqual({c['code'] for c in conflicts}, {'INVENTORY'})

    def test_article_unknown_audit_survives_pruned_search(self):
        from design.excel import read_design
        doc = read_design((ROOT / 'excel_entry/templates/表10设计演示.xlsx').read_bytes())
        doc['tasks'][1]['start_s'] = 3
        doc['artifacts'][0]['attributes'].pop('max_tracks')
        doc['policies'].update(article_table10=True, network_search_method='branch_and_bound')
        report = design(doc)
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['network_search']['algorithm'], 'branch_and_bound')
        self.assertEqual(report['network_search']['optimality'], 'not_proven')
        self.assertFalse(report['network_search']['score_bound_enabled'])
        self.assertTrue(any(c['status'] == 'pass' and not c['engineering_conflicts']
                            for c in report['article_table10']['combinations']))

    def test_twelve_tasks_with_256_candidates_each_are_solved_without_cross_product(self):
        artifacts = [{'id': f'a{i}', 'capacity': 1, 'consumable': True, 'inventory': 1, 'cooldown_s': 0} for i in range(32)]
        doc = {'artifacts': artifacts, 'policies': {'max_network_search_nodes': 10000}}
        groups = [[{'id': f'c{t}-{i:03}', 'task': f't{t}', 'artifacts': {'manifestation': f'a{i%32}'},
                    'score': 1. if i % 32 < 12 else .4, 'start_s': 0, 'end_s': 1}
                   for i in range(256)] for t in range(12)]
        best, stats = branch_and_bound(groups, doc, network_conflicts)
        self.assertEqual(best[2], 1.)
        self.assertEqual(stats['candidate_combinations'], 256 ** 12)
        self.assertLess(stats['nodes'], 10000)
        self.assertFalse(network_conflicts(best[1], doc))
