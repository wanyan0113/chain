"""Compare all feasible/unknown completions with an independent product audit."""
from copy import deepcopy
from itertools import product
from pathlib import Path
import random
import unittest

from design.article_integration import cooperation, cooperation_prefix
from design.engine import design, network_conflicts, SearchLimitError
from design.excel import read_design
from design.search import branch_and_bound

ROOT = Path(__file__).resolve().parents[1]


class Table10PruningTests(unittest.TestCase):
    def fixture(self):
        doc = read_design((ROOT / 'excel_entry/templates/表10设计演示.xlsx').read_bytes())
        task, context = deepcopy(doc['tasks'][0]), deepcopy(doc['contexts'][0])
        doc['tasks'] = [dict(deepcopy(task), id=f't{i}') for i in range(3)]
        doc['contexts'] = [dict(deepcopy(context), task=t['id']) for t in doc['tasks']]
        action, edge = deepcopy(doc['artifacts'][-1]), deepcopy(doc['links'][-1])
        doc['artifacts'] = doc['artifacts'][:2] + [dict(deepcopy(action), id=f'a{i}') for i in range(3)]
        doc['links'] = doc['links'][:1] + [dict(deepcopy(edge), to=f'a{i}') for i in range(3)]
        for artifact in doc['artifacts']:
            artifact.update(capacity=100, consumable=False, cooldown_s=0)
        groups = [[{'id': f'c{t}-{a}', 'task': f't{t}', 'start_s': 0, 'end_s': 3,
                    'artifacts': {'insight': 'demo-eye', 'coordination': 'demo-coordinator', 'manifestation': f'a{a}'},
                    'score': [1., .5, .5][a]} for a in range(3)] for t in range(3)]
        return doc, groups

    def compare(self, doc, groups):
        expected, visited, best = {}, {}, None
        for chosen in product(*groups):
            if network_conflicts(chosen, doc):
                continue
            result = cooperation(chosen, doc)
            ids = tuple(c['id'] for c in chosen)
            if result['status'] != 'fail':
                expected[ids] = result['status']
            if result['status'] == 'pass':
                key = (-sum(c['score'] for c in chosen) / len(groups), ids)
                if best is None or key < best:
                    best = key
        def check(chosen, document):
            if len(chosen) < len(groups):
                return cooperation_prefix(chosen, document)
            result = cooperation(chosen, document)
            if not network_conflicts(chosen, document) and result['status'] != 'fail':
                visited[tuple(c['id'] for c in chosen)] = result['status']
            return result
        actual, stats = branch_and_bound(groups, doc, network_conflicts, cooperation_check=check)
        self.assertEqual(visited, expected)  # includes every feasible AND unresolved completion
        self.assertEqual(None if actual is None else actual[0], best)
        self.assertEqual(stats['optimality'], 'not_proven' if 'unknown' in expected.values()
                         else 'proven' if best else 'proven_infeasible')
        return stats

    def test_known_failures_prune_before_complete_networks(self):
        for field, index in [('max_tracks', 0), ('max_processing_targets', 1)]:
            doc, groups = self.fixture()
            doc['artifacts'][index]['attributes'][field]['value'] = 2  # equality fails
            stats = self.compare(doc, groups)
            self.assertGreater(stats['pruned_table10'], 0)
            self.assertEqual(stats['evaluated'], 0)
        doc, groups = self.fixture()
        stats = self.compare(doc, groups)
        self.assertGreater(stats['pruned_table10'], 0)  # duplicate manifestation at depth two
        self.assertLess(stats['evaluated'], 27)

    def test_later_unknown_count_must_not_be_hidden_by_known_subtotal_failure(self):
        doc, groups = self.fixture()
        doc['artifacts'][0]['attributes']['max_tracks']['value'] = 2
        doc['tasks'][-1]['attributes'].pop('target_count')
        self.assertEqual(cooperation(groups[0][:1] + groups[1][1:2], doc)['status'], 'fail')
        self.assertEqual(cooperation_prefix(groups[0][:1] + groups[1][1:2], doc)['status'], 'unknown')
        self.assertEqual(self.compare(doc, groups)['optimality'], 'not_proven')

    def test_randomized_all_completions_match_with_overlap_boundaries_and_unknowns(self):
        rng = random.Random(1013)
        for trial in range(20):
            doc, groups = self.fixture()
            for task in doc['tasks']:
                task['attributes']['target_count'] = None if rng.randrange(5) == 0 else {'value': rng.randint(1, 3), 'unit': '1'}
            for index, field in [(0, 'max_tracks'), (1, 'max_processing_targets')]:
                doc['artifacts'][index]['attributes'][field] = None if rng.randrange(4) == 0 else {'value': rng.randint(2, 8), 'unit': '1'}
            for context in doc['contexts']:
                context['attributes']['target_distance'] = None if rng.randrange(6) == 0 else {'value': rng.choice([5, 10, 20]), 'unit': 'n mile'}
            for group in groups:
                for chain in group:
                    chain['start_s'] = rng.choice([0, 2, 3, 4, 6])
                    chain['end_s'] = chain['start_s'] + rng.choice([2, 3])
                    chain['score'] = rng.choice([0., .5, .5, 1.])
            with self.subTest(trial=trial):
                self.compare(doc, groups)

    def test_engine_exhaustive_branch_auto_and_limit(self):
        doc = read_design((ROOT / 'excel_entry/templates/表10设计演示.xlsx').read_bytes())
        doc['policies']['network_search_method'] = 'exhaustive'
        baseline = design(doc)
        doc['policies']['network_search_method'] = 'branch_and_bound'
        optimized = design(doc)
        self.assertEqual(optimized['network'], baseline['network'])
        self.assertEqual(optimized['article_table10']['selected'], baseline['article_table10']['selected'])
        self.assertEqual(optimized['tasks'], baseline['tasks'])
        doc['policies'].update(network_search_method='auto', max_network_combinations=1)
        self.assertEqual(design(doc)['network'], baseline['network'])
        doc['policies']['max_network_search_nodes'] = 1
        with self.assertRaises(SearchLimitError):
            design(doc)

    def compare_objective_search(self, doc, groups):
        doc['policies']['table10_score_bound'] = True
        known, pending = [], []
        for chosen in product(*groups):
            if network_conflicts(chosen, doc):
                continue
            state = cooperation(chosen, doc)['status']
            key = (-sum(c['score'] for c in chosen) / len(groups), tuple(c['id'] for c in chosen))
            if state == 'pass':
                known.append(key)
            elif state == 'unknown':
                pending.append(key)

        def check(chosen, document):
            return (cooperation_prefix if len(chosen) < len(groups) else cooperation)(chosen, document)

        actual, stats = branch_and_bound(groups, doc, network_conflicts, cooperation_check=check)
        expected = min(known) if known else None
        self.assertEqual(None if actual is None else actual[0], expected)
        unresolved = bool(pending) and (expected is None or min(pending) < expected)
        self.assertEqual(stats['optimality'], 'not_proven' if unresolved else
                         'proven' if expected is not None else 'proven_infeasible')
        return stats

    def test_table10_and_score_bound_both_prune(self):
        doc, groups = self.fixture()
        for index, field in [(0, 'max_tracks'), (1, 'max_processing_targets')]:
            doc['artifacts'][index]['attributes'][field]['value'] = 100
        stats = self.compare_objective_search(doc, groups)
        self.assertTrue(stats['score_bound_enabled'])
        self.assertGreater(stats['pruned_table10'], 0)
        self.assertGreater(stats['pruned_bound'], 0)
        doc['policies']['table10_score_bound'] = False
        self.assertLess(stats['evaluated'], self.compare(doc, groups)['evaluated'])

    def test_combined_pruning_randomized_optimum_and_unknowns_match_exhaustive(self):
        rng = random.Random(191119)
        for trial in range(30):
            doc, groups = self.fixture()
            for task in doc['tasks']:
                task['attributes']['target_count'] = None if rng.randrange(5) == 0 else {'value': rng.randint(1, 3), 'unit': '1'}
            for index, field in [(0, 'max_tracks'), (1, 'max_processing_targets')]:
                doc['artifacts'][index]['attributes'][field] = None if rng.randrange(5) == 0 else {'value': rng.randint(2, 9), 'unit': '1'}
            for group in groups:
                for chain in group:
                    chain['start_s'] = rng.choice([0, 2, 3, 6])
                    chain['end_s'] = chain['start_s'] + rng.choice([2, 3])
                    chain['score'] = rng.choice([0., .5, 1.])
            with self.subTest(trial=trial):
                self.compare_objective_search(doc, groups)

    def test_unknowns_compare_scores_and_deterministic_ties(self):
        def run(candidates):
            states = {identifier: state for identifier, _, state in candidates}
            groups = [[dict(id=identifier, score=score) for identifier, score, _ in candidates]]
            _, stats = branch_and_bound(groups, {'policies': {'table10_score_bound': True}},
                lambda chosen, doc: [], cooperation_check=lambda chosen, doc: {'status': states[chosen[0]['id']]})
            return stats['optimality']
        for candidates, expected in [
            ([('a', 1., 'pass'), ('b', .8, 'unknown')], 'proven'),
            ([('a', 1., 'unknown'), ('b', .8, 'pass')], 'not_proven'),
            ([('a', 1., 'unknown'), ('b', 1., 'pass')], 'not_proven'),
            ([('a', 1., 'pass'), ('b', 1., 'unknown')], 'proven'),
            ([('a', 1., 'unknown')], 'not_proven'),
            ([('a', 1., 'fail')], 'proven_infeasible'),
        ]:
            with self.subTest(candidates=candidates):
                self.assertEqual(run(candidates), expected)

    def test_unknown_seen_before_better_verified_solution_is_reassessed(self):
        # The optimistic first branch is unknown, but the next branch yields a
        # verified network with a better final score. Earlier uncertainty must
        # not leave a stale global "not_proven" flag.
        groups = [[{'id': 'a', 'score': 1.}, {'id': 'b', 'score': .9}],
                  [{'id': 'x', 'score': 1.}, {'id': 'y', 'score': 0.}]]
        def audit(chosen, doc):
            if len(chosen) == 1:
                return {'status': 'unknown'}
            ids = tuple(c['id'] for c in chosen)
            return {'status': 'unknown' if ids == ('a', 'y') else 'fail' if ids == ('a', 'x') else 'pass'}
        best, stats = branch_and_bound(groups, {'policies': {'table10_score_bound': True}},
                                      lambda chosen, doc: [], cooperation_check=audit)
        self.assertEqual(best[0], (-.95, ('b', 'x')))
        self.assertEqual(stats['unknown_combinations_seen'], 1)
        self.assertEqual(stats['optimality'], 'proven')

    def test_opt_in_engine_exhaustive_matches_combined_search(self):
        doc = read_design((ROOT / 'excel_entry/templates/表10设计演示.xlsx').read_bytes())
        doc['policies']['table10_score_bound'] = True
        reports = []
        for method in ('exhaustive', 'branch_and_bound'):
            doc['policies']['network_search_method'] = method
            reports.append(design(doc))
        self.assertEqual(reports[0]['network'], reports[1]['network'])
        self.assertEqual(reports[0]['network_search']['optimality'], reports[1]['network_search']['optimality'])
        # Include unknown completions and one independently verified solution.
        doc['tasks'][1]['start_s'] = 3
        doc['artifacts'][0]['attributes'].pop('max_tracks')
        reports = []
        for method in ('exhaustive', 'branch_and_bound'):
            doc['policies']['network_search_method'] = method
            reports.append(design(doc))
        self.assertEqual(reports[0]['status'], reports[1]['status'])
        self.assertEqual(reports[0]['network'], reports[1]['network'])
        self.assertEqual(reports[0]['network_search']['optimality'], reports[1]['network_search']['optimality'])
