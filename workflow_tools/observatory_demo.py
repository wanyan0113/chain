"""Actual task_flow -> HermiT -> chains/network -> v2 semantic observation demo."""
import argparse
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import io
import json
from pathlib import Path

from task_flow.design_bridge import main as bridge_cli
from task_flow.contracts import prepare_problem
from task_flow.planner import SearchSession
from task_flow.joint_verify import verify_joint
from workflow_tools.benchmark_joint import ROOT,save
from workflow_tools.observations_v2 import skeleton,freeze,assess

EXAMPLE=ROOT/'task_flow/examples/observatory_multistage'


def make_simulation(report,n=12):
    study=skeleton(report);p=study['protocol']
    p.update(study_id='observatory-enrolled-cohort-simulation',data_origin='synthetic_simulation',
        stage_definition='Each stage predicts binary resonance class against a synthetic independent class reference',
        endpoint_definition='Separate simulated telemetry: actual stable state compared with required stable=1',
        registration_mode='retrospective_analysis')
    p['acceptance'].update(tolerance=.05,minimum_calibration=4,minimum_endpoint=4,minimum_stratum_class=1)
    base=datetime(2025,1,1,tzinfo=timezone.utc)
    for flow in p['flows']:
        flow['correction_design']='present'
        for stage in flow['stages']:
            stage.update(artifact_version='simulated-device-1.0',calibration_input_condition='correct_reference_label')
        for split in ('calibration','evaluation'):
            for i in range(n):
                eid=f"{flow['task_id']}:{split}:{i}";gold=str(i%2);execution='execution:'+eid
                study['enrollments'].append(dict(enrollment_id=eid,sample_id=eid,cluster_id=eid,execution_id=execution,
                    flow_id=flow['flow_id'],split=split,stratum='all',eligible=True,exclusion_reason='',
                    expected_stages=[s['stage_instance_id'] for s in flow['stages']],registered_at=base.isoformat()))
                previous=None
                for index,stage in enumerate(flow['stages']):
                    correct=(i%(index+3))!=0;out=gold if correct else str(1-int(gold));oid=f'{eid}:{stage["stage_instance_id"]}:1'
                    row=dict(observation_id=oid,enrollment_id=eid,sample_id=eid,execution_id=execution,
                        flow_id=flow['flow_id'],stage_instance_id=stage['stage_instance_id'],attempt_id=1,
                        activity_id=stage['activity_id'],activity_version=stage['activity_version'],artifact_id=stage['artifact_id'],
                        artifact_version=stage['artifact_version'],input_reference=previous['output_reference'] if previous and split=='evaluation' else 'sim://reference-input/'+oid,
                        output_reference='sim://observed-output/'+oid,input_source_observation_id=previous['observation_id'] if previous and split=='evaluation' else '',
                        input_label=previous['output_label'] if previous and split=='evaluation' else gold,gold_label=gold,output_label=out,
                        execution_status='executed',observation_status='recorded',stage_outcome='correct' if correct else 'incorrect',
                        evaluation_method=stage['evaluation_method'],ground_truth_reference='sim://independent-class-reference/'+eid,
                        missing_reason='',recorded_at=(base+timedelta(seconds=20+index*10)).isoformat())
                    study['observations'].append(row);previous=row
                    if flow['task_id']=='T3' and split=='evaluation' and i==0 and index==2:
                        retry=deepcopy(row);retry.update(observation_id=oid[:-1]+'2',attempt_id=2,output_label=gold,
                            stage_outcome='correct',output_reference='sim://retry-output/'+oid,
                            recorded_at=(base+timedelta(seconds=21+index*10)).isoformat())
                        study['observations'].append(retry)
                if split=='evaluation':
                    # A different physical-effect event, not derived from stage accuracy or its product.
                    stable='0' if i%4==0 else '1'
                    study['endpoints'].append(dict(enrollment_id=eid,execution_id=execution,
                        endpoint_target_id=flow['endpoint_target_id'],evaluation_method=flow['endpoint_method'],
                        actual_label=stable,reference_label='1',final_outcome='success' if stable=='1' else 'failure',
                        result_reference='sim://separate-stability-telemetry/'+eid,
                        ground_truth_reference='sim://required-stable-reference/'+eid,missing_reason='',
                        evaluated_at=(base+timedelta(minutes=10)).isoformat()))
    return study


def run(output):
    if output.exists():raise ValueError('choose a new output directory')
    manifest=json.loads((EXAMPLE/'jobs.json').read_text(encoding='utf-8'))
    scene=json.loads((EXAMPLE/'scene.json').read_text(encoding='utf-8'))
    if any(k in scene['knowledge'] for k in ('sequences_inline','sequences_file')):raise AssertionError('input contains a prewritten route')
    output.mkdir(parents=True)
    with redirect_stdout(io.StringIO()):
        code=bridge_cli(['--mode','joint_candidate_optimization','--jobs',str(EXAMPLE/'jobs.json'),
            '--scene',str(EXAMPLE/'scene.json'),'--mapping',str(EXAMPLE/'mapping.json'),'--output-dir',str(output/'network')])
    if code:raise AssertionError('actual task_flow/OWL/network pipeline failed')
    report=json.loads((output/'network/joint_report.json').read_text(encoding='utf-8'))
    if verify_joint(report)['status']!='passed':raise AssertionError('independent design replay failed')
    from tests.joint_reference import audit_joint
    oracle=audit_joint(report)
    summaries=[]
    for cid in report['selected_candidate_ids']:
        c=report['candidates'][cid];seq=c['identity_payload']['activity_instance_sequence']
        summaries.append(dict(task_id=c['task_id'],candidate_id=cid,activities=[s['activity_id'] for s in seq],
            activity_types=[s['activity'] for s in c['chain']['plan']['steps']],artifacts=[s['artifact'] for s in c['chain']['plan']['steps']],
            raw_metrics=c['chain']['metrics'],score=report['evaluation']['candidates'][cid]['score']))
    if [len(s['activities']) for s in summaries]!=[4,5,6]:raise AssertionError('actual multi-stage lengths differ')
    if any(set(s['activity_types'])!={'observe','decide','act'} for s in summaries):raise AssertionError('unexpected activity types')
    owl=report['design_report']['owl_reasoning'];fact=[f for f in owl['facts'] if f[2]=='urn:observatory-semantic-demo:PhaseTune']
    if not fact:raise AssertionError('new tuning capability was not actually entailed')
    from rdflib import Graph,URIRef
    capability_triple=(URIRef('urn:arcane-scene:artifact:O-Phase-Tuner'),
        URIRef('http://example.org/arcaneweb#hasCapability'),URIRef('urn:observatory-semantic-demo:PhaseTune'))
    before=Graph().parse(output/'network/batch-001-reasoning_input.ttl',format='turtle')
    after=Graph().parse(output/'network/batch-001-reasoning_result.ttl',format='turtle')
    if capability_triple in before or capability_triple not in after:raise AssertionError('capability was preasserted or not entailed')
    study=make_simulation(report);save(output/'study-v2.json',study)
    lock=freeze(study,report,output/'study-v2-lock.json');result=assess(study,report,lock)
    save(output/'observations-v2-report.json',result)
    if result['status']!='analyzed':raise AssertionError('v2 base simulation blocked: '+str(result['blockers'])+str(result['integrity_issues']))
    cases=[]
    for name in ('entire_sample_absent','middle_stage_missing','wrong_same_label_source','independent_endpoint_missing','cohort_changed'):
        variant=deepcopy(study);enrollment=next(e for e in study['enrollments'] if e['split']=='evaluation' and e['flow_id']==study['protocol']['flows'][1]['flow_id'])
        eid=enrollment['enrollment_id']
        if name=='entire_sample_absent':
            variant['observations']=[r for r in variant['observations'] if r['enrollment_id']!=eid]
            variant['endpoints']=[r for r in variant['endpoints'] if r['enrollment_id']!=eid]
        elif name=='middle_stage_missing':
            variant['observations']=[r for r in variant['observations'] if not(r['enrollment_id']==eid and r['stage_instance_id']=='s2')]
        elif name=='wrong_same_label_source':
            target=next(r for r in variant['observations'] if r['enrollment_id']==eid and r['stage_instance_id']=='s2')
            source=next(r for r in variant['observations'] if r['enrollment_id']!=eid and r['flow_id']==target['flow_id'] and r['stage_instance_id']=='s1' and r['output_label']==target['input_label'])
            target.update(input_source_observation_id=source['observation_id'],input_reference=source['output_reference'])
        elif name=='independent_endpoint_missing':variant['endpoints']=[r for r in variant['endpoints'] if r['enrollment_id']!=eid]
        else:variant['enrollments']=[r for r in variant['enrollments'] if r['enrollment_id']!=eid]
        got=assess(variant,report,lock);save(output/(name+'.json'),got)
        if got['status']!='blocked' or any(f['statistical_assessment'] for f in got['flows']):raise AssertionError('semantic counterexample not blocked: '+name)
        cases.append(dict(case=name,passed=True,blockers=got['blockers'],issues=got['integrity_issues']))
    # Counterfactual request: remove need for calibration/audit, search chooses a shorter route.
    entry=manifest['jobs'][2]
    catalog=json.loads((EXAMPLE/entry['catalog_file']).read_text(encoding='utf-8'))
    request=json.loads((EXAMPLE/entry['request_file']).read_text(encoding='utf-8'))
    for row in request['static_facts']:
        if row['fact']['predicate'] in ('needs_tune','needs_audit'):row['value']=False
    request['search']['mode']='enumerate_bounded';short=SearchSession(prepare_problem(catalog,request)).run(max_candidates=100)
    if not short['candidates'] or {len(c['sequence']['steps']) for c in short['candidates']}!={4}:raise AssertionError('flow length was fixed rather than goal-driven')
    save(output/'shorter-goal-search.json',short)
    # A physical resource failure must not be bypassed by inventing a shorter chain.
    from task_flow.joint import run_joint
    jobs=[]
    for item in manifest['jobs']:
        jobs.append(dict(task_id=item['task_id'],catalog=json.loads((EXAMPLE/item['catalog_file']).read_text(encoding='utf-8')),
                         request=json.loads((EXAMPLE/item['request_file']).read_text(encoding='utf-8'))))
    unavailable=deepcopy(scene)
    next(a for a in unavailable['artifacts'] if a['id']=='O-Phase-Tuner')['available']=False
    blocked_design=run_joint(jobs,unavailable,json.loads((EXAMPLE/'mapping.json').read_text(encoding='utf-8')))
    if blocked_design['selected_candidate_ids']:raise AssertionError('missing tuning resource was bypassed')
    save(output/'missing-tuner-design.json',blocked_design)
    summary=dict(status='passed',input_predefined_routes=False,flow_search_complete=report['flow_search_complete'],
        flows_generated=report['flow_count'],unique_chains=report['unique_chain_count'],global_optimality=report['global_optimality'],
        network_search=report['network_search'],independent_network_oracle=oracle,selected=summaries,rdf_validation=report['rdf_validation'],
        owl_engine=owl['engine'],tuner_capability_in_input=False,tuner_capability_in_result=True,
        entailed_tuner_capabilities=fact,v2_semantics_cases=cases,
        registered_samples=result['registered_enrollments'],stage_observations=result['observations'],
        independent_endpoint_records=result['independent_endpoints'],domain_validity='pending',
        observations_are_synthetic=True,missing_tuner_status=blocked_design['integration_status'],
        internal_sequence_representation='search results are compiled by existing adapter; no input order template')
    save(output/'verification.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return report,study,result,summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,required=True)
    run(parser.parse_args(argv).output_dir.resolve())


if __name__=='__main__':main()
