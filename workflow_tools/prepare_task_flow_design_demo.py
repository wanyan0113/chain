"""Build a structured planning/resource integration fixture, without flow templates."""
from copy import deepcopy
import json
from pathlib import Path

from task_flow.contracts import canonical_sha256, prepare_problem

ROOT = Path(__file__).resolve().parents[1]
NS = 'urn:arcane-rift-demo:'
REF = ['synthetic-task-flow-design-bridge-v1']
EXAMPLES = ROOT/'task_flow/examples/design_bridge'


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def build_job(index):
    target = f'Rift-{index}'
    arg = [{'param':'rift'}]
    def test(name,value=True,bound=False):
        return {'test':{'fact':{'predicate':name,'args':[target] if bound else arg},'op':'eq','value':value}}
    definitions = [
        ('observe','observe',['WideSurvey'],{'all':[test('observed',False)]},'observed'),
        ('analyze_primary','decide',['ResonanceAnalysis','PrimaryChannel'],
         {'all':[test('observed'),test('needs_analysis'),test('analyzed',False)]},'analyzed'),
        ('analyze_secondary','decide',['ResonanceAnalysis','BackupChannel'],
         {'all':[test('observed'),test('needs_analysis'),test('analyzed',False)]},'analyzed'),
        ('decide','decide',['Dispatch'],{'all':[test('observed'),test('decided',False),
             {'any':[test('needs_analysis',False),test('analyzed')]}]},'decided'),
        ('stabilize','act',['Stabilize'],{'all':[test('decided'),test('stable',False)]},'stable')]
    activities = [dict(id=name,version='1.0',type=kind,parameters={'rift':'rift'},applicable_when={'all':[]},
        preconditions=pre,effects=[{'fact':{'predicate':out,'args':arg},'set':{'value':True}}],
        required_capabilities=[NS+c for c in caps],max_occurrences=1,source_refs=REF)
        for name,kind,caps,pre,out in definitions]
    catalog = dict(format_version='task-flow-1.0',catalog_id='rift-bridge',version='1.0',objects={'rift':[target]},
        facts=[dict(id=name,arguments=['rift'],kind='static' if name == 'needs_analysis' else 'mutable',
                    type='boolean',source_refs=REF) for name in ('needs_analysis','observed','analyzed','decided','stable')],
        derived_rules=[],activities=activities,
        capabilities=[dict(uri=uri,mapping_status='unmapped',source_refs=REF)
                      for uri in sorted({c for a in activities for c in a['required_capabilities']})],source_refs=REF)
    request = dict(format_version='task-flow-1.0',request_id=f'request-{index}',catalog_id=catalog['catalog_id'],
        catalog_version='1.0',catalog_sha256=canonical_sha256(catalog),
        static_facts=[{'fact':{'predicate':'needs_analysis','args':[target]},'value':index != 1}],
        initial_state=[{'fact':{'predicate':name,'args':[target]},'value':False} for name in ('observed','analyzed','decided','stable')],
        ontology_evidence=[],goal=test('stable',bound=True),required_activities=[],forbidden_conditions=[],activity_limits={},
        search=dict(mode='first_feasible',max_length=5,max_nodes=20000,timeout_ms=10000),source_refs=REF)
    prepare_problem(catalog,request)
    return dict(task_id=f'T{index}',catalog=catalog,request=request)


def main():
    scene = json.loads((ROOT/'design/examples/rift_multistep_demo.json').read_text(encoding='utf-8'))
    del scene['knowledge']['sequences_file']
    scene['scope'] = '规划器根据状态生成三/四环节，再接真实OWL筛选与资源约束组网；无预写顺序模板。'
    scene['tasks'][2]['attributes']['target_type'] = '共振裂隙'
    scene['knowledge'].update(ontology_files=['rift-multistep.ttl','flow-design-bridge.ttl'],
                              rules_file='flow-design-bridge.rules.json',source_refs=REF)
    primary = next(a for a in scene['artifacts'] if a['id'] == 'R-Analysis')
    primary['ontology_types'] = [NS+'PrimaryAnalysisUnit']
    primary['attributes']['supported_functions'].append(NS+'PrimaryChannel')
    primary['available'] = False
    backup = deepcopy(primary)
    backup.update(id='R-Analysis-Backup',available=True,ontology_types=[NS+'BackupAnalysisUnit'])
    backup['attributes']['supported_functions'] = [NS+'ResonanceAnalysis',NS+'BackupChannel']
    backup['activity_metrics']['decide'].update(duration_s=3,accuracy=.2,cost={'value':2,'unit':'demo-credit/use'})
    scene['artifacts'].append(backup)
    for c in list(scene['contexts']):
        if c['artifact'] == primary['id']:
            scene['contexts'].append({**deepcopy(c),'artifact':backup['id']})
    for e in list(scene['links']):
        if primary['id'] in (e['from'],e['to']):
            clone = deepcopy(e)
            for endpoint in ('from','to'):
                if clone[endpoint] == primary['id']:
                    clone[endpoint] = backup['id']
            scene['links'].append(clone)
    save(EXAMPLES/'scene.json',scene)
    ttl = '''@prefix r: <urn:arcane-rift-demo:> .
@prefix b: <http://example.org/arcaneweb#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
r:PrimaryChannel a owl:NamedIndividual, b:CoordinationCapability ; rdfs:label "主解析通道"@zh .
r:BackupChannel a owl:NamedIndividual, b:CoordinationCapability ; rdfs:label "备用解析通道"@zh .
r:PrimaryAnalysisUnit a owl:Class ; rdfs:label "主解析法器类"@zh ; rdfs:subClassOf r:AnalysisUnit,
 [ a owl:Restriction ; owl:onProperty b:hasCapability ; owl:hasValue r:PrimaryChannel ] .
r:BackupAnalysisUnit a owl:Class ; rdfs:label "备用解析法器类"@zh ; rdfs:subClassOf r:AnalysisUnit,
 [ a owl:Restriction ; owl:onProperty b:hasCapability ; owl:hasValue r:BackupChannel ] .
'''
    (ROOT/'knowledge/flow-design-bridge.ttl').write_text(ttl,encoding='utf-8')
    rules = json.loads((ROOT/'knowledge/rift-multistep.rules.json').read_text(encoding='utf-8'))
    for cap in ('PrimaryChannel','BackupChannel'):
        rules['artifact_rules'].append(dict(id=cap,role='coordination',capability=NS+cap,
            when=[{'field':field,'op':'contains','value':NS+cap}
                  for field in ('artifact.supported_functions','artifact.ontology_capabilities')],
            source_refs=REF,blocking_issue_ids=[]))
    save(ROOT/'knowledge/flow-design-bridge.rules.json',rules)
    jobs = [build_job(i) for i in range(1,4)]
    for i,job in enumerate(jobs,1):
        save(EXAMPLES/f'catalog-{i}.json',job['catalog'])
        save(EXAMPLES/f'request-{i}.json',job['request'])
    save(EXAMPLES/'jobs.json',dict(format_version='task-flow-design-jobs-1.0',jobs=[
        dict(task_id=f'T{i}',catalog_file=f'catalog-{i}.json',request_file=f'request-{i}.json') for i in range(1,4)]))
    mapping = dict(format_version='task-flow-design-1.0',mapping_id='rift-bridge-v1',version='1.0',
        communication_model='directed',allow_nonadjacent_reuse=False,
        evaluation=dict(mode='insight_lock_product',source_refs=['表13口径的顺序多协调扩展：探知精度×锁定率']),
        entries=[],source_refs=REF)
    for action in jobs[0]['catalog']['activities']:
        activity = action['type']
        mapping['entries'].append(dict(catalog_id='rift-bridge',activity_id=action['id'],activity_version='1.0',
            target_parameter='rift',activity=activity,role={'observe':'insight','decide':'coordination','act':'manifestation'}[activity],
            min_level=.8,incoming_relation='control' if activity == 'act' else 'communication',source_refs=REF))
    save(EXAMPLES/'mapping.json',mapping)
    print('Prepared task_flow/examples/design_bridge and explicit bridge knowledge.')


if __name__ == '__main__':
    main()
