"""Boundary tests: inferred communication is not an approved dependency."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch
from fdna.equipment_bridge import bridge
from fdna.bridge_demo import generate, run
from fdna.model import ModelError


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


if __name__=='__main__':unittest.main()
