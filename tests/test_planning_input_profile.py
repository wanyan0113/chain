"""Optional descriptive metadata must not remove planning or numeric constraints."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from task_flow.contracts import ContractError, canonical_sha256, prepare_problem
from task_flow.input_profile import normalize_problem, normalize_design
from task_flow.planner import SearchSession
from task_flow.validator import verify_search_report
from tests.test_task_flow_contracts import fixture

ORIGIN = 'synthetic_simulation'


def without_sources(value):
    if isinstance(value, dict):
        return {k: without_sources(v) for k,v in value.items() if k != 'source_refs'}
    if isinstance(value, list):
        return [without_sources(v) for v in value]
    return value


def simple():
    c,r=fixture();c,r=without_sources(c),without_sources(r)
    r.pop('catalog_sha256')
    return c,r


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


class PlanningProfileTests(unittest.TestCase):
    def test_complete_input_normalization_keeps_exact_core_inputs_and_ids(self):
        c,r=fixture();before=deepcopy((c,r))
        nc,nr,report=normalize_problem(c,r,data_origin=ORIGIN)
        self.assertEqual((nc,nr),before)
        self.assertEqual(report['local_reference_attachments'],[])
        old=SearchSession(prepare_problem(c,r)).run()['candidates']
        new=SearchSession(prepare_problem(nc,nr)).run()['candidates']
        self.assertEqual([x['candidate_id'] for x in old],[x['candidate_id'] for x in new])

    def test_omitted_sources_and_digest_plan_and_independently_replay(self):
        c,r=simple();before=deepcopy((c,r))
        nc,nr,meta=normalize_problem(c,r,data_origin=ORIGIN)
        problem=prepare_problem(nc,nr);result=SearchSession(problem).run()
        self.assertTrue(result['candidates'])
        self.assertEqual(verify_search_report(nc,nr,result)['verification_status'],'passed')
        self.assertEqual((c,r),before)
        self.assertFalse(meta['numeric_defaults_inserted'])
        self.assertFalse(meta['local_references_are_external_evidence'])
        self.assertIsNone(meta['generator_version'])
        # Resolve every locally generated pointer against the retained actual input.
        for entry in meta['local_reference_attachments']:
            node=meta['input_snapshot']
            for key in entry['path'].split('/')[1:]:
                node=node[int(key)] if isinstance(node,list) else node[key]
            self.assertIsInstance(node,dict)
            self.assertEqual(entry['external_source_status'],'not_provided')
        self.assertEqual(nr['catalog_sha256'],canonical_sha256(nc))

    def test_omitted_digest_only_is_computed_without_changing_catalog(self):
        c,r=fixture();r.pop('catalog_sha256')
        nc,nr,_=normalize_problem(c,r,data_origin=ORIGIN)
        self.assertEqual(nc,c);prepare_problem(nc,nr)

    def test_supplied_wrong_digest_is_not_silently_repaired(self):
        c,r=fixture();r['catalog_sha256']='0'*64
        with self.assertRaisesRegex(ContractError,'supplied catalog digest'):
            normalize_problem(c,r,data_origin=ORIGIN)

    def test_missing_origin_is_not_guessed_as_synthetic(self):
        with self.assertRaisesRegex(ContractError,'explicit data origin'):
            normalize_problem(*simple(),data_origin=None)

    def test_missing_activity_semantics_and_goal_remain_errors(self):
        for field in ('preconditions','effects','required_capabilities'):
            c,r=simple();del c['activities'][0][field]
            nc,nr,_=normalize_problem(c,r,data_origin=ORIGIN)
            with self.subTest(field=field),self.assertRaises(ContractError):prepare_problem(nc,nr)
        c,r=simple();del r['goal']
        nc,nr,_=normalize_problem(c,r,data_origin=ORIGIN)
        with self.assertRaises(ContractError):prepare_problem(nc,nr)

    def test_units_and_bounds_are_not_optionalized(self):
        for missing in ('unit','minimum','maximum'):
            c,r=simple()
            fact=dict(id='budget',arguments=[],kind='static',type='integer',minimum=0,maximum=100,unit='credit')
            del fact[missing];c['facts'].append(fact)
            nc,nr,_=normalize_problem(c,r,data_origin=ORIGIN)
            self.assertNotIn(missing,nc['facts'][-1])
            with self.subTest(field=missing),self.assertRaises(ContractError):prepare_problem(nc,nr)

    def test_unknown_reference_is_still_blocked(self):
        c,r=simple();r['initial_state'][0]['fact']['predicate']='missing-predicate'
        nc,nr,_=normalize_problem(c,r,data_origin=ORIGIN)
        with self.assertRaisesRegex(ContractError,'unknown predicate'):prepare_problem(nc,nr)

    def test_ontology_evidence_sources_are_not_fabricated(self):
        c,r=simple()
        r['ontology_evidence']=[dict(fact=r['initial_state'][0]['fact'],value=True,
                                    knowledge_sha256='a'*64,rule_ref='claimed-rule')]
        nc,nr,_=normalize_problem(c,r,data_origin=ORIGIN)
        self.assertNotIn('source_ref',nr['ontology_evidence'][0])
        with self.assertRaises(ContractError):prepare_problem(nc,nr)

    def test_invalid_supplied_sources_are_not_replaced(self):
        c,r=simple();c['activities'][0]['source_refs']='not-an-array'
        nc,nr,_=normalize_problem(c,r,data_origin=ORIGIN)
        self.assertEqual(nc['activities'][0]['source_refs'],'not-an-array')
        with self.assertRaises(ContractError):prepare_problem(nc,nr)

    def test_default_strict_cli_rejects_missing_metadata_minimal_cli_can_replay(self):
        from task_flow.__main__ import main
        with tempfile.TemporaryDirectory() as folder:
            b=Path(folder);c,r=simple();write(b/'c.json',c);write(b/'r.json',r)
            args=['plan','--catalog',str(b/'c.json'),'--request',str(b/'r.json')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args),2)
                self.assertEqual(main(args+['--input-profile','minimal']),2)
                self.assertEqual(main(args+['--input-profile','minimal','--data-origin',ORIGIN,'--verify','--output',str(b/'result.json')]),0)
                self.assertEqual(main(['verify','--catalog',str(b/'c.json'),'--request',str(b/'r.json'),
                                       '--input-profile','minimal','--data-origin',ORIGIN,'--report',str(b/'result.json')]),0)
            report=json.loads((b/'result.json').read_text(encoding='utf-8'))
            self.assertEqual(report['input_profile']['domain_validity'],'pending')

    def test_full_design_inputs_and_metric_values_are_preserved(self):
        from tests.test_observations_v2 import inputs
        jobs,scene,mapping=inputs();before=deepcopy((jobs,scene,mapping))
        nj,ns,nm,meta=normalize_design(jobs,scene,mapping,data_origin=ORIGIN)
        self.assertEqual((nj,ns,nm),before)
        self.assertEqual(meta['local_reference_attachments'],[])

    def test_real_owl_joint_network_with_missing_optional_sources(self):
        from tests.test_observations_v2 import inputs
        from task_flow.joint import run_joint
        from task_flow.joint_verify import verify_joint
        jobs,scene,mapping=inputs()
        jobs,scene,mapping=without_sources(jobs),without_sources(scene),without_sources(mapping)
        for job in jobs:job['request'].pop('catalog_sha256')
        nj,ns,nm,meta=normalize_design(jobs,scene,mapping,data_origin=ORIGIN)
        report=run_joint(nj,ns,nm)
        self.assertEqual(verify_joint(report)['status'],'passed')
        self.assertTrue(report['selected_candidate_ids'])
        self.assertTrue(any(len(c['chain']['plan']['steps'])>3 for c in report['candidates'].values()))
        self.assertFalse(meta['external_sources_authenticated'])

    def test_group_cli_keeps_multi_equipment_four_stage_chains_with_minimal_input(self):
        from workflow_tools.team_demo import inputs
        from task_flow.team_feasibility import main
        jobs,scene,mapping,config=inputs()
        jobs,scene,mapping=without_sources(jobs),without_sources(scene),without_sources(mapping)
        with tempfile.TemporaryDirectory() as folder:
            b=Path(folder);manifest=dict(format_version='task-flow-design-jobs-1.0',jobs=[])
            for i,job in enumerate(jobs):
                job['request'].pop('catalog_sha256')
                write(b/f'c{i}.json',job['catalog']);write(b/f'r{i}.json',job['request'])
                manifest['jobs'].append(dict(task_id=job['task_id'],catalog_file=f'c{i}.json',request_file=f'r{i}.json'))
            for name,data in [('jobs',manifest),('scene',scene),('mapping',mapping),('config',config)]:write(b/(name+'.json'),data)
            with redirect_stdout(io.StringIO()):
                code=main(['--jobs',str(b/'jobs.json'),'--scene',str(b/'scene.json'),'--mapping',str(b/'mapping.json'),
                           '--config',str(b/'config.json'),'--output-dir',str(b/'output'),
                           '--input-profile','minimal','--data-origin',ORIGIN])
            self.assertEqual(code,0)
            report=json.loads((b/'output/team_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['independent_verification']['status'],'passed')
            self.assertTrue(report['feasible_networks'])
            self.assertTrue(any(len(c['plan']['steps'])==4 and any(len(s['members'])>1 for s in c['plan']['steps'])
                                for c in report['candidates'].values()))
            meta=json.loads((b/'output/input_profile.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['data_origin'],ORIGIN)

    def metric_inputs(self):
        import zipfile
        from workflow_tools.team_optimization_demo import synthetic_registry
        root=Path(__file__).resolve().parents[1]
        with zipfile.ZipFile(root/'docs/task-flow/complementary-team-phase1-evidence/full-run.zip') as z:
            source=json.loads(z.read('team_report.json').decode('utf-8'))
        return source,synthetic_registry(source)

    def test_optional_metric_citations_do_not_change_raw_values(self):
        from task_flow.input_profile import normalize_metrics
        from task_flow.team_evaluation import raw_metrics
        source,full=self.metric_inputs();minimal=without_sources(full);minimal.pop('binding')
        normalized,meta=normalize_metrics(source,minimal,data_origin=ORIGIN)
        self.assertEqual(raw_metrics(source,normalized),raw_metrics(source,full))
        self.assertTrue(meta['local_reference_attachments'])
        self.assertFalse(meta['external_sources_authenticated'])

    def test_missing_quality_value_still_blocks_scoring(self):
        from task_flow.input_profile import normalize_metrics
        from task_flow.team_evaluation import raw_metrics
        source,full=self.metric_inputs();minimal=without_sources(full)
        minimal['records'][0]['quality']['value']=None
        normalized,_=normalize_metrics(source,minimal,data_origin=ORIGIN)
        raw,missing=raw_metrics(source,normalized)
        self.assertEqual(raw,{})
        self.assertTrue(missing)
        self.assertIsNone(normalized['records'][0]['quality']['value'])

    def test_bad_metric_binding_is_not_repaired(self):
        from task_flow.input_profile import normalize_metrics
        source,full=self.metric_inputs();full['binding']['source_input_hash']='wrong'
        with self.assertRaisesRegex(ContractError,'metric binding'):
            normalize_metrics(source,full,data_origin=ORIGIN)

    def test_probability_evidence_is_not_supplied_as_local_citation(self):
        from task_flow.input_profile import normalize_metrics
        source,full=self.metric_inputs()
        quality=full['records'][0]['quality']
        quality['semantics']='marginal_success_probability'
        quality['probability_basis']=dict(success_event='all-pass',conditioning_event=None,
            dependence_assumptions='unspecified',evidence_refs=[])
        normalized,_=normalize_metrics(source,without_sources(full),data_origin=ORIGIN)
        self.assertEqual(normalized['records'][0]['quality']['probability_basis']['evidence_refs'],[])


if __name__=='__main__':unittest.main()
