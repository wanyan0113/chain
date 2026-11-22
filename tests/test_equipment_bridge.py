"""Boundary tests: inferred communication is not an approved dependency."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch
from fdna.equipment_bridge import bridge
from fdna.bridge_demo import generate, run, select_response
from fdna.model import ModelError
from fdna.bridge_shared import validate_shared_roots, shared_response
from fdna.network_workflow import fit_network, recursive


class EquipmentBridgeTests(unittest.TestCase):
    def setUp(self):
        directory=Path(__file__).resolve().parents[1]/'data/fdna/bridge'
        self.read=lambda name:json.loads((directory/name).read_text(encoding='utf-8'))
        self.source=self.read('source_report.json');self.mapping=self.read('mapping.json')

    def test_only_selected_chain_and_reviewed_flows_export(self):
        network,side=bridge(self.source,self.mapping)
        self.assertEqual(network['edges'],[dict(feeder='A',receiver='B'),dict(feeder='B',receiver='C')])
        self.assertEqual(side['excluded_selected_chain_ids'],['chain-2-2','chain-3-3'])
        self.assertEqual([r['product_matched'] for r in side['candidates']],[True,True,False,False])
        self.assertFalse(side['policies']['score_used_as_se'])

    def test_unselected_candidate_is_rejected(self):
        self.mapping['chain_ids']=['chain-1-2']
        with self.assertRaises(ModelError):bridge(self.source,self.mapping)

    def test_wrong_equipment_or_duplicate_instance_rejected(self):
        for mode in ['wrong','duplicate']:
            m=deepcopy(self.mapping)
            if mode=='wrong':m['activities'][0]['subject']='effect-1'
            else:m['activities'].append(deepcopy(m['activities'][0]))
            with self.assertRaises(ModelError):bridge(self.source,m)

    def test_communication_without_consumed_product_cannot_be_approved(self):
        self.mapping['reviews'][2]['status']='approved_assumption'
        with self.assertRaises(ModelError):bridge(self.source,self.mapping)

    def test_product_without_direct_equipment_evidence_cannot_be_approved(self):
        self.mapping['activities'][2]['inputs'].append('observation_product')
        rule=self.mapping['reviews'][3];rule.update(product='observation_product',status='approved_assumption')
        with self.assertRaises(ModelError):bridge(self.source,self.mapping)

    def test_unreviewed_product_match_is_discovered_but_not_exported(self):
        self.mapping['reviews']=self.mapping['reviews'][1:]
        network,side=bridge(self.source,self.mapping)
        auto=next(r for r in side['candidates'] if r['id'].startswith('auto_'))
        self.assertTrue(auto['product_matched']);self.assertFalse(auto['exported'])
        self.assertEqual(network['edges'],[dict(feeder='B',receiver='C')])

    def test_cycles_rejected_instead_of_silently_deleting_edges(self):
        self.mapping['activities'][2]['outputs']=['feedback']
        self.mapping['activities'][0]['inputs']=['feedback']
        self.mapping['reviews'].append(dict(id='feedback',feeder='C',receiver='A',product='feedback',status='approved_assumption',rationale='synthetic test'))
        chain=next(c for t in self.source['tasks'] for c in t['chains'] if c['id']=='chain-1-1')
        chain['link_evidence'].append(dict(**{'from':'effect-0','to':'demo-eye'},communicates=True))
        with self.assertRaises(ModelError):bridge(self.source,self.mapping)

    def test_no_implicit_score_normalization_or_state_merging(self):
        self.mapping['activities'][0]['metric']['normalization']='chain_score*100'
        with self.assertRaises(ModelError):bridge(self.source,self.mapping)

    def test_single_root_pairing_determinism_and_no_split_leakage(self):
        network,_=bridge(self.source,self.mapping)
        experiment=self.read('experiment.json');nodes=self.read('generation_nodes.json')
        rows,states=generate(network,nodes,experiment)
        self.assertEqual((rows,states),generate(network,nodes,experiment))
        self.assertEqual(len(rows),240)
        for i in range(0,len(rows),2):
            self.assertEqual(rows[i]['predictor_values'],rows[i+1]['predictor_values'])
            self.assertEqual(rows[i]['split'],rows[i+1]['split'])
            self.assertEqual(rows[i]['node_values'],rows[i]['predictor_values'])
        self.assertEqual(sum(r['split']=='train' for r in rows),180)

    def test_sampler_rejects_unsupported_settings(self):
        network,_=bridge(self.source,self.mapping);e=self.read('experiment.json');e['process_std']=2
        with self.assertRaises(ValueError):generate(network,self.read('generation_nodes.json'),e)

    def test_sampler_rejects_invalid_noise_counts_and_ranges(self):
        network,_=bridge(self.source,self.mapping)
        for key,value in [('noise_std',[-2]),('noise_std',[0,0.0]),('noise_std',[]),
                          ('root_range',[100,10]),('root_range',[-1,100]),('base_cases',True),
                          ('seed',-1),('train_count',120),('unknown',1)]:
            e=self.read('experiment.json');e[key]=value
            with self.subTest(key=key,value=value),self.assertRaises((ValueError,ModelError)):
                generate(network,self.read('generation_nodes.json'),e)

    def test_existing_output_is_rejected_before_fitting(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'keep.txt').write_text('keep',encoding='utf-8')
            with patch('fdna.bridge_demo.fit_network') as fitter:
                with self.assertRaises(ValueError):run('nonexistent_config',directory)
                fitter.assert_not_called()
            self.assertEqual((Path(directory)/'keep.txt').read_text(encoding='utf-8'),'keep')

    def test_conflicting_reviews_cannot_silently_approve_a_pending_claim(self):
        duplicate=deepcopy(self.mapping['reviews'][0]);duplicate.update(id='conflict',status='pending')
        self.mapping['reviews'].append(duplicate)
        with self.assertRaises(ModelError):bridge(self.source,self.mapping)

    def test_boolean_noise_and_repetition_settings_are_rejected(self):
        network,_=bridge(self.source,self.mapping)
        for key in ('process_std','interaction_gamma','repetitions'):
            e=self.read('experiment.json');e[key]=True
            with self.subTest(key=key),self.assertRaises((ValueError,ModelError)):generate(network,self.read('generation_nodes.json'),e)

    def test_response_target_uses_graph_terminal_not_node_name_sorting(self):
        network=dict(nodes=['root','z_middle','a_leaf'],edges=[dict(feeder='root',receiver='z_middle'),dict(feeder='z_middle',receiver='a_leaf')])
        effects=[dict(intervened_node='root',target=n,group='sigma2',baseline_sample=b) for n in ('z_middle','a_leaf') for b in ('b2','b1')]
        root,target,group,selected=select_response(network,dict(topology=dict(roots=['root'])),dict(effects=effects))
        self.assertEqual((root,target,group),('root','a_leaf','sigma2'))
        self.assertEqual(len(selected),1);self.assertEqual(selected[0]['baseline_sample'],'b1')


class FullSelectedBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory=Path(__file__).resolve().parents[1]/'data/fdna/bridge_full'
        cls.read=staticmethod(lambda name:json.loads((directory/name).read_text(encoding='utf-8')))
        cls.source=cls.read('source_report.json');cls.mapping=cls.read('mapping.json')
        cls.network,cls.side=bridge(cls.source,cls.mapping)
        cls.experiment=cls.read('experiment.json');cls.nodes=cls.read('generation_nodes.json')
        cls.rows,cls.states=generate(cls.network,cls.nodes,cls.experiment)
        cls.config=cls.read('fit.json');cls.config['models'].pop('free_delta');cls.config['models']['base']['starts']=2
        cls.cleanrows=[r for r in cls.rows if r['fit_group']=='sigma0']
        cls.report=fit_network(cls.network,cls.cleanrows,cls.config)

    def test_all_selected_chains_covered_without_cross_task_edges(self):
        self.assertEqual(len(self.network['nodes']),9);self.assertEqual(len(self.network['edges']),6)
        self.assertEqual(self.side['excluded_selected_chain_ids'],[])
        for edge in self.network['edges']:
            self.assertEqual(edge['feeder'][-1],edge['receiver'][-1])
        self.assertEqual(len(self.side['state_sharing']),2)

    def test_shared_physical_subjects_require_complete_registry(self):
        for mode in ('absent','incomplete','duplicate'):
            m=deepcopy(self.mapping)
            if mode=='absent':m.pop('state_sharing')
            elif mode=='incomplete':m['state_sharing'][0]['instance_ids'].pop()
            else:m['state_sharing'].append(deepcopy(m['state_sharing'][0]))
            with self.subTest(mode=mode),self.assertRaises(ModelError):bridge(self.source,m)

    def test_shared_root_cannot_silently_become_independent(self):
        e=deepcopy(self.experiment);e.pop('root_coupling')
        with self.assertRaises(ValueError):validate_shared_roots(self.network,self.side,e)

    def test_common_source_is_drawn_once_per_base_case(self):
        for s in self.states:
            h=s['root_latents']['demo_eye_common_state'];v=s['values']
            self.assertAlmostEqual(v['A1'],h-4);self.assertAlmostEqual(v['A2'],h);self.assertAlmostEqual(v['A3'],h+4)
        self.assertEqual(len(self.states),120)

    def test_invalid_common_source_members_offsets_and_groups_rejected(self):
        for mode in ('overlap','unknown','missing_offset','outside_range'):
            e=deepcopy(self.experiment);g=e['root_coupling'][0]
            if mode=='overlap':e['root_coupling'].append(dict(g,id='other'))
            elif mode=='unknown':g['nodes'].append('unknown')
            elif mode=='missing_offset':g['offsets'].pop('A1')
            else:g['offsets']['A1']=-100
            with self.subTest(mode=mode),self.assertRaises((ValueError,ModelError)):generate(self.network,self.nodes,e)

    def test_individual_product_intervention_preserves_other_tasks(self):
        job=self.report['jobs'][0];local={f['node']:f['fit']['receivers'][0] for f in job['local_fits']}
        roots={n:self.rows[0]['predictor_values'][n] for n in ('A1','A2','A3')}
        base,_=recursive(self.network,local,roots)
        changed,_=recursive(self.network,local,roots,{'A1':0})
        self.assertEqual(base['C2'],changed['C2']);self.assertEqual(base['C3'],changed['C3'])

    def test_common_source_response_preserves_baseline_and_tracks_root_clipping(self):
        result=shared_response(self.network,self.nodes,self.experiment,self.states,self.cleanrows,self.report,[0,.5,1])
        self.assertEqual(len(result),9)
        self.assertEqual({r['target'] for r in result},{'C1','C2','C3'})
        for r in result:
            if r['retained_fraction']==1:
                self.assertAlmostEqual(r['predicted'],r['baseline'])
                self.assertAlmostEqual(r['independent_mechanism'],r['independent_baseline'])
            if r['retained_fraction']==0:self.assertTrue(r['root_clipped'])
        self.assertTrue(all(r['effect']<0 for r in result if r['retained_fraction']==.5))


if __name__=='__main__':unittest.main()
