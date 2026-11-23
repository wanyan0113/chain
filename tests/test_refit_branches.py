from copy import deepcopy
import unittest

import numpy as np

from fdna.model import ModelError
from fdna.refit_branches import branch, feeder_values, audit_model, aggregate


class RefitBranchTests(unittest.TestCase):
    def test_strict_branches_tie_missing_and_unclipped_delta(self):
        codes,y,sod,cod=branch([0,40,80,np.nan],.5,30,-50)
        np.testing.assert_array_equal(codes,[2,3,1,0])
        self.assertEqual(y[0],-20)
        self.assertTrue(np.isnan(y[3]))
        np.testing.assert_array_equal(branch([0,40,80],.5,30,7)[0],codes[:3])

    def test_derivatives_only_on_unique_branch(self):
        x=[0.,80.];p=.5;b=30.;step=1e-5
        code,y,*_=branch(x,p,b)
        shifted=branch(x,p,b+step)[1]
        np.testing.assert_allclose((shifted-y)/step,[1.,0.],atol=1e-8)
        self.assertEqual(branch([40+1e-9],p,b)[0][0],3)
        self.assertEqual(branch([40+1e-5],p,b)[0][0],1)
        self.assertEqual(branch([40-1e-5],p,b)[0][0],2)

    def test_feeder_comes_from_forced_changed_or_baseline(self):
        axes=dict(pairs=[['a','b'],['a','f'],['b','f']],nodes=['a','b','f','z'])
        arrays=dict(forced=np.full((1,1,3,1,2),5.),changed=np.full((1,1,3,1,2),7.),baseline=np.full((1,1,2,4),9.))
        np.testing.assert_array_equal(feeder_values(arrays,0,0,1,0,'a',axes),[5,5])
        np.testing.assert_array_equal(feeder_values(arrays,0,0,1,0,'b',axes),[7,7])
        np.testing.assert_array_equal(feeder_values(arrays,0,0,1,0,'z',axes),[9,9])

    def fixture(self):
        net=dict(id='fixture',nodes=['a','b'],edges=[dict(feeder='a',receiver='b')])
        fit=dict(node='b',fit_group='g',model='base',status='fitted',parameters=[dict(feeder='a',alpha=.5,beta=30.)],delta=None)
        rows=[dict(split='train',fit_group='g',base_case_id='t',node_values=dict(a=80,b=999))]
        axes=dict(jobs=[['g','base']],pairs=[['a','b']],nodes=['a','b'],base_cases=['h'],fractions=[0,1])
        arrays=dict(valid=np.ones((1,1,1,2,1),dtype=bool),forced=np.array([[[[[0.],[20.]]]]]),
                    changed=np.array([[[[[30.],[50.]]]]]),baseline=np.array([[[[20.,50.]]]]),
                    extrapolated=np.ones((1,1,1,2,1),dtype=bool))
        return net,{'fits':[fit]},rows,['t','t'],arrays,0,axes,1e-8

    def test_do_not_call_baseline_cod_a_degradation_switch(self):
        training,conditions,panel,before,error=audit_model(*self.fixture())
        self.assertTrue(training[0]['strict_training_beta_inactive'])
        self.assertEqual(training[0]['distinct_training_cases'],1)
        self.assertEqual(training[0]['planned_training_rows'],2)
        self.assertTrue(all(r['activated_already_recursive_cod']==1 for r in conditions))
        self.assertTrue(all(r['activated_from_recursive_sod']==0 for r in conditions))
        self.assertEqual(aggregate(conditions)[0]['activated_beta_records'],1)

    def test_near_tie_training_is_not_strictly_inactive(self):
        args=list(self.fixture());args[2][0]['node_values']['a']=40
        training,conditions,*_=audit_model(*args)
        self.assertFalse(training[0]['strict_training_beta_inactive'])
        self.assertEqual(training[0]['training_near_tie'],2)
        self.assertTrue(all(r['activated_beta_records']==0 for r in conditions))

    def test_missing_response_and_failed_job_remain_unavailable(self):
        args=list(self.fixture());args[4]['valid'][:]=False
        args[1]['fits'][0]['status']='failed';args[4]['changed'][:]=np.nan
        training,conditions,panel,*_=audit_model(*args)
        self.assertFalse(training[0]['job_available']);self.assertFalse(panel.any())
        self.assertTrue(all(r['available_records']==0 for r in conditions))

    def test_multifeeder_is_explicitly_rejected(self):
        args=list(self.fixture());args[0]['nodes'].append('z');args[0]['edges'].append(dict(feeder='z',receiver='b'))
        with self.assertRaises(ModelError):audit_model(*args)

    def test_wrong_predictions_and_parameters_are_rejected(self):
        args=list(self.fixture());args[4]['changed'][0,0,0,0,0]+=1
        with self.assertRaises(ModelError):audit_model(*args)
        for alpha,beta,tol in [(2,30,1e-8),(.5,-1,1e-8),(.5,30,-1),(.5,np.nan,1e-8)]:
            with self.assertRaises(ModelError):branch([30],alpha,beta,tolerance=tol)


if __name__=='__main__':unittest.main()
