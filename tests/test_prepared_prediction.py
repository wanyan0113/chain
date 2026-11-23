from copy import deepcopy
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import patch

import numpy as np

from fdna.model import ModelError
from fdna.prepared_prediction import PreparedTopology, PreparedPredictor
from fdna.refit_propagation import replay


class PreparedPredictionTests(unittest.TestCase):
    def setUp(self):
        self.network=dict(id='prepared_test',nodes=['A','B','F','Z'],edges=[dict(feeder='A',receiver='B'),dict(feeder='B',receiver='F')])
        self.fits={n:dict(status='fitted',parameters=[dict(feeder=p,alpha=.6,beta=10.)],delta=-3.) for n,p in [('B','A'),('F','B')]}
        self.roots=dict(A=np.array([20.,70.]),Z=np.array([40.,50.]))

    def test_prepared_state_is_independent_of_source_mutation(self):
        prepared=PreparedTopology.from_network(self.network).bind(self.fits)
        before=prepared.predict(self.roots)
        self.network['edges'].clear();self.network['nodes'].append('bad')
        self.fits['B']['parameters'][0].update(alpha=0,beta=100)
        np.testing.assert_array_equal(before,prepared.predict(self.roots))
        with self.assertRaises(FrozenInstanceError):prepared.topology=None
        with self.assertRaises(TypeError):prepared.equations['B']=None
        with self.assertRaises(ValueError):prepared.equations['B'][1].setflags(write=True)

    def test_calls_do_not_retain_root_data_or_intervention(self):
        prepared=PreparedTopology.from_network(self.network).bind(self.fits)
        before=prepared.predict(self.roots);root_copy=deepcopy(self.roots)
        prepared.predict(self.roots,('B',np.zeros(2)))
        np.testing.assert_array_equal(prepared.predict(self.roots),before)
        for n in self.roots:np.testing.assert_array_equal(root_copy[n],self.roots[n])
        self.roots['A'][:]=0
        self.assertFalse(np.array_equal(before,prepared.predict(self.roots)))

    def test_new_parameter_binding_does_not_change_old_binding(self):
        topology=PreparedTopology.from_network(self.network);first=topology.bind(self.fits);before=first.predict(self.roots)
        self.fits['B']['delta']=-8
        second=topology.bind(self.fits)
        self.assertFalse(np.array_equal(second.predict(self.roots),before))
        np.testing.assert_array_equal(first.predict(self.roots),before)

    def test_immutable_preparation_still_validates_inputs_each_call(self):
        prepared=PreparedTopology.from_network(self.network).bind(self.fits)
        for roots in [dict(self.roots,B=[90,90]),dict(self.roots,A=[1]),dict(self.roots,A=[[1,2]])]:
            with self.assertRaises(ModelError):prepared.predict(roots)
        for forced in [('bad',np.zeros(2)),('A',np.zeros(3))]:
            with self.assertRaises(ModelError):prepared.predict(self.roots,forced)
        bad=deepcopy(self.fits);bad['B']['parameters'][0]['alpha']=np.nan
        with self.assertRaises(ModelError):PreparedTopology.from_network(self.network).bind(bad)

    def test_same_intervention_is_evaluated_once_for_multiple_targets(self):
        record=dict(fits=[dict(f,node=n,fit_group='g',model='base') for n,f in self.fits.items()])
        rows=[dict(split='holdout',fit_group='g',base_case_id='one',node_values=dict(A=40,Z=20,B=999,F=999))]
        pairs=[('A','B'),('A','F'),('B','F')];fractions=[0,.5,1]
        ranges={('g','B','A'):(0,100),('g','F','B'):(0,100)}
        original=PreparedPredictor.predict
        with patch.object(PreparedPredictor,'predict',autospec=True,side_effect=original) as spy:
            result=replay(self.network,record,rows,[('g','base')],['one'],pairs,fractions,ranges)
        self.assertEqual(spy.call_count,1+2*len(fractions))
        self.assertTrue(result['valid'].all())
        np.testing.assert_array_equal(result['forced'][0,0],result['forced'][0,1])

    def test_replay_cache_does_not_cross_model_jobs(self):
        rows=[dict(split='holdout',fit_group='g',base_case_id='one',node_values=dict(A=40,Z=20))]
        all_fits=[]
        for mode,delta in [('base',None),('free_delta',-8.)]:
            all_fits.extend(dict(f,node=n,fit_group='g',model=mode,delta=delta) for n,f in self.fits.items())
        result=replay(self.network,dict(fits=all_fits),rows,[('g','base'),('g','free_delta')],['one'],[('A','F')],[0,1],{('g','F','B'):(0,100)})
        self.assertNotEqual(result['baseline'][0,0,2],result['baseline'][1,0,2])
        np.testing.assert_array_equal(result['decrease'][:,:,-1],0.)


if __name__=='__main__':unittest.main()
