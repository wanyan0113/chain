from copy import deepcopy
import json
from pathlib import Path
import unittest

from fdna.generation_coefficients import run_coefficient_check
from fdna.generation_fitting import audit_generation, fit_joint_network
from fdna.generation_varying import run_varying_network_check
from fdna.generation_coefficient_experiment import validate_protocol
from fdna.generation_delta_comparison import run_comparison
from fdna.model import ModelError
from fdna.structure import digest

ROOT=Path(__file__).resolve().parents[1]/'data/fdna'
def read(n):return json.loads((ROOT/n).read_text(encoding='utf-8'))


class CoefficientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network=read('generation_network.json');cls.nodes=read('generation_network_nodes.json')
        cls.config=read('generation_coefficient_conditions.json')
        cls.config['reference_experiment']['base_experiment'].update(base_cases=30,train_count=24,repetitions=1,noise_std=[0,2])
        cls.g=run_coefficient_check(cls.network,cls.nodes,cls.config)
        cls.reference=run_varying_network_check(cls.network,cls.nodes,cls.config['reference_experiment'])

    def test_frozen_controls_pairing_and_continuity(self):
        self.assertEqual(self.g['status'],'complete');self.assertTrue(all(self.g['checks'].values()))
        for k in ('root_inputs','clean_runs','observations'):
            self.assertEqual(digest(self.g[k][:len(self.reference[k])]),digest(self.reference[k]))
        self.assertEqual(self.g['source_varying_generation_sha256'],digest(self.reference))
        self.assertEqual(self.g['node_configs_snapshot'],self.nodes)
        audit_generation(self.g)

    def test_terminal_change_matches_scalar_formula(self):
        source={r['base_case_id']:r for r in self.reference['clean_runs'] if r['group']=='varying_conditions'}
        for row in self.g['clean_runs']:
            if row['group'] not in self.config['weight_groups']:continue
            old=source[row['base_case_id']];values=row['node_values_clean'];c=row['conditions_by_node']['R']
            weight=self.config['weight_groups'][row['group']]['Q']
            factor=c['C']*(.8+.2*c['M'])*(1-.4*c['E'])
            expected_shift=(weight-.62)*(values['Q']-values['D_aux'])*factor
            self.assertAlmostEqual(values['R']-old['node_values_clean']['R'],expected_shift,places=11)
            self.assertEqual({n:v for n,v in values.items() if n!='R'},
                {n:v for n,v in old['node_values_clean'].items() if n!='R'})

    def test_invalid_weights_not_silently_normalized(self):
        for weights in ({'Q':.7,'D_aux':.4},{'Q':-1,'D_aux':2},{'Q':True,'D_aux':0},
                        {'Q':.52},{'Q':.62,'D_aux':.38}):
            c=deepcopy(self.config);c['weight_groups']['weight_q052']=weights
            with self.subTest(weights=weights),self.assertRaises(ModelError):run_coefficient_check(self.network,self.nodes,c)
        c=deepcopy(self.config);c['weight_groups']['weight_q072']=c['weight_groups']['weight_q052']
        with self.assertRaises(ModelError):run_coefficient_check(self.network,self.nodes,c)

    def test_replay_rejects_config_or_output_tampering(self):
        for kind in ('config','output','variant'):
            g=deepcopy(self.g)
            if kind=='config':g['coefficient_config_snapshot']['weight_groups']['weight_q052']={'Q':.51,'D_aux':.49}
            elif kind=='output':g['observations'][-1]['node_values_clean']['R']+=.01
            else:g['variant_node_configs']['weight_q052']['nodes']['R']['weights']['Q']=.5
            with self.subTest(kind=kind),self.assertRaises(ModelError):audit_generation(g)

    def test_small_formal_pipeline_and_frozen_settings(self):
        fc=read('generation_varying_fit.json');fc.update(noise_std=[0,2],repetitions=[1]);fc['settings']['starts']=3
        dc=read('generation_varying_delta.json');dc.update(noise_std=[0,2],repetitions=[1])
        base=fit_joint_network(self.reference,fc)
        comparison=run_comparison(self.reference,base,dc,allow_repeats=True)
        cf=deepcopy(fc);cf['groups']=list(self.config['weight_groups']);cd=deepcopy(dc);cd['groups']=cf['groups']
        validate_protocol(self.g,cf,cd,self.reference,base,comparison)
        new=fit_joint_network(self.g,cf)
        result=run_comparison(self.g,new,cd,allow_repeats=True)
        self.assertEqual(result['status'],'complete');self.assertEqual(len(new['jobs']),4)
        from fdna.generation_delta_repeats import summarize_comparison
        from fdna.generation_coefficient_report import build_report
        report=build_report(self.g,new,result,summarize_comparison(result),self.reference,base,comparison,summarize_comparison(comparison))
        audit=json.loads(report['汇报来源核验.json'])
        self.assertEqual(audit['unchanged_node_metric_checks'],192)
        self.assertEqual(audit['paired_observation_checks'],120)
        bad=deepcopy(new);bad['jobs'][0]['metrics'][0]['rmse']+=1
        with self.assertRaises(ModelError):build_report(self.g,bad,result,summarize_comparison(result),
            self.reference,base,comparison,summarize_comparison(comparison))
        for key in ('starts','delta','group','source'):
            badf=deepcopy(cf);badd=deepcopy(cd);ref=deepcopy(self.reference)
            if key=='starts':badf['settings']['starts']+=1
            elif key=='delta':badd['free_delta_bounds']['R'][0]=-20
            elif key=='group':badf['groups'].pop()
            else:ref['root_inputs'][0]['split']='holdout'
            with self.subTest(key=key),self.assertRaises(ModelError):validate_protocol(self.g,badf,badd,ref,base,comparison)


if __name__=='__main__':unittest.main()
