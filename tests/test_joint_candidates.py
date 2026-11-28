from copy import deepcopy
import math
import unittest

from task_flow.contracts import ContractError
from task_flow.joint_candidates import CandidateStore,evaluate,metrics_for_chain,RAW_POLICY
from task_flow.joint_identity import new_run_id,sha


def pool(values):
    store=CandidateStore(new_run_id())
    for i,value in enumerate(values):
        payload=dict(task_id='T1',flow_id=f'flow-{i}')
        cid='candidate-'+sha(payload)
        chain=dict(id='display',score=None,metrics=dict(time=value,accuracy=.9,cost=1,cost_unit='u'),plan={'steps':[]})
        store.add(cid,payload,chain,{})
    snapshot=store.freeze(['T1'],dict(flow_search_complete=True,chain_generation_complete=True))
    return store,snapshot


class JointCandidateTests(unittest.TestCase):
    def test_EV01_cross_flow_union(self):
        store,snapshot=pool([10,20])
        result=evaluate(snapshot,store.candidates,dict(time=1,accuracy=0,cost=0))
        self.assertEqual(result['normalization_extrema']['T1']['time'],dict(min=10,max=20,degenerate=False))
        self.assertEqual(set(v['score'] for v in result['candidates'].values()),{0,1})

    def test_EV02_extreme_candidate_requires_new_snapshot_and_rescore(self):
        a,sa=pool([10,20]); b,sb=pool([10,20,100]); w=dict(time=1,accuracy=0,cost=0)
        ra,rb=evaluate(sa,a.candidates,w),evaluate(sb,b.candidates,w)
        bytime={c['chain']['metrics']['time']:rb['candidates'][cid]['score'] for cid,c in b.candidates.items()}
        self.assertEqual(bytime[10],1)
        self.assertAlmostEqual(bytime[20],8/9)
        self.assertEqual(bytime[100],0)
        self.assertNotEqual(ra['evaluation_context_hash'],rb['evaluation_context_hash'])
        self.assertEqual(set(a.candidates),set(b.candidates)-{next(k for k,c in b.candidates.items() if c['chain']['metrics']['time']==100)})

    def test_EV03_EV04_equal_and_single_values_use_half(self):
        for values in ([10],[10,10]):
            store,snapshot=pool(values)
            result=evaluate(snapshot,store.candidates,dict(time=.3,accuracy=.3,cost=.4))
            for value in result['candidates'].values():
                self.assertEqual(value,dict(normalized=dict(time=.5,accuracy=.5,cost=.5),score=.5))

    def test_EV05_empty_task_is_not_dropped(self):
        store=CandidateStore(new_run_id()); snapshot=store.freeze(['T1','T2'],{})
        with self.assertRaises(ContractError):evaluate(snapshot,store.candidates,dict(time=.3,accuracy=.3,cost=.4))

    def test_EV06_missing_and_nonfinite_metrics_block(self):
        for change in ('missing','nonfinite'):
            store,snapshot=pool([10])
            metric=next(iter(store.candidates.values()))['chain']['metrics']
            if change=='missing':del metric['accuracy']
            else:metric['accuracy']=float('nan')
            with self.assertRaises(ContractError):evaluate(snapshot,store.candidates,dict(time=.3,accuracy=.3,cost=.4))

    def test_EV07_generation_is_unranked_and_frozen_store_cannot_append(self):
        store,snapshot=pool([10,20])
        self.assertTrue(all(c['chain']['score'] is None and 'normalized' not in c['chain'] for c in store.candidates.values()))
        candidate=next(iter(store.candidates.values()))
        with self.assertRaises(ContractError):store.add(candidate['candidate_id'],candidate['identity_payload'],candidate['chain'],{})

    def test_EV08_weights_affect_evaluation_not_identity(self):
        store,snapshot=pool([10,20]); ids=set(store.candidates)
        a=evaluate(snapshot,store.candidates,dict(time=1,accuracy=0,cost=0))
        b=evaluate(snapshot,store.candidates,dict(time=0,accuracy=1,cost=0))
        self.assertEqual(set(store.candidates),ids)
        self.assertNotEqual(a['evaluation_context_hash'],b['evaluation_context_hash'])

    def test_EV09_explicit_four_step_raw_policy(self):
        scene=dict(policies={'cost_unit':'u'},artifacts=[])
        steps=[]
        for i,(activity,duration,quality,cost) in enumerate([('observe',2,.9,1),('decide',3,.8,2),('decide',4,.7,3),('act',5,.6,4)]):
            scene['artifacts'].append(dict(id=str(i),activity_metrics={activity:dict(duration_s=duration,accuracy=quality,cost={'value':cost,'unit':'u'})}))
            steps.append(dict(artifact=str(i),activity=activity))
        actual=metrics_for_chain({'plan':{'steps':steps}},scene)
        self.assertEqual(actual['time'],14);self.assertEqual(actual['cost'],10)
        self.assertEqual(actual['accuracy'],.9*.8*.7*.6)
        self.assertEqual(RAW_POLICY,'sequential_product_v2')

    def test_EV10_domain_validity_is_separate_and_pending(self):
        store,snapshot=pool([10])
        result=evaluate(snapshot,store.candidates,dict(time=.3,accuracy=.3,cost=.4))
        self.assertEqual(result['model_validation']['domain_validity'],'pending')

    def test_EV11_exact_float_equality_not_isclose(self):
        store,snapshot=pool([10,math.nextafter(10,11)])
        result=evaluate(snapshot,store.candidates,dict(time=1,accuracy=0,cost=0))
        self.assertFalse(result['normalization_extrema']['T1']['time']['degenerate'])
        self.assertEqual({r['score'] for r in result['candidates'].values()},{0,1})

    def test_EV12_inconsistent_units_not_implicitly_converted(self):
        scene=dict(policies={'cost_unit':'u'},artifacts=[dict(id='a',activity_metrics={'act':dict(duration_s=1,accuracy=.9,cost={'value':1,'unit':'other'})})])
        with self.assertRaises(ContractError):metrics_for_chain({'plan':{'steps':[dict(artifact='a',activity='act')]}},scene)


if __name__=='__main__':unittest.main()
