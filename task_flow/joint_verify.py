"""Independent replay, arithmetic and interval-resource audit for joint reports.

This audits declared model consistency, not the empirical validity of accuracy.
It never uses the reported passed flag, rank_chains or resource_conflicts.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import math

from .contracts import require
from .validator import verify_candidate
from .joint_identity import (candidate_identity, canonical, design_context, evidence, flow_identity,
                             knowledge_context_hash, mapping_context, sha, task_context)
from .joint_candidates import RAW_POLICY, NORMALIZATION_POLICY, NETWORK_POLICY


def _value(left,right,op):
    if left is None or right is None:
        return False
    if op=='contains':
        return type(left) is list and right in left
    if isinstance(left,dict) or isinstance(right,dict):
        if not isinstance(left,dict) or not isinstance(right,dict) or left.get('unit')!=right.get('unit'):
            return False
        left,right=left['value'],right['value']
    if op=='eq':
        return type(left) is type(right) and left==right
    if type(left) not in (int,float) or type(right) not in (int,float):
        return False
    return {'lt':left<right,'le':left<=right,'gt':left>right,'ge':left>=right}[op]


def _rules(cap,role,scopes,rules):
    def lookup(path):
        name,key=path.split('.',1)
        return scopes[name].get(key)
    for rule in rules:
        if rule['role']!=role or rule['capability']!=cap or rule['blocking_issue_ids']:
            continue
        if all(_value(lookup(c['field']),lookup(c['ref']) if 'ref' in c else c['value'],c['op']) for c in rule['when']):
            return True
    return False


def _resources(uses,artifacts,groups):
    for identifier,a in artifacts.items():
        rows=[u for u in uses if u['artifact']==identifier]
        for time in {u['start_s'] for u in rows}:
            require(sum(u['capacity'] for u in rows if u['start_s']<=time<u['end_s'])<=a['capacity'],'capacity conflict')
        require(not a['consumable'] or sum(u['inventory'] for u in rows)<=a['inventory'],'stock conflict')
    for group in groups:
        rows=[u for a in group['artifacts'] for u in uses if u['artifact']==a]
        for time in {u['start_s'] for u in rows}:
            require(sum(u['capacity'] for u in rows if u['start_s']<=time<u['end_s'])<=group['capacity'],'group capacity conflict')


def _verify(report):
    scene=report['input_snapshot']['scene']
    mapping=report['input_snapshot']['mapping']
    jobs={j['task_id']:j for j in report['input_snapshot']['jobs']}
    tasks={t['id']:t for t in sorted(scene['tasks'],key=lambda t:t['id'])}
    require(report['design_context_hash']==design_context(scene),'design context hash mismatch')
    require(report['mapping_hash']==mapping_context(mapping),'mapping hash mismatch')
    require(report['knowledge_snapshot_hash']==knowledge_context_hash(report['base_knowledge_snapshot']),'knowledge hash mismatch')
    for f in report['base_knowledge_snapshot']['files']:
        encoding='utf-8-sig' if f['encoding']=='utf-8-sig' else 'utf-8'
        require(hashlib.sha256(f['content'].encode(encoding)).hexdigest()==f['sha256'],'knowledge content mismatch')
    for task in tasks:
        require(report['task_context_hashes'][task]==task_context(tasks[task],jobs[task]),'task context hash mismatch')
    for key,flow in report['flows'].items():
        check=verify_candidate(flow['job']['catalog'],flow['job']['request'],flow['candidate'])
        require(check['verification_status']=='passed','flow replay failed')
        fid,payload=flow_identity(flow['job'],flow['candidate'],mapping)
        require(fid==flow['flow_id'] and payload==flow['identity_payload'],'flow semantic identity mismatch')
        require(key==sha(dict(task_id=flow['task_id'],flow_id=fid)),'flow lookup key mismatch')
    # Re-certify claims of complete logical coverage; replay alone cannot prove
    # that no valid sequence was omitted. Identity comparison ignores elapsed time.
    from .contracts import prepare_problem
    from .planner import SearchSession
    for p in report['planning']:
        original=p['search']
        if original['search_status']!='exhausted':
            continue
        snapshot=original['input_snapshot']
        rerun=SearchSession(prepare_problem(snapshot['catalog'],snapshot['request'])).run(
            max_candidates=original['limits']['max_candidates'])
        require(rerun['search_status']=='exhausted' and
                sorted(canonical(c['sequence']) for c in rerun['candidates'])==
                sorted(canonical(c['sequence']) for c in original['candidates']), 'logical coverage recertification failed')
    require(report['evidence_count']==len(report['candidate_evidence']),'evidence count mismatch')
    evidence_by_id={}
    for ordinal,rec in enumerate(report['candidate_evidence']):
        expected=evidence(report['run_id'],rec['candidate_id'],rec['kind'],ordinal,rec['content'])
        require(expected==rec,'candidate evidence mismatch')
        evidence_by_id[rec['candidate_evidence_id']]=rec
    source_occurrences={(i,c['id']) for i,b in enumerate(report['generation_batches']) if b['status']=='generated'
                        for t in b['report']['tasks'] for c in t['chains']}
    evidence_occurrences={(e['content']['origin']['batch_index'],e['content']['origin']['source_chain_id'])
                          for e in report['candidate_evidence']}
    require(source_occurrences==evidence_occurrences,'generated candidate occurrence was dropped')
    candidates=report['candidates']
    require(report['unique_chain_count']==len(candidates),'candidate count mismatch')
    require(report['generated_chain_count']==len(report['candidate_evidence']),'occurrence count mismatch')
    # Reconstruct real derived role/relation scopes from retained actual OWL facts.
    from design.knowledge import knowledge_context
    from design.ontology_inference import prepare_artifacts
    with knowledge_context(scene) as (effective,_):
        effective,inference=prepare_artifacts(effective,owl_result=report['design_report']['owl_reasoning'])
    artifacts={a['id']:a for a in effective['artifacts']}
    contexts={(c['task'],c['artifact']):c for c in effective.get('contexts',[])}
    links={(e['from'],e['to']):e for e in effective['links']}
    type_by_id={a['artifact']:a['inferred_types'] for a in inference['artifacts']}
    caps_by_id=inference['relations']['capabilities']
    mapping_entries={(e['catalog_id'],e['activity_id'],e['activity_version']):e for e in mapping['entries']}
    raw={}
    for cid,candidate in candidates.items():
        chain=candidate['chain']
        require(chain['id']==cid and chain['score'] is None and 'normalized' not in chain,'raw candidate was prematurely scored')
        require(candidate['candidate_evidence_ids'],'candidate has no evidence')
        recs=[evidence_by_id[e] for e in candidate['candidate_evidence_ids']]
        require(all(e['candidate_id']==cid for e in recs),'evidence bound to another candidate')
        origin=recs[0]['content']['origin']
        flow=report['flows'][origin['flow_key']]
        require(candidate['task_id']==flow['task_id'] and chain['task']==flow['task_id'],'candidate task mismatch')
        task=tasks[flow['task_id']]
        seq=flow['candidate']['sequence']['steps']
        expected_id,payload=candidate_identity(task['id'],report['task_context_hashes'][task['id']],flow['flow_id'],seq,
            chain,report['design_context_hash'],report['knowledge_snapshot_hash'],report['mapping_hash'])
        require(cid==expected_id and canonical(payload)==canonical(candidate['identity_payload']),'candidate identity mismatch')
        for rec in recs:
            content=rec['content']
            batch=report['generation_batches'][content['origin']['batch_index']]
            source=next(c for t in batch['report']['tasks'] for c in t['chains']
                        if c['id']==content['origin']['source_chain_id'])
            require(source==content['chain'],'generation evidence not present in source batch')
            clean=deepcopy(source)
            clean['id']=cid
            require(clean==chain,'candidate content differs from source occurrence')
        require(len(seq)==len(chain['plan']['steps']),'lost activity instance')
        durations,qualities,costs=[],[],[]
        reservations=[]
        members={r:[] for r in ('insight','coordination','manifestation')}
        clock=task['start_s']
        require(chain['start_s']==clock and not task['blocking_issue_ids'],'invalid task start/issues')
        for index,(instance,step) in enumerate(zip(seq,chain['plan']['steps'])):
            entry=mapping_entries[(flow['job']['catalog']['catalog_id'],instance['activity_id'],instance['activity_version'])]
            action=next(a for a in flow['job']['catalog']['activities'] if a['id']==instance['activity_id'])
            a=artifacts[step['artifact']]
            require(instance['parameters']=={entry['target_parameter']:task['target']},'wrong target binding')
            require(step['activity']==entry['activity'] and step['role']==entry['role']==a['role'],'activity role mismatch')
            require(set(action['required_capabilities'])<=set(step['required_capabilities']),'lost activity capability')
            require(a['available'] and not a['blocking_issue_ids'],'unavailable artifact')
            level=a['attributes'].get('level_'+step['activity'])
            require(level and level['unit']=='1' and 0<=level['value']<=1 and level['value']>=entry['min_level'], 'level mismatch')
            require(step['min_level']==entry['min_level'],'tampered level threshold')
            context=contexts.get((task['id'],a['id']),{})
            require(not context.get('blocking_issue_ids',[]),'context has blocking issues')
            if a['role']!='coordination':
                require(task['attributes']['target_type'] in a['attributes']['target_types'],'target type mismatch')
            task_facts=deepcopy(task['attributes'])
            task_facts['bridge_task_id']=task['id']
            from design.owl_scene import scene_uri
            task_facts['ontology_types']=report['design_report']['owl_reasoning']['types'][str(scene_uri('task',task['id']))]
            facts={**a['attributes'],'ontology_types':type_by_id[a['id']],'ontology_capabilities':caps_by_id[a['id']]}
            scopes=dict(task=task_facts,artifact=facts,context=context.get('attributes',{}))
            for cap in step['required_capabilities']:
                require(cap in caps_by_id[a['id']] and _rules(cap,a['role'],scopes,effective['artifact_rules']),
                        'capability/rule mismatch')
            if effective['policies'].get('article_table10',False) and a['role']!='coordination':
                if a['role']=='insight':
                    conditions=[('detection_range','target_distance','context','gt')]
                else:
                    conditions=[('max_range','target_distance','context','gt'),('min_range','target_distance','context','lt'),
                        ('resonance','impedance','task','gt'),('max_target_speed','target_speed','task','gt'),
                        ('min_target_altitude','target_altitude','task','lt'),('max_target_altitude','target_altitude','task','gt'),
                        ('min_activation_altitude','platform_altitude','context','lt'),('max_activation_altitude','platform_altitude','context','gt')]
                require(all(_value(facts.get(left),scopes[where].get(right),op) for left,right,where,op in conditions),'table10 screening mismatch')
            metric=a['activity_metrics'].get(step['activity'])
            require(metric is not None and metric['cost']['unit']==scene['policies']['cost_unit'],'missing/wrong-unit metric')
            require(step['start_s']==clock and metric['duration_s']>0 and 0<=metric['accuracy']<=1,'activity time/quality mismatch')
            clock+=metric['duration_s']
            require(step['end_s']==clock,'step end mismatch')
            durations.append(metric['duration_s']); qualities.append(metric['accuracy']); costs.append(metric['cost']['value'])
            reservations.append(dict(artifact=a['id'],capacity=1,inventory=int(a['consumable']),start_s=step['start_s'],
                                     end_s=clock+a['cooldown_s']))
            if a['id'] not in members[a['role']]:
                members[a['role']].append(a['id'])
            if index:
                previous=chain['plan']['steps'][index-1]['artifact']
                require(a['id']!=previous,'adjacent resource reuse')
                if not mapping['allow_nonadjacent_reuse']:
                    require(a['id'] not in [s['artifact'] for s in chain['plan']['steps'][:index]],'disallowed reuse')
                edge=links.get((previous,a['id']))
                relation=entry['incoming_relation']
                if edge is None and relation=='communication' and mapping['communication_model']=='undirected':
                    edge=links.get((a['id'],previous))
                require(edge and edge['communicates'] is True and not edge['blocking_issue_ids'] and
                        _value(edge['distance'],edge['max_distance'],'lt'),'connection mismatch')
                require(relation!='control' or edge['controls'] is True,'control missing')
                require(chain['plan']['connections'][index-1]=={'from':previous,'to':a['id'],'relation':relation},'connection record mismatch')
        require(chain['plan']['members']==members and chain['plan']['resource_usage']==reservations,'resource usage mismatch')
        _resources(reservations,artifacts,scene.get('resource_groups',[]))
        mission_node=str(scene_uri('task',task['id']))
        mission_caps={o for s,p,o in report['design_report']['owl_reasoning']['facts']
                      if s==mission_node and p=='http://example.org/arcaneweb#requiresCapability'}
        require(mission_caps<={cap for step in chain['plan']['steps'] for cap in step['required_capabilities']},
                'chain does not cover whole-task capability requirements')
        require(chain['artifacts']=={s['id']:s['artifact'] for s in chain['plan']['steps']},'assignment map mismatch')
        calculated=dict(time=sum(durations),accuracy=math.prod(qualities),cost=sum(costs),cost_unit=scene['policies']['cost_unit'])
        require(chain['metrics']==calculated and chain['end_s']==clock,'raw metric mismatch')
        require(all(math.isfinite(calculated[k]) for k in ('time','accuracy','cost')),'nonfinite metric')
        raw[cid]=calculated
    snapshot=report['frozen_candidate_sets']
    require(snapshot['raw_metric_policy']==RAW_POLICY,'raw policy mismatch')
    require(snapshot['candidate_set_hash']==sha([{'task_id':s['task_id'],'hash':s['candidate_set_hash']} for s in snapshot['sets']]),'pool hash mismatch')
    require(report['candidate_set_hash']==snapshot['candidate_set_hash'],'pool binding mismatch')
    require([s['task_id'] for s in snapshot['sets']]==list(tasks),'task pool coverage mismatch')
    scores,bounds_by_task={},{}
    for pool in snapshot['sets']:
        ids=sorted(cid for cid,c in candidates.items() if c['task_id']==pool['task_id'])
        require(ids==pool['candidate_ids'] and bool(ids),'candidate pool membership mismatch')
        payload=dict(task_id=pool['task_id'],candidate_ids=ids,raw_metric_policy=RAW_POLICY,raw_metrics=[raw[c] for c in ids])
        require(pool['candidate_set_hash']==sha(payload),'raw pool content mismatch')
        bounds={k:dict(min=min(raw[c][k] for c in ids),max=max(raw[c][k] for c in ids),
                       degenerate=min(raw[c][k] for c in ids)==max(raw[c][k] for c in ids))
                for k in ('time','accuracy','cost')}
        bounds_by_task[pool['task_id']]=bounds
        for cid in ids:
            normalized={}
            for k,b in bounds.items():
                v=.5 if b['degenerate'] else (raw[cid][k]-b['min'])/(b['max']-b['min'])
                if not b['degenerate'] and k!='accuracy': v=1-v
                normalized[k]=v
            scores[cid]=dict(normalized=normalized,score=sum(scene['weights'][k]*v for k,v in normalized.items()))
    ev=report['evaluation']
    require(ev['normalization_extrema']==bounds_by_task and ev['candidates']==scores,'uniform evaluation mismatch')
    expected_context=dict(raw_metric_policy=RAW_POLICY,normalization_policy=NORMALIZATION_POLICY,equal_metric_score=.5,
        arithmetic='finite_float64_exact_equality',candidate_set_hash=snapshot['candidate_set_hash'],
        normalization_scope='per_task_union_of_locally_feasible_cross_flow_chains',normalization_extrema=bounds_by_task,
        chain_weights=scene['weights'],network_objective_policy=NETWORK_POLICY)
    require(ev['evaluation_context_hash']==sha(expected_context),'evaluation context mismatch')
    for key,value in expected_context.items(): require(ev[key]==value,'evaluation policy mismatch')
    require(report['domain_validity']=='pending' and ev['model_validation']['domain_validity']=='pending','unverified domain validity claim')
    selected=report['selected_candidate_ids']
    require(len(selected)==len(tasks) and len(set(selected))==len(selected),'selected network task count')
    chosen=[candidates[c]['chain'] for c in selected]
    require([c['task'] for c in chosen]==list(tasks),'wrong selected task order')
    # Independent interval sweep: direct occupancy at each boundary, not the
    # production event-delta resource checker.
    uses=[u for c in chosen for u in c['plan']['resource_usage']]
    _resources(uses,artifacts,scene.get('resource_groups',[]))
    by_task={c['task']:c for c in chosen}
    for d in scene.get('dependencies',[]):
        require(by_task[d['after']]['start_s']>=by_task[d['before']]['end_s']+d['gap_s'],'selected dependency conflict')
    if scene['policies'].get('article_table10',False):
        all_steps=[(c,s) for c in chosen for s in c['plan']['steps']]
        for identifier,a in artifacts.items():
            rows=[(c,s) for c,s in all_steps if s['artifact']==identifier]
            if a['role']=='manifestation':
                require(len({c['id'] for c,s in rows})<=1,'selected manifestation exclusivity')
                continue
            for time in {s['start_s'] for c,s in rows}:
                active={c['task']:c for c,s in rows if s['start_s']<=time<s['end_s']}
                if len(active)<2: continue
                count=sum(tasks[t]['attributes']['target_count']['value'] for t in active)
                field='max_tracks' if a['role']=='insight' else 'max_processing_targets'
                require(a['attributes'][field]['unit']=='1' and a['attributes'][field]['value']>count,'selected shared target capacity')
                if a['role']=='insight':
                    require(all(_value(a['attributes']['detection_range'],contexts[(t,identifier)]['attributes']['target_distance'],'gt')
                                for t in active),'selected shared range')
    net=report['design_report']['network']
    require(net['chains']==selected and net['score']==sum(scores[c]['score'] for c in selected)/len(selected),'network score mismatch')
    require(net['cost_per_execution']==sum(raw[c]['cost'] for c in selected) and
            net['makespan_s']==max(c['end_s'] for c in chosen)-min(c['start_s'] for c in chosen),'network totals mismatch')
    require(report['resource_feasibility']=='passed' and report['execution_confirmed'] is False,'wrong execution/feasibility claim')
    for task in report['design_report']['tasks']:
        for chain in task['chains']:
            require(chain['metrics']==raw[chain['id']] and chain['score']==scores[chain['id']]['score'] and
                    chain['normalized']==scores[chain['id']]['normalized'],'export report chain differs from frozen evaluation')
    flow_complete=all(p['search']['search_status']=='exhausted' for p in report['planning'])
    chain_complete=(len(report['compilation'])==report['flow_count'] and
        all(c['status']=='compiled' for c in report['compilation']) and
        all(b['status']=='generated' and all(not t['blockers'] and t['sequence_generation'].get('coverage')=='complete'
            for t in b['report']['tasks']) for b in report['generation_batches']))
    require(report['flow_search_complete']==flow_complete and report['chain_generation_complete']==chain_complete,'coverage flag mismatch')
    complete=flow_complete and chain_complete and not any(p['search']['unknown_conditions_seen'] for p in report['planning'])
    search=report['network_search']
    require(report['network_optimality']==search['optimality'],'network proof binding mismatch')
    if search['optimality']=='proven':
        require(search['search_complete'],'interrupted search falsely claimed optimal')
        expected='bounded_global_optimum' if complete else 'candidate_set_optimum'
    else:
        expected='not_proven'
    require(report['global_optimality']==expected,'global proof scope mismatch')
    require(snapshot['coverage']['flow_search_complete']==flow_complete and
            snapshot['coverage']['chain_generation_complete']==chain_complete,'frozen coverage mismatch')
    # Re-certify optimizer claims from the frozen pool rather than trusting a
    # mutable optimality flag. The test oracle separately enumerates combinations
    # with an independent interval checker to validate the optimizer itself.
    from .joint_network import optimize
    groups=[]
    for pool in snapshot['sets']:
        groups.append([{**deepcopy(candidates[c]['chain']),**scores[c]} for c in pool['candidate_ids']])
    best,proof=optimize(groups,effective,mapping)
    require(best is not None and [c['id'] for c in best[1]]==selected and
            proof['optimality']==search['optimality'] and proof['search_complete']==search['search_complete'],
            'network optimality recertification failed')
    return dict(status='passed',scope='independent_flow_arithmetic_connections_and_interval_resources',
                proof_audit='logical_and_network_search_recertification',
                candidates_checked=len(candidates),selected_chains=len(selected),domain_validity='pending')


def verify_joint(report):
    try:
        return _verify(report)
    except (ValueError,KeyError,TypeError,IndexError,StopIteration,OverflowError) as exc:
        return dict(status='failed',reason=str(exc))
