from contextlib import redirect_stdout,contextmanager
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from task_flow.contracts import ContractError,canonical_sha256
from task_flow.design_bridge import run_bridge,main
from task_flow.joint import run_joint,export_joint
from task_flow.joint_network import optimize
from task_flow.joint_verify import verify_joint
from tests.joint_reference import audit_joint

ROOT=Path(__file__).resolve().parents[1]
EXAMPLE=ROOT/'task_flow/examples/joint_optimization'


def inputs():
    read=lambda name:json.loads((EXAMPLE/name).read_text(encoding='utf-8'))
    return [dict(task_id=f'T{i}',catalog=read(f'catalog-{i}.json'),request=read(f'request-{i}.json')) for i in range(1,4)],read('scene.json'),read('mapping.json')


def quick_score_in_joint_pool(quick,joint):
    attempt=quick['attempts'][quick['selected_attempt']]
    selected=[]
    for task in attempt['design_report']['tasks']:
        chain=next(c for c in task['chains'] if c['id'] in attempt['design_report']['network']['chains'])
        trace=next(t for t in attempt['compilation']['tasks'] if t['task_id']==task['task'])
        assignment=[s['artifact'] for s in chain['plan']['steps']]
        matching=[cid for cid,c in joint['candidates'].items() if c['task_id']==task['task'] and
                  c['identity_payload']['activity_instance_sequence']==trace['instances'] and
                  [s['artifact'] for s in c['chain']['plan']['steps']]==assignment]
        assert len(matching)==1
        selected.extend(matching)
    return dict(candidate_ids=selected,score=sum(joint['evaluation']['candidates'][cid]['score'] for cid in selected)/len(selected),
                evaluation_context_hash=joint['evaluation']['evaluation_context_hash'])


@unittest.skipUnless(importlib.util.find_spec('owlready2') and shutil.which('java'),'real HermiT/Java required')
class JointIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs,cls.scene,cls.mapping=inputs()
        cls.report=run_joint(cls.jobs,cls.scene,cls.mapping)

    def test_OP03_OP04_new_flow_beats_first_feasible_on_identical_basis(self):
        quick=run_bridge(self.jobs,self.scene,self.mapping)
        self.assertEqual(quick['selected_attempt'],0)
        baseline=quick_score_in_joint_pool(quick,self.report)
        self.assertGreater(self.report['design_report']['network']['score'],baseline['score'])
        self.assertNotEqual(self.report['selected_candidate_ids'],baseline['candidate_ids'])
        for cid in self.report['selected_candidate_ids'][1:]:
            seq=self.report['candidates'][cid]['identity_payload']['activity_instance_sequence']
            self.assertEqual(seq[1]['activity_id'],'analyze_secondary')

    def test_OP05_complete_chain_and_network_oracle(self):
        oracle=audit_joint(self.report)
        self.assertEqual(oracle['raw_network_combinations'],96)
        self.assertEqual(oracle['feasible_network_combinations'],32)
        self.assertEqual(self.report['unique_chain_count'],14)
        self.assertEqual(self.report['flow_count'],5)

    def test_PF01_complete_coverage_and_proof(self):
        r=self.report
        self.assertTrue(r['flow_search_complete']);self.assertTrue(r['chain_generation_complete'])
        self.assertEqual(r['global_optimality'],'bounded_global_optimum')
        self.assertEqual(r['network_optimality'],'proven')
        self.assertEqual(r['verification']['status'],'passed')
        self.assertFalse(r['execution_confirmed'])

    def test_PF02_partial_flow_set_is_only_candidate_set_optimum(self):
        r=run_joint(self.jobs,self.scene,self.mapping,max_flows_per_task=1)
        self.assertFalse(r['flow_search_complete']);self.assertTrue(r['chain_generation_complete'])
        self.assertEqual(r['global_optimality'],'candidate_set_optimum')
        self.assertEqual(r['verification']['status'],'passed')

    def test_PF02_partial_chain_generation_retains_known_candidates(self):
        scene=deepcopy(self.scene);scene['policies']['max_chain_combinations']=1
        r=run_joint(self.jobs,scene,self.mapping)
        self.assertTrue(r['flow_search_complete']);self.assertFalse(r['chain_generation_complete'])
        self.assertEqual(r['global_optimality'],'candidate_set_optimum')
        self.assertEqual(r['unique_chain_count'],3)
        self.assertEqual(r['verification']['status'],'passed')

    def test_PF03_VR03_incumbent_has_separate_rdf_contract(self):
        scene=deepcopy(self.scene)
        base=self.report
        groups=[[{**deepcopy(base['candidates'][cid]['chain']),**base['evaluation']['candidates'][cid]}
                 for cid in s['candidate_ids']] for s in base['frozen_candidate_sets']['sets']]
        roles={a['artifact']:a['role'] for a in base['design_report']['ontology_inference']['artifacts']}
        for limit in range(1,100):
            scene['policies']['max_network_search_nodes']=limit
            prepared=deepcopy(scene)
            for artifact in prepared['artifacts']:artifact['role']=roles[artifact['id']]
            best,stats=optimize(groups,prepared,self.mapping)
            if best is not None and not stats['search_complete']:break
        self.assertIsNotNone(best);self.assertFalse(stats['search_complete'])
        r=run_joint(self.jobs,scene,self.mapping)
        self.assertEqual(r['integration_status'],'feasible');self.assertEqual(r['global_optimality'],'not_proven')
        self.assertEqual(r['design_report']['status'],'feasible')
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            export_joint(r,Path(directory)/'out')
            self.assertTrue((Path(directory)/'out/incumbent.ttl').exists())
            self.assertFalse((Path(directory)/'out/design.ttl').exists())

    def test_PF04_network_limit_without_incumbent_is_undetermined(self):
        scene=deepcopy(self.scene);scene['policies']['max_network_search_nodes']=1
        r=run_joint(self.jobs,scene,self.mapping)
        self.assertEqual(r['integration_status'],'undetermined');self.assertEqual(r['global_optimality'],'not_proven')
        self.assertEqual(r['selected_candidate_ids'],[])

    def test_budget8_optimal_incumbent_never_upgrades_production_proof(self):
        scene=deepcopy(self.scene);scene['policies']['max_network_search_nodes']=8
        report=run_joint(self.jobs,scene,self.mapping)
        self.assertTrue(report['selected_candidate_ids'])
        self.assertEqual(report['verification']['status'],'passed')
        self.assertFalse(report['network_search']['search_complete'])
        oracle=audit_joint(report)
        self.assertAlmostEqual(report['design_report']['network']['score'],oracle['best_score'])
        self.assertEqual(report['global_optimality'],'not_proven')
        self.assertEqual(report['network_optimality'],'not_proven')
        self.assertEqual(verify_joint(report)['status'],'passed')
        self.assertEqual(report['global_optimality'],'not_proven')

    def test_PF05_unsupported_mapping_allows_only_known_subset_claim(self):
        mapping=deepcopy(self.mapping)
        mapping['entries']=[e for e in mapping['entries'] if e['activity_id']!='analyze_secondary']
        r=run_joint(self.jobs,self.scene,mapping)
        self.assertEqual(r['global_optimality'],'candidate_set_optimum')
        self.assertFalse(r['chain_generation_complete'])

    def test_PF05_unknown_branch_prevents_global_proof(self):
        jobs=deepcopy(self.jobs);mapping=deepcopy(self.mapping)
        jobs[0]['request']['static_facts'][0]['value']=None
        action=deepcopy(next(a for a in jobs[0]['catalog']['activities'] if a['id']=='decide'))
        action['id']='decide_safe'
        action['preconditions']['all']=action['preconditions']['all'][:2]
        jobs[0]['catalog']['activities'].append(action)
        jobs[0]['request']['catalog_sha256']=canonical_sha256(jobs[0]['catalog'])
        entry=deepcopy(next(e for e in mapping['entries'] if e['activity_id']=='decide'))
        entry['activity_id']='decide_safe';mapping['entries'].append(entry)
        r=run_joint(jobs,self.scene,mapping)
        self.assertTrue(any(p['search']['unknown_conditions_seen'] for p in r['planning']))
        self.assertEqual(r['global_optimality'],'candidate_set_optimum')

    def test_unknown_task_requirement_is_blocked_not_treated_as_complete(self):
        scene=deepcopy(self.scene)
        scene['task_rules'].append(dict(id='unknown-extra-demand',role='coordination',
            capability='urn:arcane-rift-demo:ResonanceAnalysis',
            when=[dict(field='task.unknown_requirement',op='eq',value=True)],source_refs=['test'],blocking_issue_ids=[]))
        r=run_joint(self.jobs,scene,self.mapping)
        self.assertEqual(r['integration_status'],'blocked')
        self.assertEqual(r['termination_reason'],'unknown_task_requirements')
        self.assertEqual(r['selected_candidate_ids'],[])

    def test_flow_analysis_budget_cannot_claim_complete_generation(self):
        r=run_joint(self.jobs,self.scene,self.mapping,max_flow_analyses=1)
        self.assertFalse(r['chain_generation_complete'])
        self.assertEqual(r['global_optimality'],'not_proven')
        self.assertEqual(r['integration_status'],'undetermined')

    def test_more_than_fifty_flow_templates_are_batched_without_loss(self):
        job=deepcopy(self.jobs[1]);scene=deepcopy(self.scene);mapping=deepcopy(self.mapping)
        scene['tasks']=[scene['tasks'][1]];scene['contexts']=[c for c in scene['contexts'] if c['task']=='T2']
        action=next(a for a in job['catalog']['activities'] if a['id']=='analyze_secondary')
        entry=next(e for e in mapping['entries'] if e['activity_id']=='analyze_secondary')
        for i in range(53):
            clone=deepcopy(action);clone['id']=f'analyze_clone_{i:02}';job['catalog']['activities'].append(clone)
            binding=deepcopy(entry);binding['activity_id']=clone['id'];mapping['entries'].append(binding)
        job['request']['catalog_sha256']=canonical_sha256(job['catalog'])
        job['request']['search'].update(max_length=4,max_nodes=1000000,timeout_ms=30000)
        r=run_joint([job],scene,mapping)
        self.assertEqual(r['flow_count'],55)
        self.assertEqual(r['unique_chain_count'],165)
        self.assertEqual(len(r['generation_batches']),2)
        self.assertEqual(r['global_optimality'],'bounded_global_optimum')

    def test_PF06_snapshot_or_knowledge_mutation_is_not_accepted(self):
        changed=deepcopy(self.report);changed['frozen_candidate_sets']['sets'][0]['candidate_ids'].pop()
        self.assertEqual(verify_joint(changed)['status'],'failed')

    def test_PF06_running_context_change_stops_before_optimization(self):
        from task_flow.joint import knowledge_context as real_context
        calls=[0]
        @contextmanager
        def changed_context(scene):
            calls[0]+=1
            with real_context(scene) as (effective,metadata):
                metadata=deepcopy(metadata)
                if calls[0]>1:metadata['sha256']='changed-during-run'
                yield effective,metadata
        with patch('task_flow.joint.knowledge_context',changed_context):
            with self.assertRaisesRegex(ContractError,'context_changed'):
                run_joint(self.jobs,self.scene,self.mapping)
        changed=deepcopy(self.report);changed['base_knowledge_snapshot']['files'][0]['content']+='\n'
        self.assertEqual(verify_joint(changed)['status'],'failed')

    def test_PF07_complete_infeasibility_not_merely_search_failure(self):
        scene=deepcopy(self.scene)
        for artifact in scene['artifacts']:
            if artifact['id'].startswith('R-Seal-'):artifact['inventory']=0
        r=run_joint(self.jobs,scene,self.mapping)
        self.assertEqual(r['global_optimality'],'bounded_global_infeasible')
        self.assertEqual(r['integration_status'],'no_feasible_design')

    def test_EV06_missing_raw_metric_is_blocked_not_filled(self):
        scene=deepcopy(self.scene)
        next(a for a in scene['artifacts'] if a['id']=='R-Analysis')['activity_metrics']['decide']=None
        r=run_joint(self.jobs,scene,self.mapping)
        self.assertEqual(r['integration_status'],'blocked')
        self.assertEqual(r['termination_reason'],'missing_raw_metrics')
        self.assertNotIn('evaluation',r)

    def test_VR01_tampering_flow_binding_metric_or_proof_is_rejected(self):
        for kind in ('flow','binding','metric','score','proof','domain','evidence'):
            changed=deepcopy(self.report)
            cid=changed['selected_candidate_ids'][0]
            if kind=='flow':next(iter(changed['flows'].values()))['candidate']['final_state']=[]
            elif kind=='binding':changed['candidates'][cid]['chain']['plan']['steps'][0]['artifact']='R-Review'
            elif kind=='metric':changed['candidates'][cid]['chain']['metrics']['accuracy']=.123
            elif kind=='score':changed['evaluation']['candidates'][cid]['score']=.123
            elif kind=='proof':changed['global_optimality']='fixed_flow_optimum'
            elif kind=='domain':changed['domain_validity']='proven'
            else:changed['candidate_evidence'][0]['content']['origin']['source_chain_id']='fake'
            self.assertEqual(verify_joint(changed)['status'],'failed',kind)

    def test_VR02_strict_rdf_and_provenance(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            path=Path(directory)/'out';export_joint(self.report,path)
            report=json.loads((path/'joint_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['rdf_validation']['ERROR'],0);self.assertEqual(report['rdf_validation']['WARN'],0)
            text=(path/'design.ttl').read_text(encoding='utf-8')
            self.assertIn('flowId',text);self.assertIn('candidateEvidenceId',text)
            self.assertIn(self.report['run_id'],text)

    def test_VR05_repeat_run_identity_stable_evidence_independent(self):
        again=run_joint(self.jobs,self.scene,self.mapping)
        self.assertEqual(set(again['candidates']),set(self.report['candidates']))
        self.assertEqual(again['candidate_set_hash'],self.report['candidate_set_hash'])
        self.assertEqual(again['selected_candidate_ids'],self.report['selected_candidate_ids'])
        self.assertNotEqual(again['run_id'],self.report['run_id'])
        self.assertFalse(set(e['candidate_evidence_id'] for e in again['candidate_evidence']) &
                         set(e['candidate_evidence_id'] for e in self.report['candidate_evidence']))

    def test_canonical_task_and_resource_record_order_do_not_change_full_set(self):
        scene=deepcopy(self.scene);scene['tasks'].reverse();scene['artifacts'].reverse();scene['links'].reverse()
        jobs=list(reversed(deepcopy(self.jobs)));mapping=deepcopy(self.mapping);mapping['entries'].reverse()
        r=run_joint(jobs,scene,mapping)
        self.assertEqual(set(r['candidates']),set(self.report['candidates']))
        self.assertEqual(r['candidate_set_hash'],self.report['candidate_set_hash'])
        self.assertEqual(r['selected_candidate_ids'],self.report['selected_candidate_ids'])

    def test_VR06_software_pass_does_not_validate_accuracy_model(self):
        self.assertEqual(self.report['domain_validity'],'pending')
        self.assertEqual(self.report['evaluation']['model_validation']['domain_validity'],'pending')

    def test_cli_explicit_policy_and_new_mode(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            with redirect_stdout(io.StringIO()):
                code=main(['--mode','joint_candidate_optimization','--jobs',str(EXAMPLE/'jobs.json'),
                    '--scene',str(EXAMPLE/'scene.json'),'--mapping',str(EXAMPLE/'mapping.json'),
                    '--output-dir',str(Path(directory)/'out')])
            self.assertEqual(code,0)
            self.assertTrue((Path(directory)/'out/design.ttl').exists())
        mapping=deepcopy(self.mapping);mapping['evaluation']['mode']='insight_lock_product'
        with self.assertRaises(ContractError):run_joint(self.jobs,self.scene,mapping)


if __name__=='__main__':unittest.main()
