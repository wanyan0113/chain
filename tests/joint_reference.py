"""Small-fixture oracle. Does not import production planners, scorers or resources."""
from itertools import permutations,product


def flows(job):
    cat,req=job['catalog'],job['request']
    assert not cat['derived_rules'] and all(f['type']=='boolean' for f in cat['facts'])
    base={(r['fact']['predicate'],tuple(r['fact']['args'])):r['value'] for r in req['static_facts']+req['initial_state']}
    target=next(iter(cat['objects'].values()))[0]
    def cond(tree,state,bound):
        if 'all' in tree:return all(cond(x,state,bound) for x in tree['all'])
        if 'any' in tree:return any(cond(x,state,bound) for x in tree['any'])
        if 'not' in tree:return not cond(tree['not'],state,bound)
        test=tree['test'];ref=test['fact']
        args=tuple(bound[x['param']] if isinstance(x,dict) else x for x in ref['args'])
        value=state.get((ref['predicate'],args))
        return value is not None and (value==test['value'] if test['op']=='eq' else value!=test['value'])
    result=[]
    for n in range(req['search']['max_length']+1):
        for seq in permutations(cat['activities'],n):
            state=dict(base);valid=True
            for action in seq:
                bound={p:target for p in action['parameters']}
                if (not cond(action['applicable_when'],state,bound) or not cond(action['preconditions'],state,bound)
                        or req['activity_limits'].get(action['id'],action['max_occurrences'])<1):
                    valid=False;break
                for effect in action['effects']:
                    assert 'set' in effect and 'value' in effect['set']
                    args=tuple(bound[x['param']] if isinstance(x,dict) else x for x in effect['fact']['args'])
                    state[(effect['fact']['predicate'],args)]=effect['set']['value']
                if any(cond(c,state,{}) for c in req['forbidden_conditions']):valid=False;break
            if valid and cond(req['goal'],state,{}) and set(req['required_activities'])<={a['id'] for a in seq}:
                result.append(tuple(a['id'] for a in seq))
    return result


def chains(job,scene,mapping):
    task=next(t for t in scene['tasks'] if t['id']==job['task_id'])
    actions={a['id']:a for a in job['catalog']['activities']}
    entries={e['activity_id']:e for e in mapping['entries']}
    contexts={(c['task'],c['artifact']):c['attributes'] for c in scene['contexts']}
    links={(e['from'],e['to']):e for e in scene['links']}
    output={}
    for seq in flows(job):
        if len(seq)<3 or entries[seq[0]]['activity']!='observe' or entries[seq[-1]]['activity']!='act':continue
        domains=[]
        for action_id in seq:
            entry=entries[action_id];action=actions[action_id];domain=[]
            for artifact in scene['artifacts']:
                attrs=artifact['attributes'];ctx=contexts[(task['id'],artifact['id'])]
                caps=set(attrs['supported_functions'])
                if not artifact['available'] or not set(action['required_capabilities'])<=caps:continue
                if attrs['level_'+entry['activity']]['value']<entry['min_level']:continue
                if entry['role']!='coordination' and task['attributes']['target_type'] not in attrs['target_types']:continue
                if scene['policies'].get('article_table10',False):
                    d=ctx['target_distance']['value']
                    if entry['role']=='insight' and not attrs['detection_range']['value']>d:continue
                    if entry['role']=='manifestation' and not (attrs['min_range']['value']<d<attrs['max_range']['value']
                        and attrs['resonance']['value']>task['attributes']['impedance']['value']
                        and attrs['max_target_speed']['value']>task['attributes']['target_speed']['value']
                        and attrs['min_target_altitude']['value']<task['attributes']['target_altitude']['value']<attrs['max_target_altitude']['value']
                        and attrs['min_activation_altitude']['value']<ctx['platform_altitude']['value']<attrs['max_activation_altitude']['value']):continue
                domain.append(artifact)
            domains.append(domain)
        for assignment in product(*domains):
            ids=tuple(a['id'] for a in assignment)
            if len(set(ids))!=len(ids) and not mapping['allow_nonadjacent_reuse']:continue
            valid=True
            for i,(a,b) in enumerate(zip(ids,ids[1:]),1):
                relation=entries[seq[i]]['incoming_relation']
                edge=links.get((a,b))
                if edge is None and relation=='communication' and mapping['communication_model']=='undirected':edge=links.get((b,a))
                if not edge or edge['communicates'] is not True or not edge['distance']['value']<edge['max_distance']['value'] \
                    or (relation=='control' and edge['controls'] is not True):valid=False;break
            if not valid:continue
            time,cost,quality=0,0,1.
            steps=[];clock=task['start_s']
            for action_id,a in zip(seq,assignment):
                entry=entries[action_id];metric=a['activity_metrics'][entry['activity']]
                start=clock;clock+=metric['duration_s'];time+=metric['duration_s'];cost+=metric['cost']['value'];quality*=metric['accuracy']
                steps.append(dict(artifact=a['id'],role=entry['role'],start_s=start,end_s=clock))
            output[(seq,ids)]=dict(task=task['id'],metrics=dict(time=time,accuracy=quality,cost=cost),steps=steps)
    return output


def feasible(chosen,scene):
    artifacts={a['id']:a for a in scene['artifacts']};tasks={t['id']:t for t in scene['tasks']}
    usages=[]
    for chain in chosen:
        for step in chain.get('steps',chain.get('plan',{}).get('steps',[])):
            a=artifacts[step['artifact']]
            usages.append((step['artifact'],step['start_s'],step['end_s']+a['cooldown_s'],chain,step))
    for aid,a in artifacts.items():
        rows=[u for u in usages if u[0]==aid]
        if a['consumable'] and len(rows)>a['inventory']:return False
        for t in {u[1] for u in rows}:
            if sum(1 for u in rows if u[1]<=t<u[2])>a['capacity']:return False
        if scene['policies'].get('article_table10',False):
            role=next((u[4]['role'] for u in rows),None)
            if role=='manifestation':
                if len({u[3]['task'] for u in rows})>1:return False
            else:
                for t in {u[4]['start_s'] for u in rows}:
                    active={u[3]['task'] for u in rows if u[4]['start_s']<=t<u[4]['end_s']}
                    if len(active)<2:continue
                    count=sum(tasks[k]['attributes']['target_count']['value'] for k in active)
                    field='max_tracks' if role=='insight' else 'max_processing_targets'
                    if not a['attributes'][field]['value']>count:return False
                    if role=='insight':
                        ctx={(c['task'],c['artifact']):c['attributes'] for c in scene['contexts']}
                        if any(not a['attributes']['detection_range']['value']>ctx[(k,aid)]['target_distance']['value'] for k in active):return False
    for group in scene.get('resource_groups',[]):
        rows=[u for aid in group['artifacts'] for u in usages if u[0]==aid]
        for t in {u[1] for u in rows}:
            if sum(1 for u in rows if u[1]<=t<u[2])>group['capacity']:return False
    start={c['task']:min(s['start_s'] for s in c.get('steps',c.get('plan',{}).get('steps',[]))) for c in chosen}
    end={c['task']:max(s['end_s'] for s in c.get('steps',c.get('plan',{}).get('steps',[]))) for c in chosen}
    if any(start[d['after']]<end[d['before']]+d['gap_s'] for d in scene.get('dependencies',[])):return False
    return True


def audit_joint(report):
    scene=report['input_snapshot']['scene'];mapping=report['input_snapshot']['mapping'];jobs=report['input_snapshot']['jobs']
    scored=[]
    for job in jobs:
        expected=chains(job,scene,mapping)
        actual={}
        for cid,c in report['candidates'].items():
            if c['task_id']!=job['task_id']:continue
            seq=tuple(s['activity_id'] for s in c['identity_payload']['activity_instance_sequence'])
            ids=tuple(s['artifact'] for s in c['chain']['plan']['steps'])
            actual[(seq,ids)]=cid
        assert set(actual)==set(expected),(job['task_id'],set(actual)^set(expected))
        group=[]
        for key,chain in expected.items():
            normalized={}
            for metric in ('time','accuracy','cost'):
                low=min(c['metrics'][metric] for c in expected.values());high=max(c['metrics'][metric] for c in expected.values())
                value=chain['metrics'][metric]
                normalized[metric]=.5 if low==high else ((value-low)/(high-low) if metric=='accuracy' else 1-(value-low)/(high-low))
            score=sum(scene['weights'][k]*v for k,v in normalized.items())
            cid=actual[key]
            assert abs(report['evaluation']['candidates'][cid]['score']-score)<1e-12
            for metric in chain['metrics']:
                assert abs(report['candidates'][cid]['chain']['metrics'][metric]-chain['metrics'][metric])<1e-12
            group.append(dict(chain,id=cid,score=score))
        scored.append(group)
    best=None;feasible_count=0;total=0
    for chosen in product(*scored):
        total+=1
        if not feasible(chosen,scene):continue
        feasible_count+=1
        score=sum(c['score'] for c in chosen)/len(chosen)
        key=(-score,tuple(c['id'] for c in chosen))
        if best is None or key<best:best=key
    assert best is not None
    assert tuple(report['selected_candidate_ids'])==best[1]
    assert abs(report['design_report']['network']['score']+best[0])<1e-12
    return dict(raw_network_combinations=total,feasible_network_combinations=feasible_count,
                best_score=-best[0],selected_candidate_ids=list(best[1]))
