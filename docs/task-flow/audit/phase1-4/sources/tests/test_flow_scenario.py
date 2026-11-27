from copy import deepcopy
from io import BytesIO
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import unittest
import tempfile
import numpy as np
from design.engine import design
from fdna.conditional_events import generate, event_block
from fdna.flow_scenario import canonical_document, independent_enumeration, check_design, register_mapping, audit_generation, common_source_scenarios, file_inventory, verify

ROOT=Path(__file__).resolve().parents[1]


class FlowScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=ROOT/'data/fdna/flow_scenario'
        cls.document=json.loads((path/'design_input.json').read_text(encoding='utf-8'))
        cls.config=json.loads((path/'experiment.json').read_text(encoding='utf-8'))
        cls.report=design(canonical_document(cls.document))
        cls.counts,cls.combos=check_design(cls.document,cls.report,cls.config['expected'])
        cls.mapping,cls.slots,cls.network,cls.side=register_mapping(cls.report)

    def small(self):
        c=deepcopy(self.config);c.update(base_cases=16,train_count=12,trials=40);return c

    def test_actual_ontology_and_engine_match_independent_counts(self):
        self.assertEqual(self.report['owl_reasoning']['status'],'passed')
        self.assertEqual(self.report['ontology_mapping']['mapped'],9)
        self.assertEqual((self.counts['candidate_chains'],self.counts['combinations'],self.counts['feasible_networks']),(8,16,4))
        self.assertEqual(self.counts['selection_key'],[['R1','C1','E1'],['R1','C1','E2'],['R2','C1','E3']])
        self.assertEqual(sum(not c['feasible'] for c in self.combos),12)

    def test_input_permutation_does_not_change_semantic_selection(self):
        doc=deepcopy(self.document)
        for field in ('tasks','artifacts','links'):doc[field].reverse()
        report=design(canonical_document(doc));counts,_=check_design(doc,report,self.config['expected'])
        self.assertEqual(counts['selection_key'],self.counts['selection_key'])

    def test_counts_are_derived_not_forced_to_expectations(self):
        doc=deepcopy(self.document)
        next(a for a in doc['artifacts'] if a['id']=='E1')['capacity']=2
        _,combos=independent_enumeration(doc)
        self.assertNotEqual(sum(c['feasible'] for c in combos),4)
        with self.assertRaises(ValueError):check_design(self.document,self.report,dict(self.config['expected'],candidate_chains=99))

    def test_mapping_tracks_actual_subjects_without_cross_task_edges(self):
        self.assertEqual(len(self.network['nodes']),9);self.assertEqual(len(self.network['edges']),6)
        for edge in self.network['edges']:self.assertEqual(edge['feeder'][-1],edge['receiver'][-1])
        shared={p['subject']:set(p['instance_ids']) for p in self.side['state_sharing']}
        self.assertEqual(shared,{'R1':{'A1','A2'},'C1':{'B1','B2','B3'}})
        self.assertTrue(all(not c['exported'] for c in self.side['candidates'] if c['status']=='pending'))

    def test_conditional_draw_uses_actual_upstream_event(self):
        u=np.asarray([[.2,.8],[.5,.5],[.5,.5]])
        flags=event_block(np.asarray(.5),0,u,self.config)
        np.testing.assert_array_equal(flags,[[True,False],[True,False],[True,False]])

    def test_generation_is_deterministic_and_se_matches_archived_counts(self):
        c=self.small();first=generate(self.slots,c);second=generate(self.slots,c)
        self.assertEqual(first['rows'],second['rows']);self.assertEqual(first['audit'],second['audit'])
        check=audit_generation(first,self.slots,c)
        self.assertEqual(check['status'],'passed');self.assertEqual(len(first['rows']),48)
        self.assertEqual(check['micro_events'],3*16*3*3*40)

    def test_event_audit_detects_tampered_observed_se(self):
        c=self.small();g=generate(self.slots,c);g['rows'][0]['node_values']['F1']+=.5
        with self.assertRaises(ValueError):audit_generation(g,self.slots,c)

    def test_shared_H_and_different_task_event_randomness(self):
        c=self.small();g=generate(self.slots,c)
        with np.load(BytesIO(g['archive']),allow_pickle=False) as a:
            h=a['states'];subjects=a['subjects'].tolist();probs=a['probabilities'];u=a['uniforms']
            self.assertFalse(np.array_equal(h[:,0],h[:,1]))
            for j,slot in enumerate(self.slots):
                expected=h[:,subjects.index(slot['information_subject'])]*c['task_coefficients'][slot['task']]
                np.testing.assert_allclose(probs[0,:,j,0],expected,rtol=0,atol=0)
            self.assertFalse(np.array_equal(u[:,0,0,:],u[:,1,0,:]))

    def test_random_stream_separation_and_split_group_integrity(self):
        c=self.small();g=generate(self.slots,c);c['train_count']=10;other=generate(self.slots,c)
        self.assertEqual(g['states'][0]['states'],other['states'][0]['states'])
        self.assertEqual([r['node_values'] for r in g['rows']],[r['node_values'] for r in other['rows']])
        splits={}
        for r in g['rows']:
            splits.setdefault(r['base_case_id'],set()).add(r['split'])
            self.assertNotIn('predictor_values',r)
        self.assertTrue(all(len(v)==1 for v in splits.values()))

    def test_paired_environments_recompute_monotone_events(self):
        g=generate(self.slots,self.small())
        with np.load(BytesIO(g['archive']),allow_pickle=False) as a:
            events=a['events'].astype(int)
            self.assertTrue(np.all(np.diff(events,axis=0)<=0))
            self.assertTrue(np.any(events[0]!=events[-1]))

    def test_invalid_generation_contract_is_rejected(self):
        for field,value in [('base_cases',True),('train_count',16),('environments',[0,0]),('state_range',[1,.2])]:
            c=self.small();c[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):generate(self.slots,c)
        c=self.small();c['probabilities']['command_bad']=1.1
        with self.assertRaises(ValueError):generate(self.slots,c)

    def test_common_source_replay_restores_baseline_and_preserves_other_subject(self):
        c=self.small();g=generate(self.slots,c);local=[]
        for receiver in sorted({e['receiver'] for e in self.network['edges']}):
            params=[dict(feeder=e['feeder'],alpha=.5,beta=40) for e in self.network['edges'] if e['receiver']==receiver]
            local.append(dict(node=receiver,fit=dict(receivers=[dict(status='fitted',parameters=params,delta=0)])))
        fit=dict(network_snapshot=self.network,config_snapshot=dict(analysis=dict(retained_fractions=c['retained_fractions'],max_paths_per_node=100)),
            jobs=[dict(fit_group='env_0',model='base',local_fits=local)])
        rows=common_source_scenarios(self.slots,g,c,fit)
        self.assertEqual(len(rows),30)
        for r in rows:
            if r['retained_fraction']==1:
                self.assertAlmostEqual(r['effect'],0);self.assertAlmostEqual(r['mechanism_reference'],r['mechanism_baseline'])
            if r['subject']=='R1' and r['target']=='F3':self.assertAlmostEqual(r['effect'],0)
            if r['subject']=='R1' and r['target']=='F1' and r['retained_fraction']==0:self.assertAlmostEqual(r['mechanism_reference'],37.5)

    def test_result_inventory_detects_changed_and_extra_files(self):
        with tempfile.TemporaryDirectory() as directory,redirect_stdout(io.StringIO()):
            out=Path(directory);(out/'data.txt').write_text('original',encoding='utf-8')
            (out/'完整包校验.json').write_text(json.dumps(file_inventory(out)),encoding='utf-8')
            self.assertEqual(verify(out),1)
            (out/'data.txt').write_text('changed',encoding='utf-8')
            with self.assertRaises(ValueError):verify(out)
            (out/'data.txt').write_text('original',encoding='utf-8');(out/'extra.txt').write_text('extra')
            with self.assertRaises(ValueError):verify(out)


if __name__=='__main__':unittest.main()
