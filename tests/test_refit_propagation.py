import unittest

import numpy as np

from fdna.model import ModelError
from fdna.refit_propagation import predict, replay, summarize


class RefitPropagationTests(unittest.TestCase):
    def setUp(self):
        self.net=dict(id='test',nodes=['a','b','c','z'],edges=[dict(feeder='a',receiver='c'),dict(feeder='b',receiver='c')])
        self.fit={'c':dict(status='fitted',parameters=[dict(feeder='a',alpha=.7,beta=3),dict(feeder='b',alpha=.4,beta=8)],delta=-10)}
        self.roots={'a':np.array([10.,80.]),'b':np.array([30.,60.]),'z':np.array([20.,40.])}

    def test_multifeeder_scalar_equivalence_and_no_clipping(self):
        roots={'a':np.linspace(0,100,31),'b':np.linspace(100,0,31),'z':np.ones(31)}
        result=predict(self.net,self.fit,roots)
        for i in range(31):
            a=roots['a'][i];b=roots['b'][i]
            expected=min(((100-.7*(100-a))+(100-.4*(100-b)))/2,min(a+3,b+8))-10
            self.assertAlmostEqual(result[i,2],expected)
        self.assertLess(result[0,2],0)

    def test_clamp_suspends_incoming_and_non_descendants(self):
        original=predict(self.net,self.fit,self.roots)
        changed=predict(self.net,self.fit,self.roots,('c',np.array([1.,2.])))
        np.testing.assert_array_equal(changed[:,2],[1.,2.])
        np.testing.assert_array_equal(changed[:,[0,1,3]],original[:,[0,1,3]])
        np.testing.assert_array_equal(predict(self.net,self.fit,self.roots,('a',self.roots['a'])),original)

    def test_permutations(self):
        net=dict(self.net,nodes=list(reversed(self.net['nodes'])),edges=list(reversed(self.net['edges'])))
        fit={'c':dict(self.fit['c'],parameters=list(reversed(self.fit['c']['parameters'])))}
        np.testing.assert_allclose(predict(net,fit,self.roots),predict(self.net,self.fit,self.roots))

    def test_reject_target_leakage_shapes_unknown_and_bad_parameters(self):
        for roots in [dict(self.roots,c=[90,90]),dict(self.roots,a=[1,2,3])]:
            with self.assertRaises(ModelError):predict(self.net,self.fit,roots)
        with self.assertRaises(ModelError):predict(self.net,self.fit,self.roots,('unknown',np.zeros(2)))
        with self.assertRaises(ModelError):predict(self.net,{},self.roots)
        fit={'c':dict(self.fit['c'],parameters=[dict(feeder='a',alpha=1.1,beta=3),self.fit['c']['parameters'][1]])}
        with self.assertRaises(ModelError):predict(self.net,fit,self.roots)

    def test_failure_and_missing_panel_never_substitute(self):
        net=dict(id='test',nodes=['a','b'],edges=[dict(feeder='a',receiver='b')])
        fit=dict(node='b',fit_group='g',model='base',status='fitted',parameters=[dict(feeder='a',alpha=1,beta=0)],delta=None)
        rows=[dict(fit_group='g',base_case_id='one',split='holdout',node_values={'a':20,'b':999})]
        args=(rows,[('g','base')],['one','missing'],[('a','b')],[0,1],{('g','b','a'):(10,30)})
        result=replay(net,{'fits':[fit]},*args)
        np.testing.assert_array_equal(result['valid'][0,0,0],[True,False])
        self.assertEqual(result['decrease'][0,0,0,0],20)
        old={'summary':[dict(group='g',model='base',intervened_node='a',target='b',retained_fraction=f,mean_decrease=0) for f in [0,1]]}
        stacked={k:np.stack([v,v]) for k,v in result.items()}
        summary=summarize(stacked,[('g','base')],[('a','b')],[0,1],old)
        self.assertEqual(summary[0]['complete_models'],0);self.assertIsNone(summary[0]['median'])
        failed=replay(net,{'fits':[dict(fit,status='failed')]},*args)
        self.assertFalse(failed['valid'].any());self.assertTrue(np.isnan(failed['decrease']).all())


if __name__=='__main__':unittest.main()
