"""Benchmark safety, axis isolation, and instrumented/ordinary parity."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

from workflow_tools.benchmark_joint import build,cases,digest,save,worker,ROOT
from task_flow.joint import run_joint


class BenchmarkTests(unittest.TestCase):
    def case(self,name):return next(c for c in cases() if c['id']==name)

    def test_artifact_axis_preserves_task_flow_and_expands_all_links(self):
        a=build(self.case('P2-artifacts-1'));b=build(self.case('P2-artifacts-3'))
        self.assertEqual(a[0],b[0]);self.assertEqual(a[2:],b[2:])
        self.assertEqual(len(b[1]['artifacts']),3*len(a[1]['artifacts']))
        self.assertEqual(len(b[1]['links']),9*len(a[1]['links']))
        self.assertEqual(a[1]['weights'],b[1]['weights'])

    def test_task_axis_preserves_shared_resources_and_simultaneous_starts(self):
        a=build(self.case('P4-tasks-1'));b=build(self.case('P4-tasks-5'))
        self.assertEqual(a[1]['artifacts'],b[1]['artifacts'])
        self.assertEqual(a[1]['links'],b[1]['links'])
        self.assertEqual(len(b[0]),5)
        self.assertEqual({t['start_s'] for t in b[1]['tasks']},{0})

    def test_flow_axis_changes_only_analysis_alternatives(self):
        a=build(self.case('P1-flows-1'));b=build(self.case('P1-flows-4'))
        self.assertEqual(a[1],b[1])
        ra=deepcopy(a[0][0]['request']);rb=deepcopy(b[0][0]['request'])
        ra.pop('catalog_sha256');rb.pop('catalog_sha256');self.assertEqual(ra,rb)
        self.assertEqual(len(b[0][0]['catalog']['activities'])-len(a[0][0]['catalog']['activities']),3)
        self.assertEqual(a[2]['evaluation'],b[2]['evaluation'])

    def test_budget_variants_do_not_change_evaluation_or_resources(self):
        from workflow_tools.verify_joint_optimization import inputs
        _,base,mapping=inputs()
        for case in cases():
            if case['group']!='P5':continue
            _,scene,actual,_=build(case)
            self.assertEqual(scene['artifacts'],base['artifacts'])
            self.assertEqual(scene['weights'],base['weights'])
            self.assertEqual(actual,mapping)

    def test_every_generated_activity_catalog_has_a_matching_request_digest(self):
        from task_flow.contracts import prepare_problem
        for case in cases():
            jobs,_,_,_=build(case)
            for job in jobs:prepare_problem(job['catalog'],job['request'])

    def test_p1_axis_really_generates_the_declared_number_of_flows(self):
        from task_flow.contracts import prepare_problem
        from task_flow.planner import SearchSession
        for n in (1,2,4):
            jobs,_,_,_=build(self.case(f'P1-flows-{n}'))
            job=jobs[0];request=deepcopy(job['request']);request['search']['mode']='enumerate_bounded'
            result=SearchSession(prepare_problem(job['catalog'],request)).run(max_candidates=100)
            self.assertEqual(len(result['candidates']),n)
            self.assertEqual(result['search_status'],'exhausted')

    @unittest.skipUnless(os.name=='nt','Windows working-set baseline')
    def test_instrumented_real_pipeline_matches_plain_and_preserves_inputs(self):
        case=self.case('P4-tasks-1');data=build(case);before=deepcopy(data)
        plain=run_joint(*data[:3],**data[3])
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            root=Path(directory);config=root/'input.json'
            save(config,dict(case=case,inputs=data,input_hash=digest(data)))
            worker(config,root/'run')
            actual=json.loads((root/'run/joint_report.json').read_text(encoding='utf-8'))
            perf=json.loads((root/'run/performance.json').read_text(encoding='utf-8'))
        self.assertEqual(data,before)
        for field in ('candidate_set_hash','selected_candidate_ids','evaluation','global_optimality'):
            self.assertEqual(actual[field],plain[field])
        self.assertEqual(perf['correctness']['independent_verification'],'passed')
        self.assertIsNotNone(perf['correctness']['oracle'])
        memory=perf['stage_metrics']['memory']
        self.assertGreater(memory['sampled_python_peak_rss_bytes'],0)
        self.assertTrue(any(p['name'].lower()=='java.exe' for p in memory['discovered_descendants']))
        for label in ('owl_inference','flow_search','network_optimization','independent_verification'):
            self.assertGreater(perf['stage_metrics'][label]['calls'],0)
            self.assertGreater(perf['stage_metrics'][label]['observations'][0]['sampled_process_tree_peak_rss_bytes'],0)


if __name__=='__main__':unittest.main()
