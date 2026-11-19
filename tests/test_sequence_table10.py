"""Multistep table-10 semantics and exact search versus exhaustive audit."""
from copy import deepcopy
from itertools import product
import random
import unittest

from design.article_integration import cooperation, cooperation_prefix
from design.engine import network_conflicts
from design.search import branch_and_bound
from design.sequences import SEQUENCES


def quantity(value, unit='1'):
    return {'value': value, 'unit': unit}


def fixture():
    doc = {'scope': 'synthetic ordered table10 check', 'policies': {}, 'resource_groups': [],
           'tasks': [], 'artifacts': [], 'contexts': [], 'links': []}
    roles = [('eye', 'insight'), ('coord', 'coordination')] + [(f'act{i}', 'manifestation') for i in range(3)]
    role_by_id = dict(roles)
    for identifier, role in roles:
        doc['artifacts'].append(dict(id=identifier, role=role, capacity=100, consumable=False,
            inventory=0, cooldown_s=0, source_refs=['synthetic'], blocking_issue_ids=[],
            attributes={'detection_range': quantity(10, 'range'), 'max_tracks': quantity(4),
                        'max_processing_targets': quantity(4)}))
    for a, _ in roles:
        for b, _ in roles:
            if a != b:
                doc['links'].append(dict(**{'from': a, 'to': b}, communicates=True, controls=True,
                    distance=quantity(1, 'range'), max_distance=quantity(10, 'range'),
                    source_refs=['synthetic'], blocking_issue_ids=[]))
    groups = []
    for i in range(3):
        task = dict(id=f't{i}', attributes={'target_count': quantity(1)}, blocking_issue_ids=[])
        doc['tasks'].append(task)
        doc['contexts'].append(dict(task=task['id'], artifact='eye',
            attributes={'target_distance': quantity(5, 'range')}, source_refs=['synthetic']))
        group = []
        for j in range(3):
            identifiers = ['eye', 'coord', 'eye', 'coord', f'act{j}']
            steps = [dict(id=f's{k}', artifact=a, role=role_by_id[a],
                          start_s=k * 2, end_s=k * 2 + 2) for k, a in enumerate(identifiers)]
            group.append(dict(id=f'c{i}-{j}', task=task['id'], start_s=0, end_s=10, score=[1., .5, .1][j],
                plan=dict(mode='ordered_steps', steps=steps,
                    connections=[{'from': a, 'to': b, 'relation': 'control' if k == 3 else 'communication'}
                                 for k, (a, b) in enumerate(zip(identifiers, identifiers[1:]))],
                    resource_usage=[dict(artifact=s['artifact'], capacity=1, inventory=0,
                                         start_s=s['start_s'], end_s=s['end_s']) for s in steps])))
        groups.append(group)
    return doc, groups


def shift(chain, offset):
    chain['start_s'] += offset
    chain['end_s'] += offset
    for record in chain['plan']['steps'] + chain['plan']['resource_usage']:
        record['start_s'] += offset
        record['end_s'] += offset


class SequenceTable10Tests(unittest.TestCase):
    def test_repeated_roles_count_each_chain_once_and_strict_threshold(self):
        doc, groups = fixture()
        chosen = [groups[0][0], groups[1][1]]
        doc['artifacts'][0]['attributes']['max_tracks'] = quantity(3)
        report = cooperation(chosen, doc)
        self.assertEqual(report['status'], 'pass')
        self.assertEqual([g['start_s'] for g in report['groups'] if g['artifact'] == 'eye'], [0, 4])
        sums = [c['evidence']['right']['value'] for g in report['groups'] for c in g['checks']
                if c['id'] == 'shared-max-tracks']
        self.assertEqual(sums, [2, 2])
        doc['artifacts'][0]['attributes']['max_tracks'] = quantity(2)
        self.assertEqual(cooperation(chosen, doc)['status'], 'fail')
        self.assertEqual(cooperation_prefix(chosen, doc)['status'], 'fail')

    def test_activity_intervals_not_whole_chain_or_cooldown(self):
        doc, groups = fixture()
        chosen = [groups[0][0], groups[1][1]]
        shift(chosen[1], 2)
        for artifact in doc['artifacts'][:2]:
            artifact['attributes']['max_tracks'] = quantity(1)
            artifact['attributes']['max_processing_targets'] = quantity(1)
        doc['artifacts'][0]['cooldown_s'] = 50
        self.assertEqual(cooperation(chosen, doc)['groups'], [])
        self.assertEqual(cooperation(chosen, doc)['status'], 'pass')

    def test_manifestation_exclusive_across_chains_even_serial(self):
        doc, groups = fixture()
        chosen = [groups[0][0], groups[1][0]]
        shift(chosen[1], 20)
        self.assertEqual(cooperation_prefix(chosen, doc)['status'], 'fail')
        repeated = deepcopy(groups[0][0]['plan']['steps'][-1])
        repeated.update(id='repeat-act', start_s=12, end_s=14)
        groups[0][0]['plan']['steps'].append(repeated)
        self.assertEqual(cooperation(groups[0][:1], doc)['status'], 'pass')

    def test_range_missing_count_and_unknown_must_not_be_pruned(self):
        doc, groups = fixture()
        chosen = [groups[0][0], groups[1][1]]
        doc['contexts'][0]['attributes']['target_distance'] = quantity(10, 'range')
        self.assertEqual(cooperation_prefix(chosen, doc)['status'], 'fail')
        doc['contexts'][0]['attributes']['target_distance'] = quantity(5, 'range')
        doc['artifacts'][0]['attributes']['max_tracks'] = quantity(2)
        doc['tasks'][2]['attributes']['target_count'] = None
        self.assertEqual(cooperation(chosen, doc)['status'], 'fail')
        self.assertEqual(cooperation_prefix(chosen, doc)['status'], 'unknown')
        chosen.append(groups[2][2])
        self.assertEqual(cooperation(chosen, doc)['status'], 'unknown')

    def test_different_executors_for_same_role_checked_separately(self):
        doc, groups = fixture()
        second = deepcopy(doc['artifacts'][0])
        second['id'] = 'eye2'
        second['attributes']['max_tracks'] = quantity(2)
        doc['artifacts'].append(second)
        for context in list(doc['contexts']):
            doc['contexts'].append({**deepcopy(context), 'artifact': 'eye2'})
        for edge in list(doc['links']):
            if edge['from'] == 'eye' or edge['to'] == 'eye':
                doc['links'].append({**deepcopy(edge),
                    'from': 'eye2' if edge['from'] == 'eye' else edge['from'],
                    'to': 'eye2' if edge['to'] == 'eye' else edge['to']})
        chosen = [groups[0][0], groups[1][1]]
        for chain in chosen:
            chain['plan']['steps'][2]['artifact'] = 'eye2'
            chain['plan']['connections'][1]['to'] = 'eye2'
            chain['plan']['connections'][2]['from'] = 'eye2'
        report = cooperation(chosen, doc)
        self.assertEqual(report['status'], 'fail')
        self.assertEqual({g['artifact'] for g in report['groups'] if g['status'] == 'fail'}, {'eye2'})

    def test_ending_unknown_participant_does_not_hide_known_failure(self):
        doc, groups = fixture()
        chosen = [groups[0][0], groups[1][1], groups[2][2]]
        doc['tasks'][2]['attributes']['target_count'] = None
        doc['artifacts'][0]['attributes']['max_tracks'] = quantity(2)
        chosen[2]['plan']['steps'][0]['end_s'] = 1
        report = cooperation(chosen, doc)
        self.assertEqual(report['status'], 'fail')
        self.assertTrue(any(g['start_s'] == 1 and g['status'] == 'fail' for g in report['groups']))

    def test_links_respect_undirected_communication_but_directed_control(self):
        doc, groups = fixture()
        doc['links'] = [e for e in doc['links'] if (e['from'], e['to']) != ('coord', 'eye')]
        token = SEQUENCES.set({'communication_model': 'undirected'})
        try:
            self.assertEqual(cooperation(groups[0][:1], doc)['status'], 'pass')
            doc['links'] = [e for e in doc['links'] if (e['from'], e['to']) != ('coord', 'act0')]
            self.assertEqual(cooperation(groups[0][:1], doc)['status'], 'unknown')
        finally:
            SEQUENCES.reset(token)

    def compare_search(self, doc, groups):
        expected, visited, best = {}, {}, None
        for chosen in product(*groups):
            if network_conflicts(chosen, doc):
                continue
            state = cooperation(chosen, doc)['status']
            ids = tuple(c['id'] for c in chosen)
            if state != 'fail':
                expected[ids] = state
            if state == 'pass':
                key = (-sum(c['score'] for c in chosen) / len(groups), ids)
                if best is None or key < best:
                    best = key

        def audit(chosen, document):
            if len(chosen) < len(groups):
                return cooperation_prefix(chosen, document)
            result = cooperation(chosen, document)
            if not network_conflicts(chosen, document) and result['status'] != 'fail':
                visited[tuple(c['id'] for c in chosen)] = result['status']
            return result

        actual, stats = branch_and_bound(groups, doc, network_conflicts, cooperation_check=audit)
        self.assertEqual(visited, expected)
        self.assertEqual(None if actual is None else actual[0], best)
        self.assertEqual(stats['optimality'], 'not_proven' if 'unknown' in expected.values()
                         else 'proven' if best else 'proven_infeasible')
        return stats

    def test_pruning_and_randomized_exhaustive_agreement(self):
        doc, groups = fixture()
        stats = self.compare_search(doc, groups)
        self.assertGreater(stats['pruned_table10'], 0)
        self.assertLess(stats['evaluated'], 27)
        for index, field in [(0, 'max_tracks'), (1, 'max_processing_targets')]:
            doc, groups = fixture()
            doc['artifacts'][index]['attributes'][field] = quantity(2)
            self.assertEqual(self.compare_search(doc, groups)['evaluated'], 0)
        rng = random.Random(1911)
        for trial in range(25):
            doc, groups = fixture()
            for task in doc['tasks']:
                task['attributes']['target_count'] = None if rng.randrange(5) == 0 else quantity(rng.randint(1, 3))
            for index, field in [(0, 'max_tracks'), (1, 'max_processing_targets')]:
                doc['artifacts'][index]['attributes'][field] = None if rng.randrange(5) == 0 else quantity(rng.randint(2, 8))
            for context in doc['contexts']:
                context['attributes']['target_distance'] = None if rng.randrange(6) == 0 else quantity(rng.choice([5, 10]), 'range')
            for group in groups:
                for chain in group:
                    shift(chain, rng.choice([0, 1, 2, 4, 10]))
            with self.subTest(trial=trial):
                self.compare_search(doc, groups)


if __name__ == '__main__':
    unittest.main()
