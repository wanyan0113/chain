from copy import deepcopy
import json
from pathlib import Path
import unittest

from fdna.generation_interaction import run_interaction_network_check
from fdna.generation_fitting import fit_joint_network
from fdna.generation_delta_comparison import run_comparison
from fdna.generation_delta_repeats import summarize_comparison
from fdna.generation_interaction_sensitivity import validate_frozen_protocol
from fdna.model import ModelError

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'
def read(n):return json.loads((ROOT/n).read_text(encoding='utf-8'))


class InteractionSensitivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config=read('generation_interaction_conditions.json')
        config['reference_experiment']['base_experiment'].update(base_cases=40,train_count=32,repetitions=1,noise_std=[0,2])
        cls.generation=run_interaction_network_check(read('generation_network.json'),read('generation_network_nodes.json'),config)
        fc=read('generation_interaction_fit.json');fc.update(noise_std=[0,2],repetitions=[1]);fc['settings']['starts']=4
        dc=read('generation_interaction_delta.json');dc.update(noise_std=[0,2],repetitions=[1])
        cls.base=fit_joint_network(cls.generation,fc)
        cls.comparison=run_comparison(cls.generation,cls.base,dc,allow_repeats=True)
        cls.summary=summarize_comparison(cls.comparison)
        cls.fc=read('generation_interaction_sensitivity_fit.json');cls.fc.update(noise_std=[0,2],repetitions=[1]);cls.fc['settings']['starts']=4
        cls.dc=read('generation_interaction_sensitivity_delta.json');cls.dc.update(noise_std=[0,2],repetitions=[1])

    def validate(self,fc=None,dc=None,base=None,comparison=None):
        return validate_frozen_protocol(self.generation,fc or self.fc,dc or self.dc,base or self.base,
            comparison or self.comparison,self.summary)

    def test_frozen_protocol_preserved(self):
        audit=self.validate()
        self.assertFalse(audit['primary_refitted']);self.assertFalse(audit['new_samples_generated'])
        self.assertEqual(audit['fitted_groups'],['interaction_005','interaction_030'])

    def test_disallows_changed_solver_selection_noise_and_delta(self):
        for kind in ('starts','group','missing','duplicate','noise','repetitions','fixed_delta','bounds'):
            fc=deepcopy(self.fc);dc=deepcopy(self.dc)
            if kind=='starts':fc['settings']['starts']+=1
            elif kind=='group':fc['groups'][0]='interaction_015';dc['groups']=fc['groups']
            elif kind=='missing':fc['groups'].pop();dc['groups']=fc['groups']
            elif kind=='duplicate':fc['groups'].append(fc['groups'][0]);dc['groups']=fc['groups']
            elif kind=='noise':fc['noise_std']=[0];dc['noise_std']=[0]
            elif kind=='repetitions':dc['repetitions']=[2]
            elif kind=='fixed_delta':dc['fixed_delta']['T']=-6
            else:dc['free_delta_bounds']['T']=[-20,10]
            with self.subTest(kind=kind),self.assertRaises(ModelError):self.validate(fc=fc,dc=dc)

    def test_tampered_frozen_reports_rejected(self):
        base=deepcopy(self.base);base['jobs'][0]['parameters'][0]['alpha']+=.01
        with self.assertRaises(ModelError):self.validate(base=base)
        comparison=deepcopy(self.comparison);comparison['metrics'][0]['rmse']+=1
        with self.assertRaises(ModelError):self.validate(comparison=comparison)

    def test_small_complete_weak_strong_comparison(self):
        self.validate()
        base=fit_joint_network(self.generation,self.fc)
        self.assertEqual(base['status'],'complete');self.assertEqual(len(base['jobs']),4)
        c=run_comparison(self.generation,base,self.dc,allow_repeats=True)
        s=summarize_comparison(c)
        self.assertEqual(s['status'],'complete');self.assertEqual(s['counts']['new_local_fits'],24)
        self.assertEqual({r['group'] for r in s['metrics']},{'interaction_005','interaction_030'})

    def test_strength_summaries_pair_repetitions_not_row_order(self):
        from fdna.generation_interaction_sensitivity_report import paired_strength_metrics
        rows=[];gammas={'zero':0,'weak':.05}
        for mode in ('base','fixed_delta','free_delta'):
            for g in gammas:
                for rep in (1,2):
                    for split in ('train','holdout'):
                        for target in ('clean','observed'):
                            for pred in ('local','recursive'):
                                value=rep+(1 if g=='weak' else 0)
                                rows.append(dict(model=mode,group=g,noise_sigma=0,repetition=rep,node='R',split=split,
                                    target=target,prediction=pred,count=8,rmse=value,mae=value,mean_residual=value,r_squared=None))
        summaries,paired=paired_strength_metrics(list(reversed(rows)),gammas,[0],[1,2],['R'],'zero')
        self.assertTrue(all(r['rmse_change']==1 for r in paired))
        r=next(r for r in summaries if r['model']=='base' and r['group']=='weak' and r['split']=='holdout'
            and r['target']=='clean' and r['prediction']=='recursive' and r['quantity']=='rmse')
        self.assertEqual(r['n'],2);self.assertEqual(r['mean'],2.5)
        for bad in (rows[:-1],rows+[rows[0]]):
            with self.assertRaises(ModelError):paired_strength_metrics(bad,gammas,[0],[1,2],['R'],'zero')

    def test_strength_samples_reject_unpaired_conditions(self):
        from fdna.generation_interaction_sensitivity_report import verify_sample_pairing
        self.assertEqual(verify_sample_pairing(self.generation,['interaction_005','interaction_030'],'varying_conditions'),160)
        g=deepcopy(self.generation)
        row=next(r for r in g['observations'] if r['group']=='interaction_005')
        row['conditions_by_node']['T']['C']+=.01
        with self.assertRaises(ModelError):verify_sample_pairing(g,['interaction_005'],'varying_conditions')


if __name__=='__main__':unittest.main()
