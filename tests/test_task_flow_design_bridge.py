"""Planning/resource adapter contracts, fallback and real OWL integration."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from task_flow.contracts import ContractError, prepare_problem
from task_flow.planner import SearchSession
from task_flow.design_bridge import compile_tuple, prepare_mapping, run_bridge, main
from design.engine import DesignError, SearchLimitError, design
from design.knowledge import knowledge_context, knowledge_outputs
from workflow_tools.prepare_task_flow_design_demo import build_job

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT/'task_flow/examples/design_bridge'


def inputs():
    scene = json.loads((EXAMPLE/'scene.json').read_text(encoding='utf-8'))
    mapping = json.loads((EXAMPLE/'mapping.json').read_text(encoding='utf-8'))
    return [build_job(i) for i in range(1,4)],scene,mapping


def candidates(jobs):
    return [SearchSession(prepare_problem(j['catalog'],j['request'])).run()['candidates'][0] for j in jobs]


class BridgeContractTests(unittest.TestCase):
    def setUp(self):
        self.jobs,self.scene,self.mapping = inputs()

    def test_planner_derives_lengths_and_compiler_preserves_instances_and_capabilities(self):
        initial = deepcopy((self.jobs,self.scene,self.mapping))
        flows = candidates(self.jobs)
        doc,trace = compile_tuple(self.jobs,flows,self.scene,self.mapping)
        self.assertEqual(trace['status'],'compiled')
        templates = doc['knowledge']['sequences_inline']['templates']
        self.assertEqual([len(t['steps']) for t in templates],[3,4,4])
        self.assertNotIn('sequences_file',doc['knowledge'])
        for job,flow,template,record in zip(self.jobs,flows,templates,trace['tasks']):
            self.assertEqual(record['verification']['verification_status'],'passed')
            self.assertEqual(record['instances'],flow['sequence']['steps'])
            for instance,step in zip(record['instances'],template['steps']):
                definition = next(a for a in job['catalog']['activities'] if a['id'] == instance['activity_id'])
                self.assertEqual(step['capabilities'],definition['required_capabilities'])
        self.assertEqual((self.jobs,self.scene,self.mapping),initial)

    def test_state_change_changes_number_of_steps_without_template_change(self):
        job = self.jobs[1]
        job['request']['static_facts'][0]['value'] = False
        self.assertEqual(len(candidates([job])[0]['sequence']['steps']),3)
        job['request']['static_facts'][0]['value'] = True
        self.assertEqual(len(candidates([job])[0]['sequence']['steps']),4)

    def test_tampered_final_state_is_replayed_not_trusted(self):
        flows = candidates(self.jobs)
        flows[0]['final_state'] = []
        doc,result = compile_tuple(self.jobs,flows,self.scene,self.mapping)
        self.assertIsNone(doc)
        self.assertEqual(result['status'],'verification_failed')

    def test_target_parameters_cannot_be_silently_rebound(self):
        flows = candidates(self.jobs)
        scene = deepcopy(self.scene)
        scene['tasks'][0]['target'] = 'Some-Other-Rift'
        doc,result = compile_tuple(self.jobs,flows,scene,self.mapping)
        self.assertIsNone(doc)
        self.assertEqual(result['status'],'unsupported_mapping')

    def test_unmapped_activity_version_or_parameter_is_explicitly_unsupported(self):
        for change in ('missing','version','parameter'):
            mapping = deepcopy(self.mapping)
            if change == 'missing':
                mapping['entries'] = mapping['entries'][1:]
            elif change == 'version':
                mapping['entries'][0]['activity_version'] = '2.0'
            else:
                mapping['entries'][0]['target_parameter'] = 'other'
            doc,result = compile_tuple(self.jobs,candidates(self.jobs),self.scene,mapping)
            self.assertIsNone(doc)
            self.assertEqual(result['status'],'unsupported_mapping')

    def test_mapping_guards(self):
        for mutate in (lambda m:m['entries'].append(deepcopy(m['entries'][0])),
                       lambda m:m['entries'][0].update(role='manifestation'),
                       lambda m:m['entries'][0].update(min_level=True),
                       lambda m:m.update(allow_nonadjacent_reuse=1),
                       lambda m:m['evaluation'].update(mode='feasibility_only'),
                       lambda m:m.update(extra=True)):
            mapping = deepcopy(self.mapping)
            mutate(mapping)
            with self.assertRaises((ContractError,DesignError)):
                prepare_mapping(mapping)

    def test_no_input_templates_no_partial_task_coverage_no_reserved_collision(self):
        for change in ('template','coverage','collision'):
            jobs,scene,mapping = inputs()
            if change == 'template':
                scene['knowledge']['sequences_file'] = 'rift-multistep.flows.json'
            elif change == 'coverage':
                jobs.pop()
            else:
                scene['tasks'][0]['attributes']['bridge_task_id'] = 'x'
            with self.assertRaises(ContractError):
                run_bridge(jobs,scene,mapping)

    def test_unknown_and_design_limits_are_not_proven_infeasibility(self):
        for value in ('blocked','limit','error'):
            if value == 'blocked':
                mocked = patch('task_flow.design_bridge.design',return_value={'status':'blocked'})
            else:
                error = SearchLimitError('budget') if value == 'limit' else DesignError('reasoner unavailable')
                mocked = patch('task_flow.design_bridge.design',side_effect=error)
            with mocked:
                result = run_bridge(self.jobs,self.scene,self.mapping)
            self.assertEqual(result['integration_status'],'design_limit' if value == 'limit' else 'blocked')
            self.assertIsNone(result['selected_attempt'])
            self.assertEqual(len(result['attempts']),4)

    def test_exact_tuple_feedback_and_limits_do_not_reject_other_flows(self):
        with patch('task_flow.design_bridge.design',return_value={'status':'no_feasible_design'}):
            result = run_bridge(self.jobs,self.scene,self.mapping)
            limited = run_bridge(self.jobs,self.scene,self.mapping,max_checks=1)
            flow_limited = run_bridge(self.jobs,self.scene,self.mapping,max_flows_per_task=1)
        self.assertEqual(result['integration_status'],'resource_no_solution_in_bounds')
        self.assertEqual(len({tuple(a['candidate_ids']) for a in result['attempts']}),4)
        self.assertEqual(limited['integration_status'],'analysis_limit')
        self.assertEqual(flow_limited['integration_status'],'search_limit')

    def test_planning_limits_and_logical_no_solution_are_distinct(self):
        for case in ('limit','no_solution','unknown'):
            jobs = deepcopy(self.jobs)
            if case == 'limit':
                jobs[0]['request']['search']['max_nodes'] = 1
            elif case == 'no_solution':
                jobs[0]['request']['search']['max_length'] = 2
            else:
                jobs[0]['request']['initial_state'][0]['value'] = None
            with patch('task_flow.design_bridge.design') as backend:
                result = run_bridge(jobs,self.scene,self.mapping)
            self.assertEqual(result['integration_status'],{'limit':'search_limit','no_solution':'logical_no_solution','unknown':'blocked'}[case])
            backend.assert_not_called()

    def test_short_valid_flow_is_not_padded_with_fake_steps(self):
        jobs = deepcopy(self.jobs)
        jobs[0]['request']['initial_state'][0]['value'] = True
        with patch('task_flow.design_bridge.design',return_value={'status':'no_feasible_design'}):
            result = run_bridge(jobs,self.scene,self.mapping)
        self.assertEqual(result['integration_status'],'blocked')
        self.assertTrue(any(a['compilation']['status'] == 'unsupported_mapping' for a in result['attempts']))

    def test_inline_snapshot_changes_hash_and_context_is_restored(self):
        doc,_ = compile_tuple(self.jobs,candidates(self.jobs),self.scene,self.mapping)
        with knowledge_context(doc) as (_,meta):
            before = deepcopy(meta)
        self.assertEqual(meta['files'][-1]['path'],'inline:sequences')
        doc['knowledge']['sequences_inline']['templates'][0]['steps'][0]['min_level'] = .7
        with knowledge_context(doc) as (_,after):
            self.assertNotEqual(before['sha256'],after['sha256'])
        doc['knowledge']['sequences_file'] = 'rift-multistep.flows.json'
        with self.assertRaises(DesignError),knowledge_context(doc):
            pass

    def test_insight_lock_mode_rejects_multiple_observations(self):
        doc,_ = compile_tuple(self.jobs,candidates(self.jobs),self.scene,self.mapping)
        steps = doc['knowledge']['sequences_inline']['templates'][0]['steps']
        extra = deepcopy(steps[0])
        extra.update(id='second-observe',incoming_relation='communication')
        steps.insert(2,extra)
        with self.assertRaises(DesignError),knowledge_context(doc):
            pass

    def test_output_directory_is_never_reused(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as output:
            code = main(['--jobs',str(EXAMPLE/'jobs.json'),'--scene',str(EXAMPLE/'scene.json'),
                         '--mapping',str(EXAMPLE/'mapping.json'),'--output-dir',output])
            self.assertEqual(code,2)


@unittest.skipUnless(importlib.util.find_spec('owlready2') and shutil.which('java'),'HermiT and Java required')
class RealBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs,cls.scene,cls.mapping = inputs()
        cls.result = run_bridge(cls.jobs,cls.scene,cls.mapping)

    def test_real_fallback_and_multitask_network(self):
        result = self.result
        self.assertEqual(result['integration_status'],'selected')
        self.assertEqual([a['design_status'] for a in result['attempts']],['no_feasible_design']*3+['complete'])
        self.assertEqual(result['global_flow_optimality'],'not_proven')
        self.assertFalse(result['execution_confirmed'])
        selected = result['attempts'][result['selected_attempt']]
        report = selected['design_report']
        self.assertEqual(report['owl_reasoning']['status'],'passed')
        self.assertEqual(report['network_search']['optimality'],'proven')
        self.assertEqual([len(t['chains'][0]['plan']['steps']) for t in report['tasks']],[3,4,4])
        for trace in selected['compilation']['tasks'][1:]:
            self.assertEqual([s['activity_id'] for s in trace['instances']],['observe','analyze_secondary','decide','stabilize'])
        self.assertEqual(report['network']['makespan_s'],15)
        self.assertEqual(report['network']['cost_per_execution'],24)
        self.assertAlmostEqual(report['network']['score'],87/140)

    def test_precision_is_observation_times_lock_and_export_is_replayable(self):
        selected = self.result['attempts'][self.result['selected_attempt']]
        doc,report = selected['design_input'],selected['design_report']
        resources = {a['id']:a for a in doc['artifacts']}
        chosen = []
        for task in report['tasks']:
            for chain in task['chains']:
                effect = chain['plan']['steps'][-1]['artifact']
                self.assertAlmostEqual(chain['metrics']['accuracy'],.99*resources[effect]['activity_metrics']['act']['accuracy'])
                if chain['id'] in report['network']['chains']:
                    chosen.append(effect)
        self.assertEqual(chosen,['R-Seal-B','R-Seal-D','R-Seal-C'])
        outputs = knowledge_outputs(doc,report)
        self.assertIn('reasoning_input.ttl',outputs)
        # CLI executes strict RDF export; test that its source can be rerun as a
        # normal design input without any bridge global state or temporary files.
        self.assertEqual(design(json.loads(json.dumps(doc))),report)


if __name__ == '__main__':
    unittest.main()
