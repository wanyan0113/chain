"""TFDO-DESIGN-1.0: generate -> freeze -> evaluate -> jointly optimize -> verify."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import platform
import sys

from .contracts import prepare_problem, require
from .planner import SearchSession
from .validator import verify_candidate
from .design_bridge import prepare_mapping, compile_tuple
from .joint_identity import (BASELINE, IDENTITY_VERSION, candidate_identity, design_context, evidence,
                             flow_identity, knowledge_context_hash, mapping_context, new_run_id, sha, task_context,
                             require_nfc_source)
from .joint_candidates import CandidateStore, RAW_POLICY, evaluate, metrics_for_chain
from .joint_network import network, optimize
from design.engine import DesignError, generate_candidates, validate_document
from design.knowledge import knowledge_context
from design.capability_identity import annotate_design_mapping
from design.ontology_inference import prepare_artifacts


def run_joint(jobs,scene,mapping,*,max_flows_per_task=100,max_flow_analyses=10000):
    jobs,scene,mapping = deepcopy(jobs),deepcopy(scene),prepare_mapping(mapping)
    require_nfc_source(scene);require_nfc_source(mapping)
    require(mapping['evaluation']['mode']==RAW_POLICY,'joint mode requires explicit sequential_product_v2 policy')
    require(type(max_flows_per_task) is int and 1<=max_flows_per_task<=10000,'invalid flow budget')
    require(type(max_flow_analyses) is int and 1<=max_flow_analyses<=10000,'invalid flow analysis budget')
    validate_document(scene)
    require(scene['policies']['equal_metric_score']==.5,'joint normalization policy requires equal_metric_score=0.5')
    require('knowledge' in scene and not any(k in scene['knowledge'] for k in ('sequences_file','sequences_inline')),
            'joint scene needs base knowledge, without prewritten flow templates')
    require(type(jobs) is list and bool(jobs) and len(jobs)<=50,'expected 1..50 jobs')
    for job in jobs:
        require(type(job) is dict and set(job)=={'task_id','catalog','request'} and type(job['task_id']) is str,'invalid job')
    task_ids = sorted(t['id'] for t in scene['tasks'])
    require(len(set(j['task_id'] for j in jobs))==len(jobs) and {j['task_id'] for j in jobs}==set(task_ids),
            'jobs must cover all tasks exactly once')
    require(all('bridge_task_id' not in t['attributes'] for t in scene['tasks']),'reserved bridge task attribute')
    jobs = sorted(jobs,key=lambda j:task_ids.index(j['task_id']))
    run_id = new_run_id()
    store = CandidateStore(run_id)
    with knowledge_context(scene) as (_,metadata):
        frozen_knowledge = deepcopy(metadata)
    context_hash = design_context(scene)
    knowledge_hash = knowledge_context_hash(metadata)
    mapping_hash = mapping_context(mapping)
    task_hashes = {t['id']:task_context(t,next(j for j in jobs if j['task_id']==t['id'])) for t in scene['tasks']}
    result = dict(format_version='tfdo-joint-report-1.0',design_baseline_id=BASELINE,identity_version=IDENTITY_VERSION,
        run_id=run_id,selection_mode='joint_candidate_optimization',integration_status='undetermined',
        runtime=dict(started_at_utc=datetime.now(timezone.utc).isoformat(),python=sys.version,
                     platform=platform.platform(),algorithm_version='tfdo-joint-1.0'),
        global_optimality='not_proven',network_optimality='not_run',resource_feasibility='not_run',
        execution_confirmed=False,domain_validity='pending',raw_metric_policy=RAW_POLICY,
        input_snapshot=dict(jobs=jobs,scene=scene,mapping=mapping),
        task_context_hashes=task_hashes,design_context_hash=context_hash,knowledge_snapshot_hash=knowledge_hash,
        mapping_hash=mapping_hash,base_knowledge_snapshot=frozen_knowledge,
        planning=[],flows={},compilation=[],generation_batches=[],candidates={},candidate_evidence=[],
        limits=dict(max_flows_per_task=max_flows_per_task,max_flow_analyses=max_flow_analyses),
        termination_reasons=[],selected_candidate_ids=[],selected_candidate_evidence_ids=[])
    # B: enumerate bounded activity sequences; normalize identities independently
    # of request search mode, budgets and generated display IDs.
    accepted, template_origins = [],{}
    analyses = 0
    chain_complete = True
    missing_metrics = False
    unknown_requirements = False
    for job in jobs:
        request = deepcopy(job['request'])
        request['search']['mode']='enumerate_bounded'
        problem = prepare_problem(job['catalog'],request)
        search = SearchSession(problem).run(max_candidates=max_flows_per_task)
        result['planning'].append(dict(task_id=job['task_id'],search=search))
        if search['search_status']!='exhausted':
            result['termination_reasons'].append(dict(stage='flow_search',task_id=job['task_id'],reason=search['termination_reason']))
        effective_job = dict(task_id=job['task_id'],catalog=problem.catalog,request=problem.request)
        for candidate in search['candidates']:
            if analyses>=max_flow_analyses:
                chain_complete=False
                result['termination_reasons'].append(dict(stage='flow_analysis',task_id=job['task_id'],reason='flow_analysis_limit'))
                break
            analyses+=1
            verify = verify_candidate(problem.catalog,problem.request,candidate)
            require(verify['verification_status']=='passed','generated flow failed independent replay')
            flow_id,payload = flow_identity(effective_job,candidate,mapping)
            flow_key = sha(dict(task_id=job['task_id'],flow_id=flow_id))
            # Preserve source candidate and effective request for independent replay.
            flow = dict(flow_id=flow_id,identity_payload=payload,task_id=job['task_id'],candidate=candidate,
                        verification=verify,job=effective_job)
            result['flows'][flow_key]=flow
            compiled,trace = compile_tuple([effective_job],[candidate],scene,mapping)
            result['compilation'].append(dict(flow_key=flow_key,**trace))
            if compiled is None:
                chain_complete=False
                continue
            template=compiled['knowledge']['sequences_inline']['templates'][0]
            template['id']='flow-'+flow_key
            accepted.append(template)
            template_origins[template['id']]=flow_key
    result['flow_search_complete']=all(p['search']['search_status']=='exhausted' for p in result['planning'])
    unresolved_logic=any(p['search']['unknown_conditions_seen'] for p in result['planning'])
    result['flow_count']=sum(len(p['search']['candidates']) for p in result['planning'])
    # C: bounded batches respect the legacy 50-template knowledge contract.
    # No scoring or network optimization occurs during these calls.
    merged = None
    for start in range(0,len(accepted),50):
        document=deepcopy(scene)
        document['tasks']=sorted(document['tasks'],key=lambda t:t['id'])
        for task in document['tasks']:
            task['attributes']['bridge_task_id']=task['id']
        document['knowledge']['sequences_inline']=dict(templates=accepted[start:start+50],
            communication_model=mapping['communication_model'],evaluation=mapping['evaluation'],source_refs=mapping['source_refs'])
        with knowledge_context(scene) as (_,current):
            require(current==frozen_knowledge,'context_changed: knowledge changed during candidate generation')
        try:
            generated=generate_candidates(document,allow_partial=True)
        except DesignError as exc:
            chain_complete=False
            result['generation_batches'].append(dict(status='generation_error',reason=str(exc),design_input=document))
            result['termination_reasons'].append(dict(stage='chain_generation',reason=str(exc)))
            continue
        with knowledge_context(scene) as (_,current):
            require(current==frozen_knowledge,'context_changed: knowledge changed during candidate generation')
        result['generation_batches'].append(dict(status='generated',design_input=document,report=generated))
        if merged is None:
            merged=deepcopy(generated)
            for t in merged['tasks']:
                t['chains']=[]
        else:
            for key in ('types','subjects'):
                merged['owl_reasoning'][key].update(generated['owl_reasoning'][key])
            merged['owl_reasoning']['facts']=[list(f) for f in sorted({tuple(f) for f in
                merged['owl_reasoning']['facts']+generated['owl_reasoning']['facts']})]
            for a,b in zip(merged['tasks'],generated['tasks']):
                for role,caps in b['required_capabilities'].items():
                    a['required_capabilities'][role]=sorted(set(a['required_capabilities'][role])|set(caps))
        for task in generated['tasks']:
            if task['blockers'] or task['sequence_generation'].get('coverage')!='complete':
                chain_complete=False
                result['termination_reasons'].append(dict(stage='chain_generation',task_id=task['task'],
                    reason=task['sequence_generation'].get('termination_reason'),blockers=task['blockers']))
            if any(b['code'] in ('UNKNOWN_OWL_FACT','TASK_ISSUES','UNKNOWN_TASK_RULE','NO_CAPABILITY') for b in task['blockers']):
                # A lower bound on task requirements is not a certified complete
                # requirement. It cannot be turned into a selectable subset.
                unknown_requirements=True
                continue
            for blocker in task['blockers']:
                if blocker['code']=='MISSING_ACTIVITY_METRICS':
                    missing_metrics=True
            for chain in task['chains']:
                # Internal output should never already contain a final score.
                require(chain['score'] is None and 'normalized' not in chain,'candidate generated with premature ranking')
                flow_key=template_origins[chain['plan']['template']]
                flow=result['flows'][flow_key]
                try:
                    expected_metrics=metrics_for_chain(chain,scene)
                except ValueError:
                    missing_metrics=True
                    continue
                require(chain['metrics']==expected_metrics,'generator raw metrics differ from declared policy')
                cid,payload=candidate_identity(task['task'],task_hashes[task['task']],flow['flow_id'],
                    flow['candidate']['sequence']['steps'],chain,context_hash,knowledge_hash,mapping_hash)
                store.add(cid,payload,chain,dict(flow_key=flow_key,flow_id=flow['flow_id'],
                          batch_index=len(result['generation_batches'])-1,source_chain_id=chain['id']))
    result['chain_generation_complete']=chain_complete
    coverage=dict(flow_search_complete=result['flow_search_complete'],chain_generation_complete=chain_complete,
                  unknown_logic=unresolved_logic,scope='declared_finite_catalogs_and_request_length_bounds')
    # D: freeze all per-task pools before any extrema or score is computed.
    frozen=store.freeze(task_ids,coverage)
    result.update(frozen_candidate_sets=frozen,candidate_set_hash=frozen['candidate_set_hash'],
        candidates=store.candidates,candidate_evidence=store.evidence_records,
        generated_chain_count=store.generated_count,unique_chain_count=len(store.candidates),
        evidence_count=len(store.evidence_records))
    complete=result['flow_search_complete'] and chain_complete and not unresolved_logic
    if missing_metrics or unknown_requirements:
        result.update(integration_status='blocked',termination_reason='missing_raw_metrics' if missing_metrics else 'unknown_task_requirements')
        return result
    if any(not s['candidate_ids'] for s in frozen['sets']):
        result.update(integration_status='no_feasible_design' if complete else 'undetermined',
                      global_optimality='bounded_global_infeasible' if complete else 'not_proven',
                      termination_reason='empty_task_candidate_set')
        return result
    # E: immutable scores in a separate evaluation context.
    evaluation=evaluate(frozen,store.candidates,scene['weights'])
    result['evaluation']=evaluation
    groups=[]
    for pool in frozen['sets']:
        group=[]
        for cid in pool['candidate_ids']:
            chain=deepcopy(store.candidates[cid]['chain'])
            chain.update(evaluation['candidates'][cid])
            group.append(chain)
        groups.append(group)
    # F: reuse actual derived roles/relations from OWL, not raw scene labels.
    with knowledge_context(scene) as (effective,current):
        require(current==frozen_knowledge,'context_changed: knowledge changed before optimization')
        effective,inference=prepare_artifacts(effective,owl_result=merged['owl_reasoning'])
        best,stats=optimize(groups,effective,mapping)
        result['network_search']=stats
        result['network_optimality']=stats['optimality']
        result['visited_network_count']=stats.get('evaluated',0)
        if best is None:
            result.update(integration_status='no_feasible_design' if stats['optimality']=='proven_infeasible' else 'undetermined',
                global_optimality=('bounded_global_infeasible' if complete else 'candidate_set_infeasible')
                    if stats['optimality']=='proven_infeasible' else 'not_proven',termination_reason=stats['termination_reason'])
            return result
        _,chosen,score,article=best
        result['selected_candidate_ids']=[c['id'] for c in chosen]
        result['selected_candidate_evidence_ids']=[e for cid in result['selected_candidate_ids']
                                                  for e in store.candidates[cid]['candidate_evidence_ids']]
        result['resource_feasibility']='passed'
        result['global_optimality']=('bounded_global_optimum' if complete else 'candidate_set_optimum') \
            if stats['optimality']=='proven' else 'not_proven'
        result['integration_status']='selected' if stats['optimality']=='proven' else 'feasible'
        result['termination_reason']=stats['termination_reason']
        design_report=merged
        design_report.update(status='complete' if stats['optimality']=='proven' else 'feasible',
            network=network(chosen,score,effective),network_search=stats,ontology_inference=inference,
            input_sha256=sha(scene),execution_sha256=sha(dict(candidate_set=frozen['candidate_set_hash'],
                evaluation=evaluation['evaluation_context_hash'],scene=context_hash)))
        for task,group in zip(design_report['tasks'],groups):
            task['chains']=sorted(group,key=lambda c:(-c['score'],c['id']))
        if effective['policies'].get('article_table10',False):
            design_report['article_table10'].update(status='pass',selected=article)
        result['design_report']=annotate_design_mapping(design_report)
    # G: independent arithmetic/timing/resource replay before returning a design.
    from .joint_verify import verify_joint
    result['verification']=verify_joint(result)
    require(result['verification']['status']=='passed','joint independent verification failed')
    return result


def export_joint(report,output_dir,*,protected=()):
    """Persist snapshots and RDF only after independent verification."""
    from source_data.common import write_outputs
    from .joint_verify import verify_joint
    from design.rdf import _design_graph, DESIGN
    from design.vocabulary import KW
    from check_ontology import analyze_graph
    from rdflib import Literal
    outputs={}
    payload=lambda x:json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    snapshot=report['input_snapshot']
    outputs.update({'input_scene.json':payload(snapshot['scene']),'input_mapping.json':payload(snapshot['mapping']),
                    'base_knowledge_snapshot.json':payload(report['base_knowledge_snapshot'])})
    manifest=dict(format_version='task-flow-design-jobs-1.0',jobs=[])
    for index,job in enumerate(snapshot['jobs'],1):
        catalog_file=f'catalog-{index:03}.json';request_file=f'request-{index:03}.json'
        outputs[catalog_file]=payload(job['catalog']);outputs[request_file]=payload(job['request'])
        manifest['jobs'].append(dict(task_id=job['task_id'],catalog_file=catalog_file,request_file=request_file))
    outputs['input_jobs.json']=payload(manifest)
    # Retain exact OWL input/output for every actual generation batch. These
    # files are evidence, not alternative per-batch scoring baselines.
    from design.knowledge import knowledge_outputs
    for index,batch in enumerate(report['generation_batches'],1):
        if batch['status']=='generated':
            for name,content in knowledge_outputs(batch['design_input'],batch['report']).items():
                outputs[f'batch-{index:03}-{name}']=content
    if report.get('selected_candidate_ids'):
        verification=verify_joint(report)
        require(verification['status']=='passed','cannot export unverifiable joint report')
        scene=report['input_snapshot']['scene']
        with knowledge_context(scene) as (_,current):
            require(current==report['base_knowledge_snapshot'],'context_changed: knowledge changed before export')
            from design.capability_identity import load_capability_ontology
            graph=_design_graph(scene,report['design_report'])
            for cid in report['selected_candidate_ids']:
                for node in list(graph.subjects(KW.chainId,Literal(cid))):
                    graph.add((node,DESIGN.flowId,Literal(report['candidates'][cid]['flow_id'])))
                    graph.add((node,DESIGN.runId,Literal(report['run_id'])))
                    for eid in report['candidates'][cid]['candidate_evidence_ids']:
                        graph.add((node,DESIGN.candidateEvidenceId,Literal(eid)))
            review=analyze_graph(load_capability_ontology()+graph)
        require(review.counts()['ERROR']==review.counts()['WARN']==0,'joint RDF failed static validation')
        report=deepcopy(report)
        report['rdf_validation']=review.counts()
        name='design.ttl' if report['network_optimality']=='proven' else 'incumbent.ttl'
        outputs[name]=graph.serialize(format='turtle')
        summary=['# 跨流程联合网络设计','',
            '结论：'+report['global_optimality'],'资源核验：passed',
            '流程搜索完整：'+str(report['flow_search_complete']),
            '候选链生成完整：'+str(report['chain_generation_complete']),
            '综合精度业务有效性：pending','',
            '| 任务 | 活动顺序 | 法器顺序 | 时间/秒 | 精度乘积 | 成本 | 统一评分 |',
            '| --- | --- | --- | ---: | ---: | ---: | ---: |']
        for cid in report['selected_candidate_ids']:
            c=report['candidates'][cid];chain=c['chain'];metric=chain['metrics']
            summary.append('| '+' | '.join([c['task_id'],
                ' → '.join(s['activity_id'] for s in c['identity_payload']['activity_instance_sequence']),
                ' → '.join(s['artifact'] for s in chain['plan']['steps']),str(metric['time']),
                str(metric['accuracy']),str(metric['cost']),str(report['evaluation']['candidates'][cid]['score'])])+' |')
        summary.extend(['', '网络平均评分：'+str(report['design_report']['network']['score']),
            '网络跨度/秒：'+str(report['design_report']['network']['makespan_s']),
            '总成本：'+str(report['design_report']['network']['cost_per_execution']),
            '候选与证据身份详见joint_report.json；不代表真实执行或无限范围最优。',''])
        outputs['design_summary.md']='\n'.join(summary)
    outputs.update({'joint_report.json':payload(report),'candidate_sets.json':payload(report['frozen_candidate_sets']),
                    'candidate_evidence.json':payload(report['candidate_evidence'])})
    write_outputs(output_dir,outputs,protected=protected)
