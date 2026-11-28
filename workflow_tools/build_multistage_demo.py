"""Build a goal/precondition-driven 4/5/6-stage observatory fixture, no input routes."""
from copy import deepcopy
import json
from pathlib import Path

from task_flow.contracts import canonical_sha256,prepare_problem
from workflow_tools.benchmark_joint import ROOT,save

DEST=ROOT/'task_flow/examples/observatory_multistage'
CAP='urn:observatory-semantic-demo:'
REF='observatory-multistage-synthetic-v1'


def build(destination=DEST):
    require_new=not destination.exists()
    if not require_new:raise ValueError('scenario directory already exists')
    read=lambda name:json.loads((ROOT/'task_flow/examples/joint_optimization'/name).read_text(encoding='utf-8'))
    base=read('catalog-2.json');scene=read('scene.json');mapping=read('mapping.json')
    mapping.update(mapping_id='observatory-multistage-map',source_refs=[REF])
    for e in mapping['entries']:e['catalog_id']='observatory-stabilization';e['source_refs']=[REF]
    def fact(predicate,param=True):return dict(predicate=predicate,args=[{'param':'rift'}] if param else [])
    def test(predicate,value):return {'test':{'fact':fact(predicate),'op':'eq','value':value}}
    def action(identifier,pre,effect,capability):
        return dict(id=identifier,version='1.0',type='decide',parameters={'rift':'rift'},applicable_when={'all':[]},
            preconditions={'all':pre},effects=[{'fact':fact(effect),'set':{'value':True}}],
            required_capabilities=[capability],max_occurrences=1,source_refs=[REF])
    base.update(catalog_id='observatory-stabilization',source_refs=[REF])
    for name,kind in [('needs_tune','static'),('needs_audit','static'),('tuned','mutable'),('audited','mutable')]:
        base['facts'].append(dict(id=name,arguments=['rift'],kind=kind,type='boolean',source_refs=[REF]))
    base['capabilities'].extend(dict(uri=CAP+name,mapping_status='unmapped',source_refs=[REF]) for name in ('PhaseTune','SafetyAudit'))
    actions={a['id']:a for a in base['activities']}
    actions['decide']['preconditions']['all'].append({'any':[test('needs_tune',False),test('tuned',True)]})
    actions['stabilize']['preconditions']['all'].append({'any':[test('needs_audit',False),test('audited',True)]})
    base['activities'].extend([
        action('phase_tune',[test('needs_tune',True),test('analyzed',True),test('decided',False),test('tuned',False)],'tuned',CAP+'PhaseTune'),
        action('safety_audit',[test('needs_audit',True),test('decided',True),test('stable',False),test('audited',False)],'audited',CAP+'SafetyAudit')])
    # Non-executable decoy: search must reject its unsatisfied precondition.
    decoy=action('premature_release',[test('stable',True),test('stable',False)],'audited',CAP+'SafetyAudit')
    base['activities'].append(decoy)
    for name in ('phase_tune','safety_audit','premature_release'):
        entry=deepcopy(next(e for e in mapping['entries'] if e['activity_id']=='decide'))
        entry['activity_id']=name;mapping['entries'].append(entry)
    targets=['North-Rift','East-Rift','Core-Rift'];destination.mkdir(parents=True)
    jobs=[]
    for i,(target,tune,audit) in enumerate(zip(targets,[False,False,True],[False,True,True]),1):
        catalog=deepcopy(base);catalog['objects']={'rift':[target]}
        request=read(f'request-{i}.json')
        request.update(request_id=f'observatory-{i}',catalog_id=catalog['catalog_id'])
        for collection in ('static_facts','initial_state'):
            for row in request[collection]:row['fact']['args']=[target]
        request['static_facts'][0]['value']=True
        for key,value in [('needs_tune',tune),('needs_audit',audit)]:
            request['static_facts'].append({'fact':{'predicate':key,'args':[target]},'value':value})
        for key in ('tuned','audited'):request['initial_state'].append({'fact':{'predicate':key,'args':[target]},'value':False})
        request['goal']['test']['fact']['args']=[target]
        request['search'].update(max_length=7,max_nodes=50000,timeout_ms=20000)
        request['catalog_sha256']=canonical_sha256(catalog);request['source_refs']=[REF]
        prepare_problem(catalog,request)
        save(destination/f'catalog-{i}.json',catalog);save(destination/f'request-{i}.json',request)
        jobs.append(dict(task_id=f'T{i}',catalog_file=f'catalog-{i}.json',request_file=f'request-{i}.json'))
        scene['tasks'][i-1]['target']=target;scene['tasks'][i-1]['source_refs']=[REF]
    scene['scope']='虚构星穹观测站三裂隙稳定；流程由task_flow按事实/目标搜索，输入没有活动顺序。'
    scene['knowledge']['ontology_files'].append('observatory-multistage.ttl');scene['knowledge']['source_refs']=[REF]
    prototype=deepcopy(next(a for a in scene['artifacts'] if a['id']=='R-Dispatch'))
    for identifier,cls,cap,duration,cost in [('O-Phase-Tuner','TunerUnit','PhaseTune',2,2),('O-Safety-Auditor','AuditorUnit','SafetyAudit',2,1)]:
        artifact=deepcopy(prototype);artifact.update(id=identifier,ontology_types=[CAP+cls],source_refs=[REF])
        artifact['attributes']['supported_functions']=[CAP+cap]
        artifact['activity_metrics']['decide'].update(duration_s=duration,accuracy=.985,cost={'value':cost,'unit':'demo-credit/use'})
        scene['artifacts'].append(artifact)
        for task in scene['tasks']:
            context=deepcopy(next(c for c in scene['contexts'] if c['task']==task['id'] and c['artifact']=='R-Dispatch'))
            context['artifact']=identifier;scene['contexts'].append(context)
    rules=json.loads((ROOT/'knowledge/flow-design-bridge.rules.json').read_text(encoding='utf-8'))
    for cap in ('PhaseTune','SafetyAudit'):
        rule=deepcopy(next(r for r in rules['artifact_rules'] if r['id']=='Dispatch'))
        rule.update(id=cap,capability=CAP+cap,source_refs=[REF])
        for condition in rule['when']:condition['value']=CAP+cap
        scene['artifact_rules'].append(rule)
    # Explicit complete directed physical links; constraints are still checked by the engine.
    link=deepcopy(scene['links'][0]);seals={a['id'] for a in scene['artifacts'] if a['consumable']}
    scene['links']=[]
    for a in scene['artifacts']:
        for b in scene['artifacts']:
            if a['id']==b['id']:continue
            row=deepcopy(link);row.update({'from':a['id'],'to':b['id'],'controls':b['id'] in seals,'source_refs':[REF]})
            scene['links'].append(row)
    scene['policies'].update(max_chain_combinations=10000,max_network_combinations=100000,max_network_search_nodes=100000)
    save(destination/'jobs.json',dict(format_version='task-flow-design-jobs-1.0',jobs=jobs))
    save(destination/'scene.json',scene);save(destination/'mapping.json',mapping)
    print('Scenario files written:',destination)


if __name__=='__main__':build()
