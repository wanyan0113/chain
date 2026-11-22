"""Branch interpretation, near ties, finite changes and frozen-report provenance."""
from copy import deepcopy
import unittest
from unittest.mock import patch
import csv
import io

from fdna.branch_explanation import explain_dependencies,fitted_core,core_state,branch_figures
from fdna.model import ModelError
from fdna.network_workflow import analyze_dependencies
from fdna.structure import digest
from fdna.workflow_common import settings


class BranchExplanationTests(unittest.TestCase):
    def setUp(self):
        self.network=dict(id='branches',nodes=['A','B','T','Q'],edges=[dict(feeder='A',receiver='T'),dict(feeder='B',receiver='T'),dict(feeder='T',receiver='Q')])
        t=dict(receiver='T',status='fitted',parameters=[dict(feeder='A',alpha=.5,beta=10.),dict(feeder='B',alpha=.5,beta=20.)],delta=None)
        q=dict(receiver='Q',status='fitted',parameters=[dict(feeder='T',alpha=.5,beta=15.)],delta=None)
        self.job=dict(fit_group='g',model='base',local_fits=[dict(node=r['receiver'],status='fitted',flags=['LOCAL_RANK_DEFICIENT'],
            fit=dict(receivers=[r]),training_predictor_ranges={p['feeder']:[0.,100.] for p in r['parameters']}) for r in (t,q)])
        self.rows=[dict(sample_id='h1',base_case_id='b1',fit_group='g',scenario_id='one',split='holdout',node_values=dict(A=20.,B=80.,T=30.,Q=45.))]
        self.report=dict(network_snapshot=self.network,config_snapshot=dict(input_role='observed',models={'base':settings('base',['T','Q'])}),
            data_sha256=digest(self.rows),topology=dict(roots=['A','B'],order=['A','B','T','Q'],receivers=['T','Q']),jobs=[self.job])

    def state(self,roots=None,forced=None):
        row=deepcopy(self.rows[0])
        if roots:row['node_values'].update(roots)
        return core_state(self.report,self.job,*fitted_core(self.report,self.job),row,forced)

    def analysis(self):
        return analyze_dependencies(self.report,self.rows,dict(retained_fractions=[0.,.5],max_paths_per_node=100))

    def test_cod_branch_slopes_match_small_perturbation_but_not_large_degradation(self):
        baseline=self.state()['T'];slopes={r['feeder']:r['slope'] for r in baseline['edge_slopes']}
        self.assertEqual(baseline['binding_candidates'],['cod:A'])
        self.assertEqual(slopes,dict(A=1.,B=0.))
        step=1e-4
        self.assertAlmostEqual((self.state(dict(A=20.+step))['T']['value']-baseline['value'])/step,slopes['A'])
        self.assertEqual(self.state(dict(B=80.+step))['T']['value'],baseline['value'])
        self.assertLess(self.state(dict(B=0.))['T']['value'],baseline['value'])

    def test_sod_branch_uses_alpha_divided_by_number_of_feeders(self):
        for p in self.job['local_fits'][0]['fit']['receivers'][0]['parameters']:p['beta']=100.
        baseline=self.state()['T'];self.assertEqual(baseline['binding_candidates'],['sod_mean'])
        self.assertEqual([r['slope'] for r in baseline['edge_slopes']],[.25,.25])
        self.assertAlmostEqual((self.state(dict(A=20.001))['T']['value']-baseline['value'])/.001,.25)

    def test_exact_and_near_ties_keep_candidates_without_unique_derivative(self):
        # SOD=75; COD:A=75 and COD:B=100.
        params=self.job['local_fits'][0]['fit']['receivers'][0]['parameters'];params[0]['beta']=55.
        state=self.state()['T'];self.assertEqual(state['branch_type'],'near_tie')
        self.assertEqual(state['binding_candidates'],['cod:A','sod_mean'])
        self.assertTrue(all(r['slope'] is None for r in state['edge_slopes']))
        params[0]['beta']+=5e-9
        self.assertEqual(self.state()['T']['branch_type'],'near_tie')
        params[0]['beta']+=1e-5
        self.assertEqual(self.state()['T']['branch_type'],'sod')

    def test_delta_does_not_get_compared_to_pre_delta_candidate_values(self):
        self.job['model']='free_delta';self.report['config_snapshot']['models']={'free_delta':settings('free_delta',['T','Q'])}
        self.job['local_fits'][0]['fit']['receivers'][0].update(delta=-3.)
        self.job['local_fits'][1]['fit']['receivers'][0].update(delta=-2.)
        state=self.state();self.assertEqual(state['T']['value'],27.)
        self.assertEqual(state['T']['base_before_delta'],30.)
        self.assertEqual(state['T']['binding_candidates'],['cod:A'])
        self.assertEqual(state['Q']['value'],40.)

    def test_intervened_node_has_no_fictional_active_incoming_branch(self):
        state=self.state(forced={'T':0.})
        self.assertEqual(state['T']['branch_type'],'intervened')
        self.assertTrue(state['T']['incoming_suspended'])
        self.assertEqual(state['T']['edge_slopes'],[])
        self.assertEqual(state['Q']['value'],15.)

    def test_blocked_node_does_not_receive_branch_or_derivative(self):
        row=deepcopy(self.rows[0]);row['node_values']['A']=None
        state=core_state(self.report,self.job,*fitted_core(self.report,self.job),row)
        self.assertEqual(state['T']['branch_type'],'blocked')
        self.assertEqual(state['T']['edge_slopes'],[])

    def test_frozen_explanation_preserves_response_and_inactive_structural_edge_example(self):
        analysis=self.analysis()
        with patch('fdna.network_workflow.fit_parameters',side_effect=AssertionError('must not refit')):
            result=explain_dependencies(self.report,self.rows,analysis)
        self.assertEqual(result['audit']['response_records'],10)
        self.assertLess(result['audit']['max_response_difference'],1e-10)
        half=next(r for r in result['response_branches'] if (r['intervened_node'],r['target'],r['retained_fraction'])==('B','T',.5))
        zero=next(r for r in result['response_branches'] if (r['intervened_node'],r['target'],r['retained_fraction'])==('B','T',0.))
        self.assertEqual(half['effect'],0.)
        self.assertFalse(half['branch_changed'])
        self.assertEqual(zero['effect'],-10.)
        self.assertTrue(zero['branch_changed'])
        self.assertIn('LOCAL_RANK_DEFICIENT',result['baseline_edges'][0]['fit_flags'])

    def test_different_data_or_modified_effects_are_rejected(self):
        analysis=self.analysis();rows=deepcopy(self.rows);rows[0]['node_values']['A']=21.
        with self.assertRaisesRegex(ModelError,'数据与拟合报告'):explain_dependencies(self.report,rows,analysis)
        for field,value in [('effect',999.),('forced_value',99.),('outside',True)]:
            changed=deepcopy(analysis);changed['effects'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ModelError):explain_dependencies(self.report,self.rows,changed)

    def test_duplicate_and_non_descendant_effect_records_are_rejected(self):
        analysis=self.analysis();analysis['effects'].append(deepcopy(analysis['effects'][0]))
        with self.assertRaisesRegex(ModelError,'主键重复'):explain_dependencies(self.report,self.rows,analysis)
        analysis=self.analysis();analysis['effects'][0]['target']='A'
        with self.assertRaisesRegex(ModelError,'不是.*后代'):explain_dependencies(self.report,self.rows,analysis)

    def test_blocked_target_is_exported_as_missing_in_figures_not_zero(self):
        q=self.job['local_fits'][1];q.update(fit=None,status='insufficient_samples',flags=['FIT_INSUFFICIENT_SAMPLES'])
        analysis=self.analysis();result=explain_dependencies(self.report,self.rows,analysis)
        self.assertEqual(result['audit']['blocked_responses'],6)
        # Rendering is checked on real output separately; this checks missing-data semantics.
        with patch('matplotlib.figure.Figure.savefig'):
            outputs=branch_figures(self.report,result)
        rows=list(csv.DictReader(io.StringIO(outputs['图表数据_分支变化与近零响应.csv'].lstrip('\ufeff'))))
        q=next(r for r in rows if r['target']=='Q')
        self.assertEqual(q['evaluated'],'0')
        self.assertEqual(q['zero_response_percent'],'')


if __name__=='__main__':unittest.main()
