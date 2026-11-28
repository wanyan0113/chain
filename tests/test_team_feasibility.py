"""Phase-one complementarity acceptance and legacy-exit safety boundaries."""
from collections import Counter
from copy import deepcopy
from itertools import product
import unittest
from unittest.mock import patch

from workflow_tools.team_demo import inputs
from task_flow.team_feasibility import run_teams, stage_plans, contributions, Budget, generate_chains
from task_flow.team_verify import verify_teams
from task_flow.team_contracts import identity, prepare
from task_flow.joint_identity import canonical
from task_flow.contracts import ContractError
from design.knowledge import knowledge_context
from design.ontology_inference import prepare_artifacts
from design.plans import resource_conflicts


class TeamFeasibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs, cls.scene, cls.mapping, cls.config = inputs()
        before = deepcopy((cls.jobs, cls.scene, cls.mapping, cls.config))
        cls.report = run_teams(cls.jobs, cls.scene, cls.mapping, cls.config)
        assert before == (cls.jobs, cls.scene, cls.mapping, cls.config), 'input mutation'
        cls.check = verify_teams(cls.report)  # actual fresh HermiT, not mocked
        cls.analysis = cls.report['analyses'][0]
        with knowledge_context(cls.analysis['design_input']) as (effective, _):
            cls.doc, cls.inference = prepare_artifacts(effective, owl_result=cls.analysis['owl_reasoning'])

    def fixture(self):
        analysis = deepcopy(self.analysis)
        stage = analysis['stages'][1]
        return analysis, stage, deepcopy(self.doc), deepcopy(self.inference)

    def plans(self, stage, doc, inference=None):
        task = next(t for t in doc['tasks'] if t['id'] == 'T1')
        with knowledge_context(self.analysis['design_input']):
            rows = contributions(task, stage['step'], stage['required_capabilities'], doc,
                                 inference or self.inference, self.analysis['owl_reasoning'])
        plans, rejects = stage_plans(stage['step'], stage['required_capabilities'], rows,
                                     {(e['from'], e['to']): e for e in doc['links']}, 'directed',
                                     self.config, Budget(self.config['budgets']))
        return rows, plans, rejects

    def test_real_task_flow_owl_four_stages_and_independent_network_oracle(self):
        r = self.report
        self.assertEqual(self.check['status'], 'passed')
        self.assertEqual(r['status'], 'feasible_networks')
        self.assertEqual(len(r['candidates']), 28)
        self.assertEqual(len(r['feasible_networks']), 16)
        self.assertTrue(r['candidate_search_complete'])
        self.assertTrue(r['network_search_complete'])
        self.assertEqual(r['optimality'], 'not_run')
        self.assertTrue(all(len(c['plan']['steps']) == 4 for c in r['candidates'].values()))
        # Independent small oracle: count simultaneous real-member uses directly.
        # Fixture declares identical nonoverlapping stage windows, cooldown=0,
        # so event sweep is unnecessary here. No production conflict helper.
        groups = [[c for c in r['candidates'].values() if c['task'] == t] for t in ('T1', 'T2')]
        artifacts = {a['id']: a for a in self.scene['artifacts']}
        reference = set()
        for pair in product(*groups):
            allowed = True
            inventory = Counter()
            for i in range(4):
                occupied = Counter(m for c in pair for m in c['plan']['steps'][i]['members'])
                allowed &= all(n <= artifacts[m]['capacity'] for m, n in occupied.items())
                inventory.update(m for c in pair for m in c['plan']['steps'][i]['members'] if artifacts[m]['consumable'])
            allowed &= all(n <= artifacts[m]['inventory'] for m, n in inventory.items())
            if allowed:
                reference.add(tuple(c['candidate_id'] for c in pair))
        self.assertEqual(reference, {tuple(ids) for ids in r['feasible_networks']})
        self.assertTrue(any('PhaseTune' in f[2] for f in self.analysis['owl_reasoning']['facts']))

    def test_ac01_ac02_independent_provider_assignment_oracle(self):
        _, stage, doc, inference = self.fixture()
        rows, plans, _ = self.plans(stage, doc, inference)
        names = ['O-Phase-Tuner', 'O-Safety-Auditor', 'O-Combined']
        cap1, cap2 = stage['required_capabilities']
        # Oracle facts from the synthetic equipment specification, not generated
        # contribution rows. Preserve every valid division and hub choice.
        own = {'O-Phase-Tuner': {'urn:observatory-semantic-demo:PhaseTune'},
               'O-Safety-Auditor': {'urn:observatory-semantic-demo:SafetyAudit'},
               'O-Combined': set(stage['required_capabilities'])}
        expected = set()
        for a, b in product(names, repeat=2):
            if cap1 in own[a] and cap2 in own[b]:
                for hub in set((a, b)):
                    expected.add((a, b, hub))
        self.assertEqual(expected, {(p['capability_assignments'][cap1], p['capability_assignments'][cap2], p['hub']) for p in plans})
        self.assertIn(['O-Combined'], [p['members'] for p in plans])
        self.assertIn(['O-Phase-Tuner', 'O-Safety-Auditor'], [p['members'] for p in plans])
        self.assertNotIn(['O-Phase-Tuner'], [p['members'] for p in plans])
        self.assertNotIn(['O-Safety-Auditor'], [p['members'] for p in plans])

    def test_ac03_ac04_missing_unknown_and_numeric_condition(self):
        _, stage, doc, inference = self.fixture()
        cap = stage['required_capabilities'][1]
        for a in doc['artifacts']:
            a['attributes']['supported_functions'] = []
        rows, plans, _ = self.plans(stage, doc, inference)
        self.assertFalse(plans)
        self.assertTrue(all(row['capabilities'][cap] == 'fail' for row in rows))
        _, stage, doc, inference = self.fixture()
        for rule in doc['artifact_rules']:
            if rule['capability'] == cap:
                rule['when'].append(dict(field='artifact.proof_level', op='ge', value=dict(value=2, unit='1')))
        rows, plans, _ = self.plans(stage, doc, inference)
        self.assertFalse(plans)
        self.assertTrue(any(row['capabilities'][cap] == 'unknown' for row in rows))
        for a in doc['artifacts']:
            a['attributes']['proof_level'] = dict(value=1, unit='1')
        rows, plans, _ = self.plans(stage, doc, inference)
        self.assertFalse(plans)
        self.assertTrue(all(row['capabilities'][cap] == 'fail' for row in rows))

    def test_ac05_member_unavailable_blocked_or_low_level(self):
        for change in ('unavailable', 'blocked', 'low_level'):
            _, stage, doc, _ = self.fixture()
            a = next(a for a in doc['artifacts'] if a['id'] == 'O-Safety-Auditor')
            if change == 'unavailable': a['available'] = False
            elif change == 'blocked': a['blocking_issue_ids'] = ['unresolved-test']
            else: a['attributes']['level_decide']['value'] = .1
            _, plans, _ = self.plans(stage, doc)
            self.assertTrue(plans)  # C remains feasible
            self.assertTrue(all(a['id'] not in p['members'] for p in plans))

    def test_ac06_internal_missing_and_explicit_failed_edges(self):
        for missing in (True, False):
            _, stage, doc, _ = self.fixture()
            pair = {'O-Phase-Tuner', 'O-Safety-Auditor'}
            if missing:
                doc['links'] = [e for e in doc['links'] if {e['from'], e['to']} != pair]
            else:
                for e in doc['links']:
                    if {e['from'], e['to']} == pair: e['communicates'] = False
            _, plans, rejected = self.plans(stage, doc)
            self.assertFalse(any(set(p['members']) == pair for p in plans))
            self.assertTrue(any(e['status'] == ('unknown' if missing else 'fail') for r in rejected for e in r['connections']))

    def test_ac07_handoff_and_control_cannot_be_replaced_by_other_member_edge(self):
        analysis, _, doc, _ = self.fixture()
        task = next(t for t in doc['tasks'] if t['id'] == 'T1')
        template = analysis['design_input']['knowledge']['sequences_inline']['templates'][0]
        for e in doc['links']:
            if e['from'] == 'R-Dispatch' and e['to'] == 'R-Seal-A': e['controls'] = False
        chains, rejected = generate_chains(task, analysis['flow_id'], template,
            [s['plans'] for s in analysis['stages']], doc, self.config, analysis['chain_context'],
            Budget(self.config['budgets']), 1000)
        self.assertFalse(chains)
        self.assertTrue(any('NO_CONTROL' in key for key in rejected))
        doc['links'] = [e for e in doc['links'] if e['from'] != 'R-Survey']
        chains, rejected = generate_chains(task, analysis['flow_id'], template,
            [s['plans'] for s in analysis['stages']], doc, self.config, analysis['chain_context'],
            Budget(self.config['budgets']), 1000)
        self.assertFalse(chains)
        self.assertTrue(any(key.startswith('unknown:') for key in rejected))

    def test_ac08_ac09_real_member_capacity_inventory_and_cooldown(self):
        chain = next(c for c in self.report['candidates'].values() if any(len(p['members']) == 2 for p in c['plan']['steps']))
        second = deepcopy(chain)
        conflicts = resource_conflicts([chain, second], self.scene)
        self.assertTrue(any(c['code'] == 'CAPACITY' and c['resource'].startswith('O-') for c in conflicts))
        doc = deepcopy(self.scene)
        a = next(a for a in doc['artifacts'] if a['id'] == 'O-Phase-Tuner')
        a.update(consumable=True, inventory=1)
        usage = dict(artifact=a['id'], capacity=1, inventory=1, start_s=0, end_s=12)
        one = dict(plan=dict(resource_usage=[usage]))
        two = dict(plan=dict(resource_usage=[{**usage, 'start_s': 10, 'end_s': 20}]))
        self.assertEqual({c['code'] for c in resource_conflicts([one, two], doc)}, {'CAPACITY', 'INVENTORY'})
        two['plan']['resource_usage'][0].update(start_s=12, end_s=24)
        self.assertEqual({c['code'] for c in resource_conflicts([one, two], doc)}, {'INVENTORY'})

    def test_ac10_input_order_and_divisions_keep_correct_identity(self):
        _, stage, doc, _ = self.fixture()
        first = self.plans(stage, doc)[1]
        doc['artifacts'].reverse()
        self.assertEqual(first, self.plans(stage, doc)[1])
        p = first[0]
        q = deepcopy(p)
        q['members'].reverse()
        self.assertEqual(identity('T', 'F', [p], {}), identity('T', 'F', [q], {}))
        self.assertEqual(len({identity('T', 'F', [p], {}) for p in first}), len(first))

    def test_ac11_independent_verifier_rejects_tampering(self):
        # Frozen trusted OWL results shorten structural tamper probes only;
        # the positive end-to-end verification above reruns actual HermiT.
        for kind in ('assignment', 'members', 'edge', 'reservation', 'logical_effect', 'contribution'):
            report = deepcopy(self.report)
            cid = report['analyses'][0]['candidate_ids'][0]
            chain = report['candidates'][cid]
            if kind == 'assignment':
                plan = chain['plan']['steps'][1]
                plan['capability_assignments'][next(iter(plan['capability_assignments']))] = 'R-Survey'
            elif kind == 'members': chain['plan']['steps'][1]['members'] = ['R-Survey']
            elif kind == 'edge': chain['plan']['connections'][0]['evidence']['communicates'] = False
            elif kind == 'reservation': chain['plan']['resource_usage'].pop()
            elif kind == 'logical_effect': report['analyses'][0]['flow_candidate']['final_state'] = []
            else: report['analyses'][0]['stages'][1]['contributions'][0]['common_status'] = 'forged'
            with patch('task_flow.team_verify.reason_scene', side_effect=[deepcopy(a['owl_reasoning']) for a in report['analyses']]):
                self.assertEqual(verify_teams(report)['status'], 'failed', kind)

    def test_ac12_team_and_chain_budgets_mark_partial(self):
        analysis, stage, doc, _ = self.fixture()
        limits = {**self.config['budgets'], 'team_nodes': 1}
        b = Budget(limits)
        stage_plans(stage['step'], stage['required_capabilities'], stage['contributions'],
                    {(e['from'], e['to']): e for e in doc['links']}, 'directed', self.config, b)
        self.assertIn('team_nodes', b.hit)
        b = Budget({**limits, 'chain_nodes': 1})
        chains, _ = generate_chains(next(t for t in doc['tasks'] if t['id'] == 'T1'), analysis['flow_id'],
            analysis['design_input']['knowledge']['sequences_inline']['templates'][0],
            [s['plans'] for s in analysis['stages']], doc, self.config, analysis['chain_context'], b, 100)
        self.assertFalse(chains)
        self.assertIn('chain_nodes', b.hit)

    def test_ac13_legacy_scoring_rdf_and_observations_explicitly_refuse(self):
        from task_flow.joint_candidates import metrics_for_chain
        from design.rdf import design_to_rdf, _design_graph
        from workflow_tools.design_observation_bridge import build
        from design.engine import DesignError
        chain = next(iter(self.report['candidates'].values()))
        with self.assertRaisesRegex(ContractError, 'complementary teams'):
            metrics_for_chain(chain, self.scene)
        for export in (design_to_rdf, _design_graph):
            with self.assertRaisesRegex(DesignError, 'RDF'):
                export(self.scene, self.report)
        with self.assertRaisesRegex(ValueError, 'complementary teams'):
            build(self.report)

    def test_explicit_new_contract_no_accidental_old_mode(self):
        mapping = deepcopy(self.mapping)
        mapping['format_version'] = 'task-flow-design-1.0'
        with self.assertRaises(ContractError): prepare(mapping, self.config)
        config = deepcopy(self.config)
        config['schedules']['T1'][1]['start_s'] = 0
        with self.assertRaises(ContractError): prepare(self.mapping, config)

    def test_full_report_order_stability_no_timing_and_network_budget(self):
        for kind in ('order', 'no_timing', 'network_budget'):
            scene, config = deepcopy(self.scene), deepcopy(self.config)
            if kind == 'order':
                scene['artifacts'].reverse()
                scene['links'].reverse()
            elif kind == 'no_timing': config['schedules'] = {}
            else: config['budgets']['network_nodes'] = 1
            # Known real OWL conclusions are replayed for these orchestration
            # probes; neither input reordering nor time/budget changes alter OWL.
            with patch('task_flow.team_feasibility.reason_scene', side_effect=[deepcopy(a['owl_reasoning']) for a in self.report['analyses']]):
                got = run_teams(self.jobs, scene, self.mapping, config)
            if kind == 'order':
                self.assertEqual(set(got['candidates']), set(self.report['candidates']))
                self.assertEqual(got['feasible_networks'], self.report['feasible_networks'])
            elif kind == 'no_timing':
                self.assertEqual(got['status'], 'feasible_chains_timing_unverified')
                self.assertFalse(got['feasible_networks'])
                self.assertTrue(all(c['plan']['resource_usage'] and
                    all(u['start_s'] is None for u in c['plan']['resource_usage']) for c in got['candidates'].values()))
            else:
                self.assertFalse(got['network_search_complete'])
                self.assertNotIn(got['status'], ('no_feasible_network_in_bounds', 'no_feasible_chain_in_bounds'))
                self.assertEqual(got['optimality'], 'not_run')


if __name__ == '__main__':
    unittest.main()
