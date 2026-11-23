from copy import deepcopy
import unittest

import numpy as np

from fdna.edge_ablation import forward, fit_means, receiver_value, scenarios
from fdna.model import ModelError


class EdgeAblationTests(unittest.TestCase):
    def setUp(self):
        self.network=dict(id='test',nodes=['A','B','F','Q'],edges=[dict(feeder='A',receiver='B'),dict(feeder='B',receiver='F')])
        self.fits={n:dict(status='fitted',parameters=[dict(feeder=p,alpha=.5,beta=10.)],delta=-2.) for n,p in [('B','A'),('F','B')]}
        self.roots={'A':np.array([20.,80.]),'Q':np.array([40.,50.])}

    def test_new_root_uses_constant_and_downstream_recomputes(self):
        sub=deepcopy(self.network);sub['edges']=sub['edges'][1:]
        result=forward(sub,['A','Q'],self.fits,{'B':30.},self.roots)
        np.testing.assert_array_equal(result['B'],[30.,30.])
        np.testing.assert_array_equal(result['F'],[38.,38.])
        altered=forward(sub,['A','Q'],self.fits,{'B':30.},dict(self.roots,A=np.array([0.,100.])))
        np.testing.assert_array_equal(result['F'],altered['F'])
        suspended=forward(sub,['A','Q'],self.fits,{'B':30.},self.roots,('B',np.zeros(2)))
        np.testing.assert_array_equal(suspended['F'],[8.,8.])

    def test_all_edges_cut_preserves_targets_and_ignores_original_fits(self):
        sub=dict(self.network,edges=[])
        out=forward(sub,['A','Q'],{},dict(B=30.,F=60.),self.roots)
        self.assertEqual(set(out),set(self.network['nodes']))
        np.testing.assert_array_equal(out['F'],[60.,60.])
        changed=forward(sub,['A','Q'],{},dict(B=30.,F=60.),self.roots,('A',np.zeros(2)))
        np.testing.assert_array_equal(changed['F'],out['F'])

    def test_targets_cannot_be_added_as_observed_roots(self):
        sub=dict(self.network,edges=self.network['edges'][1:])
        for constants,inputs in [({},dict(self.roots,B=np.array([99.,99.]))),({},self.roots),({'B':30.,'F':40.},self.roots)]:
            with self.assertRaises(ModelError):forward(sub,['A','Q'],self.fits,constants,inputs)

    def test_training_mean_not_affected_by_any_holdout_truth(self):
        rows=[dict(fit_group='g',sample_id='t1',base_case_id='t1',split='train',node_values={'B':20.}),
              dict(fit_group='g',sample_id='t2',base_case_id='t2',split='train',node_values={'B':40.}),
              dict(fit_group='g',sample_id='h',base_case_id='h',split='holdout',node_values={'B':100.})]
        means=fit_means(rows,['B']);rows[-1]['node_values']['B']=0.
        self.assertEqual(means,fit_means(rows,['B']));self.assertEqual(means[0]['value'],30.)
        with self.assertRaises(ModelError):fit_means(rows,['missing'])

    def test_unmodified_graph_matches_local_equations_and_input_order(self):
        result=forward(self.network,['A','Q'],self.fits,{},self.roots)
        np.testing.assert_array_equal(result['B'],receiver_value(self.fits['B'],self.roots))
        np.testing.assert_array_equal(result['F'],receiver_value(self.fits['F'],{'B':result['B']}))
        reversed_network=dict(self.network,nodes=list(reversed(self.network['nodes'])),edges=list(reversed(self.network['edges'])))
        other=forward(reversed_network,['Q','A'],self.fits,{},self.roots)
        for n in result:np.testing.assert_array_equal(result[n],other[n])

    def test_invalid_forcing_shape_parameters_and_cycles_rejected(self):
        for forced in [('unknown',np.zeros(2)),('B',np.zeros(3)),('B',np.full(2,101.))]:
            with self.assertRaises(ModelError):forward(self.network,['A','Q'],self.fits,{},self.roots,forced)
        fits=deepcopy(self.fits);fits['B']['parameters'][0]['feeder']='Q'
        with self.assertRaises(ModelError):forward(self.network,['A','Q'],fits,{},self.roots)
        fits=deepcopy(self.fits);fits['B']['status']='failed'
        with self.assertRaises(ModelError):forward(self.network,['A','Q'],fits,{},self.roots)
        cycle=deepcopy(self.network);cycle['edges'].append(dict(feeder='F',receiver='A'))
        with self.assertRaises(ModelError):forward(cycle,['A','Q'],self.fits,{},self.roots)

    def test_no_clipping_and_missing_roots_propagate_without_truth_substitution(self):
        fits=deepcopy(self.fits);fits['B']['delta']=-40.
        roots=dict(self.roots,A=np.array([0.,np.nan]))
        result=forward(self.network,['A','Q'],fits,{},roots)
        self.assertEqual(result['B'][0],-30.)
        self.assertTrue(np.isnan(result['F'][1]))

    def test_multifeeder_experiment_requires_another_registered_policy(self):
        network=deepcopy(self.network);network['edges'].append(dict(feeder='Q',receiver='B'))
        with self.assertRaises(ModelError):scenarios(network,{})


if __name__=='__main__':unittest.main()
