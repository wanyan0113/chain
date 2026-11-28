"""Rebuild the fictional 3/4/5-step knowledge and scene fixtures."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NS = 'urn:arcane-rift-demo:'
REF = ['docs/rift_multistep_demo.md：虚构实验设定v1']
TARGETS = ['普通裂隙', '共振裂隙', '迷雾裂隙']
# key, label, role, activity, class
FUNCTIONS = [
    ('WideSurvey', '广域探知', 'insight', 'observe', 'SurveyUnit'),
    ('FineScan', '精细复核', 'insight', 'observe', 'ReviewUnit'),
    ('ResonanceAnalysis', '共振解析', 'coordination', 'decide', 'AnalysisUnit'),
    ('Dispatch', '稳定术协调', 'coordination', 'decide', 'DispatchUnit'),
    ('Stabilize', '裂隙稳定', 'manifestation', 'act', 'StabilizerUnit'),
]


def q(value, unit='1'):
    return {'value': value, 'unit': unit}


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    ttl = ['@prefix r: <' + NS + '> .',
           '@prefix b: <http://example.org/arcaneweb#> .',
           '@prefix owl: <http://www.w3.org/2002/07/owl#> .',
           '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .',
           '@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .',
           'r:Ontology a owl:Ontology ; owl:versionInfo "1.0" ; rdfs:label "迷雾裂隙稳定实验"@zh .']
    rules = []
    for cap, label, role, activity, cls in FUNCTIONS:
        prefix = {'insight': 'Insight', 'coordination': 'Coordination', 'manifestation': 'Manifestation'}[role]
        ttl += [f'r:{cap} a owl:NamedIndividual, b:{prefix}Capability ; rdfs:label "{label}"@zh .',
                f'r:{cls} a owl:Class ; rdfs:label "{label}法器类"@zh ; rdfs:subClassOf b:{prefix}Artifact,',
                f'  [ a owl:Restriction ; owl:onProperty b:hasCapability ; owl:hasValue r:{cap} ] .',
                f'r:{cap}Step a owl:Class ; rdfs:label "{label}环节类"@zh ; rdfs:subClassOf b:SequentialStepMission,',
                f'  [ a owl:Restriction ; owl:onProperty b:requiresCapability ; owl:hasValue r:{cap} ] .']
        conditions = [
            # This is an explicit complete function list for this fixture. Its
            # absence from an OWL entailment alone would mean unknown, not false.
            {'field': 'artifact.supported_functions', 'op': 'contains', 'value': NS + cap},
            {'field': 'artifact.ontology_capabilities', 'op': 'contains', 'value': NS + cap},
        ]
        rules.append(dict(id=cap, role=role, capability=NS+cap, when=conditions,
                          source_refs=REF, blocking_issue_ids=[]))
    paths = [[0, 3, 4], [0, 2, 3, 4], [0, 2, 1, 3, 4]]
    templates = []
    for i, (target, path) in enumerate(zip(TARGETS, paths), 1):
        ttl += [f'r:Target{i} a owl:Class ; rdfs:label "{target}目标类"@zh ; owl:equivalentClass [ a owl:Class ; owl:intersectionOf (',
                f' b:Target [ a owl:Restriction ; owl:onProperty b:targetType ; owl:hasValue "{target}"^^xsd:string ] ) ] .',
                f'r:Mission{i} a owl:Class ; rdfs:label "{target}任务类"@zh ; owl:equivalentClass [ a owl:Class ; owl:intersectionOf (',
                f' b:ArcaneMission [ a owl:Restriction ; owl:onProperty b:hasTarget ; owl:someValuesFrom r:Target{i} ] ) ] ;',
                ' rdfs:subClassOf ' + ', '.join(
                    f'[ a owl:Restriction ; owl:onProperty b:requiresCapability ; owl:hasValue r:{FUNCTIONS[j][0]} ]'
                    for j in path) + ' .']
        steps = []
        for index, j in enumerate(path):
            cap, label, role, activity, cls = FUNCTIONS[j]
            step = dict(id=cap, activity=activity, role=role, capabilities=[NS+cap],
                        ontology_types=[NS+cap+'Step'], min_level=.8, source_refs=REF)
            if index:
                step['incoming_relation'] = 'control' if activity == 'act' else 'communication'
            steps.append(step)
        templates.append(dict(id=f'rift-{len(path)}-step',
                              when=[{'field': 'task.target_type', 'op': 'eq', 'value': target}],
                              allow_nonadjacent_reuse=False, steps=steps, source_refs=REF))
    (ROOT/'knowledge/rift-multistep.ttl').write_text('\n\n'.join(ttl)+'\n', encoding='utf-8')
    save(ROOT/'knowledge/rift-multistep.rules.json', dict(task_rules=[], artifact_rules=rules))
    save(ROOT/'knowledge/rift-multistep.flows.json', dict(templates=templates, communication_model='directed',
         evaluation={'mode': 'sequential_product', 'source_refs': REF}, source_refs=REF))
    doc = dict(format_version='1.0', scope='虚构迷雾裂隙稳定；三、四、五环节共同组网；指标独立设定，不复算原文表13。',
               policies=dict(equal_metric_score=.5, cost_unit='demo-credit/use', max_chain_combinations=10000,
                             max_network_combinations=10000, article_table10=True,
                             network_search_method='branch_and_bound'),
               weights=dict(time=.3, accuracy=.3, cost=.4), tasks=[], artifacts=[],
               task_rules=[], artifact_rules=[], contexts=[], links=[], dependencies=[], resource_groups=[],
               knowledge=dict(ontology_files=['rift-multistep.ttl'], rules_file='rift-multistep.rules.json',
                              sequences_file='rift-multistep.flows.json', source_refs=REF))
    for i, target in enumerate(TARGETS, 1):
        doc['tasks'].append(dict(id=f'T{i}', target=f'Rift-{i}', start_s=0,
            attributes=dict(target_type=target, target_count=q(1), impedance=q(2),
                            target_speed=q(0, 'demo-speed'), target_altitude=q(50, 'demo-height')),
            source_refs=REF, blocking_issue_ids=[]))
    specs = [('R-Survey', 0, 2, .99, 1, 60), ('R-Review', 1, 3, .98, 2, 40),
             ('R-Analysis', 2, 2, .99, 1, None), ('R-Dispatch', 3, 1, .99, 1, None),
             ('R-Seal-A', 4, 2, .90, 9, 10), ('R-Seal-B', 4, 4, .95, 6, 20),
             ('R-Seal-C', 4, 6, .99, 3, 30), ('R-Seal-D', 4, 9, .97, 5, 40)]
    for name, j, duration, accuracy, cost, reach in specs:
        cap, label, role, activity, cls = FUNCTIONS[j]
        attrs = dict(target_types=TARGETS, supported_functions=[NS+cap])
        attrs['level_'+activity] = q(.9)
        if role == 'insight':
            attrs.update(detection_range=q(reach, 'demo-range'), max_tracks=q(4))
        elif role == 'coordination':
            attrs.update(max_processing_targets=q(4))
        else:
            attrs.update(min_range=q(0, 'demo-range'), max_range=q(reach, 'demo-range'), resonance=q(10),
                         max_target_speed=q(10, 'demo-speed'), min_target_altitude=q(0, 'demo-height'),
                         max_target_altitude=q(100, 'demo-height'), min_activation_altitude=q(0, 'demo-height'),
                         max_activation_altitude=q(100, 'demo-height'))
        doc['artifacts'].append(dict(id=name, available=True, ontology_types=[NS+cls], attributes=attrs,
             metrics=None, activity_metrics={activity:dict(duration_s=duration, accuracy=accuracy,
                                                          cost=q(cost, 'demo-credit/use'))},
             capacity=1 if role == 'manifestation' else 3, consumable=role == 'manifestation',
             inventory=int(role == 'manifestation'), cooldown_s=0, source_refs=REF, blocking_issue_ids=[]))
        for i in range(1, 4):
            doc['contexts'].append(dict(task=f'T{i}', artifact=name,
                attributes=dict(target_distance=q(5+10*(i-1), 'demo-range'), platform_altitude=q(50, 'demo-height')),
                source_refs=REF, blocking_issue_ids=[]))
    pairs = [('R-Survey','R-Dispatch'), ('R-Survey','R-Analysis'), ('R-Analysis','R-Dispatch'),
             ('R-Analysis','R-Review'), ('R-Review','R-Dispatch')]
    pairs += [('R-Dispatch', f'R-Seal-{c}') for c in 'ABCD']
    for left, right in pairs:
        doc['links'].append(dict(**{'from':left, 'to':right}, communicates=True, controls=right.startswith('R-Seal-'),
             distance=q(1, 'demo-range'), max_distance=q(50, 'demo-range'), source_refs=REF, blocking_issue_ids=[]))
    save(ROOT/'design/examples/rift_multistep_demo.json', doc)
    print('Prepared rift_multistep_demo.json and three knowledge files.')


if __name__ == '__main__':
    main()
