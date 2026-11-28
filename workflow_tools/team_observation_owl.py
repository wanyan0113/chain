"""Fresh HermiT checks on observation roles, contribution types and known capabilities."""
from pathlib import Path
from urllib.parse import quote
import hashlib

from rdflib import Graph, Namespace, URIRef, RDF, OWL
from adapters.owl_infer import infer_owl
from design.vocabulary import ARTIFACT_TYPES, KW
from design.knowledge import ontology_for_document
from .team_observation_sources import source_payload

ROOT=Path(__file__).resolve().parents[1]
OBS=Namespace('urn:team-observation-validation:')
ROLE_CLASS={'insight':OBS.InsightContribution,'coordination':OBS.CoordinationContribution,'manifestation':OBS.ManifestationContribution}


def audit_owl(data, protocol, sources):
    graph=Graph().parse(ROOT/'knowledge/team-observation-validation.ttl',format='turtle')
    arms={a['arm_id']:a for a in protocol['arms']}
    executions={r['execution_id']:r for r in data['executions']}
    source_details={};capability_checks=[]
    for sid,report in sources.items():
        source=source_payload(report);scene=source['input_snapshot']['scene']
        graph+=ontology_for_document(scene)
        info={a['id']:a for a in scene['artifacts']}
        inference=source['analyses'][0]['ontology_inference'] if source['analyses'] else None
        roles={e['artifact']:e['role'] for e in inference['artifacts']} if inference else {}
        for artifact,role in roles.items():
            node=URIRef('urn:team-observed-artifact:'+quote(sid+'|'+artifact,safe=''))
            graph.add((node,RDF.type,OWL.NamedIndividual));graph.add((node,RDF.type,ARTIFACT_TYPES[role]))
            for cls in info[artifact].get('ontology_types',[]):graph.add((node,RDF.type,URIRef(cls)))
        source_details[sid]=set(tuple(f) for a in source['analyses'] for f in a['owl_reasoning']['facts'])
    subjects=[]
    for row in data['member_observations']:
        node=URIRef('urn:team-observation:'+quote(row['observation_id'],safe=''));subjects.append(str(node))
        graph.add((node,RDF.type,OWL.NamedIndividual));graph.add((node,RDF.type,OBS.MemberContribution))
        execution=executions.get(row['execution_id']);arm=arms.get(execution['arm_id']) if execution else None
        if arm:
            stage=next((s for s in arm['stages'] if s['stage_instance_id']==row['stage_instance_id']),None)
            if stage:
                role={'observe':'insight','assess':'insight','decide':'coordination','act':'manifestation'}[stage['activity_type']]
                graph.add((node,RDF.type,ROLE_CLASS[role]))
            member=URIRef('urn:team-observed-artifact:'+quote(execution['source_report_id']+'|'+row['artifact_id'],safe=''))
            graph.add((node,OBS.observesArtifact,member))
            if row['capability_id'].startswith(('urn:','http://','https://')):
                graph.add((node,OBS.validatesCapability,URIRef(row['capability_id'])))
            from design.owl_scene import scene_uri
            fact=(str(scene_uri('artifact',row['artifact_id'])),str(KW.hasCapability),row['capability_id'])
            capability_checks.append(dict(observation_id=row['observation_id'],artifact_id=row['artifact_id'],
                capability_id=row['capability_id'],source_report_id=execution['source_report_id'],
                status='entailed' if fact in source_details.get(execution['source_report_id'],set()) else 'unknown'))
    for row in data['group_outputs']:
        node=URIRef('urn:team-observation:'+quote(row['observation_id'],safe=''));subjects.append(str(node))
        graph.add((node,RDF.type,OWL.NamedIndividual));graph.add((node,RDF.type,OBS.GroupOutput))
        for ref in row['member_observation_ids']:
            graph.add((node,OBS.hasContribution,URIRef('urn:team-observation:'+quote(ref,safe=''))))
    for row in data['task_endpoints']:
        node=URIRef('urn:team-observation:'+quote(row['observation_id'],safe=''));subjects.append(str(node))
        graph.add((node,RDF.type,OWL.NamedIndividual));graph.add((node,RDF.type,OBS.TaskEndpoint))
    result=infer_owl(graph,sorted(set(subjects)),[str(OBS.observesArtifact),str(OBS.validatesCapability),str(OBS.hasContribution)])
    return dict(**result,ontology_file='knowledge/team-observation-validation.ttl',
        capability_support=capability_checks,capability_support_complete=all(c['status']=='entailed' for c in capability_checks),
        ontology_sha256=hashlib.sha256((ROOT/'knowledge/team-observation-validation.ttl').read_bytes()).hexdigest(),
        scope='OWL role/type consistency and entailed contribution classification only; no completeness or statistical validity claim',
        input_ttl=graph.serialize(format='turtle'), missing_records_are_not_owl_inconsistency=True)
