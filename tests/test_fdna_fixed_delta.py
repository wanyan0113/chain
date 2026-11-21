"""Fixed delta is a configured offset, never an extra fitted coordinate."""
from copy import deepcopy
import unittest

from fdna.additive import ADDITIVE_MODEL
from fdna.fitting import fit_parameters, fit_check
from fdna.recovery import recovery_model, independent_forward
from fdna.residuals import diagnose_residuals
from fdna.model import ModelError
from fdna.structure import digest


class FixedDeltaTests(unittest.TestCase):
    def setUp(self):
        self.model=recovery_model('fixed_delta')
        self.settings=dict(format_version='1.0',id='fixed',basis='Known fixed offset test',
            model=ADDITIVE_MODEL,solver='L-BFGS-B',seed=21,starts=4,max_nfev=3000,
            delta_policy='per_receiver_fixed',delta_values={'R':-5},output_policy='no_clipping')
        self.samples=dict(format_version='1.0',id='fixed_samples',data_kind='method_check',
            basis='Independently expanded scalar formula',case_sha256=digest(self.model),records=[])
        for split,grid in [('train',[0,20,40,60,80,100]),('holdout',[10,30,70,90])]:
            for i,a in enumerate(grid):
                for j,b in enumerate(grid):
                    y=independent_forward(a,b,[.4,.65],[20,35])[0]-5
                    self.samples['records'].append(dict(id=f'{split}:{i}:{j}',split=split,node_values=dict(A=a,B=b,R=y)))

    def test_fixed_offset_has_four_coordinates_and_recovers_alpha_beta(self):
        fit=fit_parameters(self.model,self.samples,self.settings); row=fit['receivers'][0]
        self.assertEqual(fit['status'],'complete')
        self.assertEqual(row['delta'],-5)
        self.assertIsNone(row['delta_bounds'])
        self.assertEqual(row['sensitivity']['parameter_count'],4)
        self.assertTrue(all(len(a['parameters'])==4 for a in row['attempts']))
        self.assertAlmostEqual(row['parameters'][0]['alpha'],.4,places=6)
        self.assertAlmostEqual(row['parameters'][1]['beta'],35,places=5)
        self.assertLess(row['metrics']['holdout']['rmse'],1e-6)
        self.assertEqual(diagnose_residuals(self.model,fit)['status'],'complete')

    def test_holdout_cannot_change_parameters_and_tampered_fixed_offset_rejected(self):
        original=fit_parameters(self.model,self.samples,self.settings)
        for r in self.samples['records']:
            if r['split']=='holdout': r['node_values']['R']=0
        changed=fit_parameters(self.model,self.samples,self.settings)
        self.assertEqual(original['receivers'][0]['parameters'],changed['receivers'][0]['parameters'])
        original['receivers'][0]['delta']=-4
        with self.assertRaisesRegex(ModelError,'固定配置'): diagnose_residuals(self.model,original)

    def test_fixed_delta_must_match_receivers_and_cannot_coexist_with_bounds(self):
        for kind in ('missing','extra','bounds','bool','policy'):
            settings=deepcopy(self.settings)
            if kind=='missing': settings['delta_values']={}
            if kind=='extra': settings['delta_values']['absent']=0
            if kind=='bounds': settings['delta_bounds']={'R':[-10,10]}
            if kind=='bool': settings['delta_values']['R']=True
            if kind=='policy': settings['delta_policy']='per_receiver_bounded'
            with self.subTest(kind=kind),self.assertRaises(ModelError): fit_parameters(self.model,self.samples,settings)

    def test_fixed_fit_check_compares_to_generation_delta(self):
        model=deepcopy(self.model)
        for edge,a,b in zip(model['edges'],[.4,.65],[20,35]): edge.update(alpha=a,beta=b)
        generation=dict(format_version='1.0',id='fixed_generation',basis='Artificial known offset',
            seed=21,random_train=64,random_holdout=32,include_leaf_corners=False,
            extension=dict(model=ADDITIVE_MODEL,basis='Artificial',delta_values={'R':-5},output_policy='no_clipping'))
        result=fit_check(model,generation,self.settings)
        self.assertEqual(result['status'],'complete')
        generation['extension']['delta_values']['R']=-4
        with self.assertRaisesRegex(ModelError,'生成真值'): fit_check(model,generation,self.settings)


if __name__=='__main__': unittest.main()
