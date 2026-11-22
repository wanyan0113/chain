"""End-to-end data mapping, held-out isolation, partial failure and interventions."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from fdna.model import ModelError
from fdna.network_workflow import read_samples,fit_network,validate_samples,analyze_dependencies,recursive,export_outputs,_paths
from fdna.workflow_common import settings,scalar_forward,write_bundle,csv_text,make_model
from fdna.multi_input_recovery import truth_geometry,input_design


class NetworkWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network=dict(id='workflow_test',nodes=['A','B','T','Q'],edges=[dict(feeder='A',receiver='T'),dict(feeder='B',receiver='T'),dict(feeder='T',receiver='Q')])
        cls.config=dict(input_role='observed',pool_scenarios=False,data_kind='simulated',models={'base':settings('base',['T','Q'],starts=6)})
        cls.rows=[]
        for i,x in enumerate(np.random.default_rng(913).uniform(0,100,(144,2))):
            a,b=map(float,x);t=scalar_forward([a,b],[.4,.65],[20,35]);q=scalar_forward([t],[.5],[15])
            cls.rows.append(dict(sample_id=f's{i:03d}',base_case_id=f'b{i}',scenario_id='fixed',fit_group='g',split='train' if i<112 else 'holdout',node_values=dict(A=a,B=b,T=t,Q=q)))
        cls.report=fit_network(cls.network,cls.rows,cls.config)

    def test_fits_and_recurses_from_roots_without_intermediate_leakage(self):
        self.assertEqual(self.report['status'],'complete')
        self.assertGreater(self.report['jobs'][0]['core_crosscheck_count'],0)
        j=self.report['jobs'][0]
        self.assertLess(j['core_max_difference'],1e-10)
        for m in j['metrics']:
            self.assertLess(m['rmse'],1e-4)
        self.assertFalse(self.report['policies']['topology_inferred'])

    def test_holdout_targets_do_not_select_parameters(self):
        changed=deepcopy(self.rows)
        for r in changed:
            if r['split']=='holdout': r['node_values']['T']=0.;r['node_values']['Q']=100.
        other=fit_network(self.network,changed,self.config)
        for a,b in zip(self.report['jobs'][0]['local_fits'],other['jobs'][0]['local_fits']):
            self.assertEqual(a['fit']['receivers'][0]['parameters'],b['fit']['receivers'][0]['parameters'])

    def test_edge_and_node_order_does_not_change_mapping(self):
        n=deepcopy(self.network);n['nodes'].reverse();n['edges'].reverse()
        other=fit_network(n,list(reversed(self.rows)),self.config)
        self.assertEqual(self.report['jobs'][0]['points'],other['jobs'][0]['points'])

    def test_missing_local_inputs_are_reported_not_zero_filled(self):
        changed=deepcopy(self.rows);changed[0]['node_values']['A']=None
        report=fit_network(self.network,changed,self.config)
        local=next(l for l in report['jobs'][0]['local_fits'] if l['node']=='T')
        self.assertIn(dict(id='s000',reason='MISSING_LOCAL_SE'),local['fit']['receivers'][0]['omitted'])
        points=[p for p in report['jobs'][0]['points'] if p['sample_id']=='s000']
        self.assertTrue(all(p['recursive_prediction'] is None for p in points))

    def test_insufficient_fit_blocks_descendants(self):
        report=fit_network(self.network,self.rows[:3]+self.rows[-2:],self.config)
        self.assertEqual(report['status'],'incomplete')
        t=next(l for l in report['jobs'][0]['local_fits'] if l['node']=='T')
        self.assertEqual(t['status'],'insufficient_samples')
        self.assertTrue(all(p['recursive_prediction'] is None for p in report['jobs'][0]['points']))

    def test_invalid_topology_and_metadata_fail_before_optimization(self):
        variants=[]
        n=deepcopy(self.network);n['edges'].append(dict(feeder='Q',receiver='A'));variants.append((n,self.rows,self.config))
        n=deepcopy(self.network);n['edges'].append(dict(feeder='X',receiver='T'));variants.append((n,self.rows,self.config))
        r=deepcopy(self.rows);r.append(deepcopy(r[0]));variants.append((self.network,r,self.config))
        r=deepcopy(self.rows);r[-1]['base_case_id']=r[0]['base_case_id'];variants.append((self.network,r,self.config))
        r=deepcopy(self.rows);r[0]['node_values']['A']=float('nan');variants.append((self.network,r,self.config))
        r=deepcopy(self.rows);r[0]['node_values']['A']=101;variants.append((self.network,r,self.config))
        for n,r,c in variants:
            with self.subTest(n=n,r=r[0]),patch('fdna.network_workflow.fit_parameters',side_effect=AssertionError('must validate first')):
                with self.assertRaises(ModelError): fit_network(n,r,c)

    def test_distinct_scenarios_need_explicit_pooling(self):
        r=deepcopy(self.rows);r[-1]['scenario_id']='different'
        with self.assertRaisesRegex(ModelError,'合并'): validate_samples(self.network,r,self.config)
        c=deepcopy(self.config);c['pool_scenarios']=True
        self.assertEqual(validate_samples(self.network,r,c),['g'])

    def test_clean_input_role_requires_explicit_predictors(self):
        c=deepcopy(self.config);c['input_role']='clean_predictors_observed_targets'
        with self.assertRaisesRegex(ModelError,'predictor_values'): validate_samples(self.network,self.rows,c)

    def test_wide_and_long_csv_roundtrip_and_duplicates(self):
        meta=('sample_id','base_case_id','scenario_id','fit_group','split')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            wide=[{**{k:r[k] for k in meta},**r['node_values']} for r in self.rows]
            long=[{**{k:r[k] for k in meta},'node_id':n,'se':v} for r in self.rows for n,v in r['node_values'].items()]
            (path/'wide.csv').write_text(csv_text(wide),encoding='utf-8')
            (path/'long.csv').write_text(csv_text(long),encoding='utf-8')
            self.assertEqual(read_samples(path/'wide.csv',self.network),self.rows)
            self.assertEqual(read_samples(path/'long.csv',self.network,'long'),self.rows)
            (path/'long.csv').write_text(csv_text(long+[long[0]]),encoding='utf-8')
            with self.assertRaisesRegex(ModelError,'主键重复'): read_samples(path/'long.csv',self.network,'long')

    def test_middle_node_intervention_suspends_incoming_and_leaves_others(self):
        result=analyze_dependencies(self.report,self.rows,dict(retained_fractions=[0.,.5],max_paths_per_node=10))
        selected=[e for e in result['effects'] if e['intervened_node']=='T' and e['retained_fraction']==0]
        self.assertEqual({e['target'] for e in selected},{'Q'})
        self.assertTrue(all(c.get('non_descendant_invariant',True) for c in result['checks']))
        self.assertTrue(all(c.get('core_max_difference',0)<1e-9 for c in result['checks']))
        self.assertFalse(result['policies']['causal_effect_established'])

    def test_paths_are_capped_with_truncation_not_summed(self):
        paths,truncated=_paths(self.network,'A',1)
        self.assertEqual(len(paths),1);self.assertTrue(truncated)

    def test_arbitrary_ids_do_not_collide_with_generated_subjects(self):
        network=dict(id='names',nodes=['A','U_A','__workflow_subject__A'],edges=[dict(feeder='A',receiver='U_A')])
        model=make_model(network)
        self.assertFalse(set(n['id'] for n in model['nodes']) & set(n['id'] for n in model['subjects']))

    def test_free_delta_workflow_and_intervention_use_same_unclipped_model(self):
        rows=deepcopy(self.rows)
        for r in rows:
            r['node_values']['T']-=3
            r['node_values']['Q']=scalar_forward([r['node_values']['T']],[.5],[15],-2)
        config=deepcopy(self.config);config['models']={'free_delta':settings('free_delta',['T','Q'],starts=6)}
        report=fit_network(self.network,rows,config)
        self.assertEqual(report['status'],'complete')
        self.assertTrue(all(l['fit']['receivers'][0]['delta'] is not None for l in report['jobs'][0]['local_fits']))
        analysis=analyze_dependencies(report,rows,dict(retained_fractions=[0.],max_paths_per_node=100))
        self.assertLess(max(c.get('core_max_difference') or 0 for c in analysis['checks']),1e-9)

    def test_output_cannot_overwrite_existing_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_bundle(tmp,export_outputs(self.report))
            with self.assertRaises(ModelError): write_bundle(tmp,{'new.txt':'x'})
            self.assertTrue((Path(tmp)/'manifest.json').is_file())

    def test_ragged_csv_rows_are_not_silently_accepted(self):
        header='sample_id,base_case_id,scenario_id,fit_group,split,A,B,T,Q\n'
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'data.csv'
            for row in ('s,b,fixed,g,train,10,20,30,40,discarded\n','s,b,fixed,g,train,10,20,30\n'):
                path.write_text(header+row,encoding='utf-8')
                with self.subTest(row=row),self.assertRaisesRegex(ModelError,'字段数'):
                    read_samples(path,self.network)

    def test_reserved_node_names_require_unambiguous_long_or_json_input(self):
        network=dict(id='reserved',nodes=['A','sample_id'],edges=[dict(feeder='A',receiver='sample_id')])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'data.csv'
            path.write_text('sample_id,base_case_id,scenario_id,fit_group,split,A\ns,b,fixed,g,train,10\n',encoding='utf-8')
            with self.assertRaisesRegex(ModelError,'保留列冲突'): read_samples(path,network)

    def test_dependency_report_rejects_different_data_snapshot(self):
        rows=deepcopy(self.rows);rows[-1]['node_values']['A']=0.
        with self.assertRaisesRegex(ModelError,'分析数据与拟合报告不一致'):
            analyze_dependencies(self.report,rows,dict(retained_fractions=[0.],max_paths_per_node=10))

    def test_multifeeder_design_covers_identification_and_diagnostic_case(self):
        for n in (3,5):
            x=input_design(np.random.default_rng(16),192,n,'recovery',dict(high=[85,98],single_low=[10,35]))
            a=[.35,.55,.45,.65,.25][:n];b=[14,18,22,26,30][:n]
            for free in (False,True):
                geometry=truth_geometry(x,a,b,free,20)
                self.assertEqual(geometry['rank'],2*n+int(free))
                bad=truth_geometry(np.random.default_rng(17).uniform(10,35,(192,n)),[0.]*n,b,free,20)
                self.assertLess(bad['rank'],bad['parameter_count'])

if __name__=='__main__': unittest.main()
