from copy import deepcopy
import unittest

from task_flow.contracts import ContractError
from task_flow.joint_identity import (canonical,sha,design_context,task_context,flow_identity,mapping_context,require_nfc_source,
                                      candidate_identity,new_run_id,evidence)
from task_flow.joint_candidates import CandidateStore
from tests.test_task_flow_design_bridge import inputs,candidates


def sample():
    jobs,scene,mapping=inputs()
    flow=candidates(jobs[:1])[0]
    fid,_=flow_identity(jobs[0],flow,mapping)
    chain=dict(id='display-1',task='T1',score=None,metrics=dict(time=3,accuracy=.8,cost=2,cost_unit='u'),
               plan={'steps':[{'artifact':x} for x in ('A','B','C')]})
    cid,payload=candidate_identity('T1',task_context(scene['tasks'][0],jobs[0]),fid,flow['sequence']['steps'],chain,
        design_context(scene),'base-knowledge-hash',mapping_context(mapping))
    return jobs,scene,mapping,flow,chain,cid,payload


class JointIdentityTests(unittest.TestCase):
    def test_ID01_same_flow_different_binding(self):
        *_,chain,cid,payload=sample()
        changed=deepcopy(payload); changed['artifact_assignment'][1]['artifact_id']='other'
        self.assertNotEqual(cid,'candidate-'+sha(changed))

    def test_ID02_ID07_different_sequence_same_resources(self):
        jobs,scene,mapping,flow,chain,cid,payload=sample()
        changed=deepcopy(flow)
        changed['sequence']['steps'][0]['parameters']['rift']='Rift-other'
        self.assertNotEqual(flow_identity(jobs[0],flow,mapping)[0],flow_identity(jobs[0],changed,mapping)[0])
        changed=deepcopy(flow); changed['sequence']['steps'].reverse()
        self.assertNotEqual(flow_identity(jobs[0],flow,mapping)[0],flow_identity(jobs[0],changed,mapping)[0])

    def test_ID03_merge_preserves_all_evidence(self):
        *_,chain,cid,payload=sample()
        store=CandidateStore(new_run_id())
        store.add(cid,payload,chain,{'origin':'first'})
        store.add(cid,payload,chain,{'origin':'second'})
        self.assertEqual(len(store.candidates),1)
        self.assertEqual(store.generated_count,2)
        self.assertEqual(len(store.candidates[cid]['candidate_evidence_ids']),2)
        self.assertNotEqual(store.evidence_records[0]['candidate_evidence_id'],store.evidence_records[1]['candidate_evidence_id'])

    def test_ID04_resource_change_without_ontology_change(self):
        _,scene,*_=sample()
        changed=deepcopy(scene); changed['artifacts'][0]['capacity']+=1
        self.assertNotEqual(design_context(scene),design_context(changed))
        self.assertEqual(scene['knowledge'],changed['knowledge'])

    def test_ID05_display_changes_do_not_change_identity(self):
        jobs,scene,mapping,flow,chain,cid,payload=sample()
        flow['candidate_id']='renumbered'
        chain['id']='renumbered'; chain['score']=.999
        self.assertEqual(flow_identity(jobs[0],flow,mapping)[0],payload['flow_id'])
        self.assertEqual(candidate_identity('T1',payload['task_context_hash'],payload['flow_id'],
            flow['sequence']['steps'],chain,payload['design_context_hash'],payload['knowledge_snapshot_hash'],
            payload['mapping_hash'])[0],cid)

    def test_ID06_same_candidate_new_run_new_evidence(self):
        *_,chain,cid,payload=sample()
        a,b=new_run_id(),new_run_id()
        self.assertNotEqual(a,b)
        self.assertNotEqual(evidence(a,cid,'test',0,chain)['candidate_evidence_id'],
                            evidence(b,cid,'test',0,chain)['candidate_evidence_id'])

    def test_ID08_same_version_changed_semantics_changes_flow_id(self):
        jobs,scene,mapping,flow,*_=sample()
        changed=deepcopy(jobs[0]); changed['catalog']['activities'][0]['preconditions']={'all':[]}
        self.assertNotEqual(flow_identity(jobs[0],flow,mapping)[0],flow_identity(changed,flow,mapping)[0])

    def test_ID09_repeated_occurrences_keep_ordered_bindings(self):
        *_,chain,cid,payload=sample()
        self.assertEqual([r['step_index'] for r in payload['artifact_assignment']],[0,1,2])
        changed=deepcopy(payload); changed['artifact_assignment'][0]['artifact_id']='C'
        self.assertNotEqual(sha(payload),sha(changed))

    def test_ID10_canonical_objects_sets_and_semantic_arrays(self):
        self.assertEqual(sha({'b':2,'a':1}),sha({'a':1.0,'b':2.0}))
        self.assertEqual(sha(-0.0),sha(0))
        self.assertNotEqual(sha([1,2]),sha([2,1]))
        jobs,scene,mapping,flow,*_=sample()
        scene2=deepcopy(scene); scene2['artifacts'].reverse();scene2['links'].reverse()
        mapping2=deepcopy(mapping);mapping2['entries'].reverse()
        self.assertEqual(design_context(scene),design_context(scene2))
        self.assertEqual(mapping_context(mapping),mapping_context(mapping2))

    def test_ID11_conflicting_payload_or_metric_is_rejected(self):
        *_,chain,cid,payload=sample()
        store=CandidateStore(new_run_id()); store.add(cid,payload,chain,{})
        changed=deepcopy(payload);changed['task_id']='T2'
        with self.assertRaises(ContractError):store.add(cid,changed,chain,{})
        changed=deepcopy(chain);changed['metrics']['accuracy']=.7
        with self.assertRaises(ContractError):store.add(cid,payload,changed,{})

    def test_ID12_budget_weight_and_output_do_not_change_candidate_context(self):
        jobs,scene,mapping,flow,*_=sample()
        changed_job=deepcopy(jobs[0]); changed_job['request']['search']['max_nodes']+=100
        changed_scene=deepcopy(scene); changed_scene['weights']=dict(time=.2,accuracy=.3,cost=.5)
        changed_scene['policies']['max_network_search_nodes']=12
        changed_mapping=deepcopy(mapping);changed_mapping['mapping_id']='new-display-id'
        self.assertEqual(task_context(scene['tasks'][0],jobs[0]),task_context(scene['tasks'][0],changed_job))
        self.assertEqual(design_context(scene),design_context(changed_scene))
        self.assertEqual(mapping_context(mapping),mapping_context(changed_mapping))

    def test_ID13_mapping_semantics_changes_context(self):
        *_,mapping,flow,chain,cid,payload=sample()
        changed=deepcopy(mapping);changed['entries'][0]['min_level']=.7
        self.assertNotEqual(mapping_context(mapping),mapping_context(changed))

    def test_ID14_nonfinite_and_unicode_collision_are_rejected(self):
        for v in (float('nan'),float('inf'),{'é':1,'e\u0301':2}):
            with self.assertRaises(ContractError):canonical(v)
        self.assertEqual(sha(9007199254740993),sha(9007199254740993))
        self.assertNotEqual(sha(9007199254740993),sha(9007199254740992))
        with self.assertRaises(ContractError):require_nfc_source({'business_value':'e\u0301'})


if __name__=='__main__':unittest.main()
