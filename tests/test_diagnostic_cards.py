from copy import deepcopy
import unittest

from fdna.diagnostic_cards import build_cards, delta_contrasts, mapping_audit, split_identity, parameter_rows
from fdna.model import ModelError
from fdna.structure import digest


def fixture():
    rows=[dict(base_case_id='case1',sample_id='s1',scenario_id='e',fit_group='e',split='train'),
          dict(base_case_id='case2',sample_id='s2',scenario_id='e',fit_group='e',split='holdout')]
    policies={'base':'excluded_for_base_check','free_delta':'per_receiver_bounded'}
    configs={m:dict(seed=17,delta_policy=p) for m,p in policies.items()}
    jobs=[]
    for model in policies:
        receiver=dict(parameters=[dict(feeder='A',alpha=.4,beta=20)],delta=None if model=='base' else -2,
                      delta_bounds=None if model=='base' else [-10,10],sensitivity={},
                      near_best_parameter_spread={'alpha:A':.2,'beta:A':30})
        local=dict(node='B',status='fitted',flags=[],fit=dict(settings_snapshot=configs[model],receivers=[receiver]),
                   diagnostic=dict(parameter_names=['alpha:A','beta:A'],parameter_spread=[.01,2]))
        metrics=[dict(node='B',split=s,prediction=k,rmse=4 if model=='base' else 3)
                 for s in ('train','holdout') for k in ('local','recursive')]
        jobs.append(dict(fit_group='e',model=model,local_fits=[local],metrics=metrics,points=[]))
    report=dict(input_sha256='a'*64,data_sha256=digest(rows),config_snapshot=dict(models=configs),jobs=jobs,
                network_snapshot=dict(nodes=['A','B'],edges=[dict(feeder='A',receiver='B')]))
    def activity(subject,inputs,outputs):
        return dict(subject=subject,role='record',context='synthetic',chain_id='c',label=subject,inputs=inputs,outputs=outputs,
                    metric=dict(definition='qualified fraction',raw_range=[0,1],direction='higher_is_better',normalization='100*x'),
                    state_identity_reason='task-specific')
    side=dict(capability_instances=dict(A=activity('s1',[],['p']),B=activity('s2',['p'],[])),candidates=[
        dict(id='edge',feeder='A',receiver='B',product='p',product_matched=True,exported=True,status='approved_assumption',
             rationale='registered consumption',equipment_link_evidence=[])])
    return report,rows,side


class DiagnosticCardTests(unittest.TestCase):
    def test_identity_is_stable_and_result_sensitive(self):
        report,rows,side=fixture();cards=build_cards(report,rows,side)
        self.assertEqual(cards,build_cards(deepcopy(report),rows,side))
        self.assertEqual(len({c['fit_id'] for c in cards}),2)
        report['jobs'][0]['local_fits'][0]['fit']['settings_snapshot']['seed']=18
        changed=build_cards(report,rows,side)
        self.assertNotEqual(cards[0]['fit_id'],changed[0]['fit_id'])
        self.assertNotEqual(cards[0]['model_config_sha256'],changed[0]['model_config_sha256'])
        self.assertEqual(changed[0]['fit_seed'],18)

    def test_bad_data_digest_and_base_case_leakage_rejected(self):
        report,rows,side=fixture();bad=deepcopy(rows);bad[0]['split']='holdout'
        with self.assertRaises(ModelError):build_cards(report,bad,side)
        with self.assertRaises(ModelError):split_identity(rows+[dict(rows[0],split='holdout')])
        self.assertEqual(split_identity(rows),split_identity(list(reversed(rows))))

    def test_duplicate_fit_key_rejected(self):
        report,rows,side=fixture();report['jobs'].append(deepcopy(report['jobs'][0]))
        with self.assertRaises(ModelError):build_cards(report,rows,side)

    def test_spread_definitions_remain_separate(self):
        report,rows,side=fixture();card=build_cards(report,rows,side)[0]
        self.assertEqual(card['parameters'][0]['near_best_spread'],.2)
        self.assertEqual(card['parameters'][0]['strict_near_spread'],.01)
        self.assertEqual(card['parameters'][1]['near_best_spread'],30)
        self.assertEqual(card['parameters'][1]['strict_near_spread'],2)
        self.assertFalse(any(p['name']=='delta' for p in card['parameters']))

    def test_delta_pairing_keeps_both_prediction_levels_and_no_fixed_group(self):
        report,rows,side=fixture();cards=build_cards(report,rows,side);pairs=delta_contrasts(cards)
        self.assertEqual(len(pairs),1);self.assertEqual(pairs[0]['delta'],-2)
        self.assertEqual(pairs[0]['delta_policy'],'per_receiver_bounded')
        for k in ('local','recursive'):
            for s in ('train','holdout'):self.assertEqual(pairs[0][k+'_'+s+'_improvement'],1)
        cards[1]['group_split_sha256']='wrong'
        with self.assertRaises(ModelError):delta_contrasts(cards)

    def test_mapping_checks_semantics_and_exported_topology(self):
        report,rows,side=fixture();nodes,edges=mapping_audit(report,side,'synthetic_scenario')
        self.assertEqual(len(nodes),2);self.assertEqual(len(edges),1)
        self.assertTrue(all(not r['real_dependency_verified'] for r in nodes+edges))
        side['candidates'][0]['exported']=False
        with self.assertRaises(ModelError):mapping_audit(report,side,'synthetic_scenario')
        side['candidates'][0]['exported']=True;side['capability_instances']['B']['inputs']=[]
        with self.assertRaises(ModelError):mapping_audit(report,side,'synthetic_scenario')

    def test_postprocessing_does_not_mutate_input(self):
        report,rows,side=fixture();before=deepcopy((report,rows,side))
        delta_contrasts(build_cards(report,rows,side));mapping_audit(report,side,'synthetic_scenario')
        self.assertEqual((report,rows,side),before)


if __name__=='__main__':unittest.main()
