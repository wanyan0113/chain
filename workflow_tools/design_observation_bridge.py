"""Verified selected design -> editable observation protocol and immutable bindings."""
import argparse
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path

from task_flow.joint_verify import verify_joint
from workflow_tools.accuracy_observations import ROOT,FIELDS,canonical_hash,read_json,readiness,require,validate_protocol
from workflow_tools.benchmark_joint import save

VERSION='tfdo-design-observation-bridge-1.0'
ROSTER_FIELDS=['task_id','observation_flow_id','source_flow_id','candidate_id','stage_index','stage_instance_id',
               'activity_id','activity_version','activity_type','role','artifact_id','artifact_version','target']


def build(report,task_ids=None,artifact_versions=None):
    require(not isinstance(report, dict) or report.get('mode') != 'complementary_team_v1',
            'complementary teams do not support legacy observation export in phase one')
    require(type(report) is dict and report.get('format_version')=='tfdo-joint-report-1.0','expected joint report')
    require(report.get('integration_status') in ('selected','feasible') and bool(report.get('selected_candidate_ids')),
            'source report has no selected feasible design')
    check=verify_joint(report)
    require(check['status']=='passed','source independent verification failed: '+check.get('reason',''))
    candidates=[]
    for cid in report['selected_candidate_ids']:
        candidate=report['candidates'][cid]
        require(candidate['candidate_id']==cid and candidate['flow_id']==candidate['identity_payload']['flow_id'],
                'redundant candidate identity disagrees with canonical source')
        candidates.append(candidate)
    available={c['task_id'] for c in candidates}
    selected=available if task_ids is None else set(task_ids)
    require(bool(selected) and selected<=available,'task selector is empty or not in selected network')
    candidates=sorted((c for c in candidates if c['task_id'] in selected),key=lambda c:c['task_id'])
    used={s['artifact'] for c in candidates for s in c['chain']['plan']['steps']}
    versions={} if artifact_versions is None else deepcopy(artifact_versions)
    require(type(versions) is dict and set(versions)<=used,'version declarations reference an unselected artifact')
    require(all(type(v) is str and v==v.strip() and v and v!='UNASSIGNED' for v in versions.values()),'invalid declared artifact version')
    canonical_hash(versions)
    protocol=read_json(ROOT/'data/accuracy-observations/templates/protocol.json')
    protocol['flows']=[];bindings=[];roster=[]
    scene=report['input_snapshot']['scene'];artifacts={a['id']:a for a in scene['artifacts']};tasks={t['id']:t for t in scene['tasks']}
    for candidate in candidates:
        cid=candidate['candidate_id'];task_id=candidate['task_id'];stages=[];rows=[]
        instances=candidate['identity_payload']['activity_instance_sequence'];steps=candidate['chain']['plan']['steps']
        require(len(instances)==len(steps),'activity/assignment length mismatch')
        for index,(instance,step) in enumerate(zip(instances,steps),1):
            version=versions.get(step['artifact'],'UNASSIGNED')
            stage=dict(stage_instance_id=step['id'],activity_id=instance['activity_id'],activity_version=instance['activity_version'],
                artifact_id=step['artifact'],artifact_version=version,calibration_input_condition='natural_case')
            stages.append(stage)
            rows.append(dict(stage_index=index,**stage,activity_type=step['activity'],role=step['role'],
                parameters=deepcopy(instance['parameters']),required_capabilities=deepcopy(step['required_capabilities']),
                artifact_snapshot_hash=canonical_hash(artifacts[step['artifact']]),
                planned_start_s=step['start_s'],planned_end_s=step['end_s']))
            roster.append(dict(task_id=task_id,observation_flow_id=cid,source_flow_id=candidate['flow_id'],candidate_id=cid,
                stage_index=index,stage_instance_id=step['id'],activity_id=instance['activity_id'],activity_version=instance['activity_version'],
                activity_type=step['activity'],role=step['role'],artifact_id=step['artifact'],artifact_version=version,
                target=tasks[task_id]['target']))
        # A candidate, not just activity-flow identity, identifies an assignment-specific study flow.
        protocol['flows'].append(dict(flow_id=cid,calibration_population='',input_from_previous_output=False,stages=stages))
        bindings.append(dict(task_id=task_id,observation_flow_id=cid,candidate_id=cid,source_flow_id=candidate['flow_id'],
            source_candidate_identity=deepcopy(candidate['identity_payload']),task_snapshot=deepcopy(tasks[task_id]),stages=rows))
    validate_protocol(protocol)
    plan_id='collection-'+canonical_hash(dict(version=VERSION,selected_candidate_ids=[c['candidate_id'] for c in candidates]))
    binding=dict(version=VERSION,collection_plan_id=plan_id,
        identity_scope='source design selection only; actual measurement context belongs in separately frozen protocol',
        source_run_id=report['run_id'],source_global_optimality=report['global_optimality'],source_verification=check,
        source_knowledge_snapshot_hash=report['knowledge_snapshot_hash'],source_design_context_hash=report['design_context_hash'],
        artifact_versions_are_external_declarations=True,declared_artifact_versions=versions,
        selected_tasks=sorted(selected),source_network_tasks=sorted(available),flows=bindings,
        selected_artifact_snapshots=[deepcopy(artifacts[identifier]) for identifier in sorted(used)],
        domain_validity='pending',measurements_generated=False)
    return protocol,binding,roster


def export(report_bytes,output,*,task_ids=None,artifact_versions=None):
    require(not output.exists(),'choose a new collection directory')
    # Strict duplicate-key/nonfinite parsing also applies to the retained source bytes.
    from workflow_tools.accuracy_observations import read_json as strict_read
    import tempfile
    (ROOT/'tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
        source=Path(directory)/'source.json';source.write_bytes(report_bytes);report=strict_read(source)
    protocol,binding,roster=build(report,task_ids,artifact_versions)
    output.mkdir(parents=True)
    (output/'source_joint_report.json').write_bytes(report_bytes)
    save(output/'protocol-template.json',protocol);save(output/'protocol.json',protocol);save(output/'binding.json',binding)
    with (output/'observations.csv').open('w',encoding='utf-8',newline='') as handle:
        csv.DictWriter(handle,fieldnames=FIELDS,lineterminator='\n').writeheader()
    with (output/'stage-roster.csv').open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=ROSTER_FIELDS,lineterminator='\n');writer.writeheader();writer.writerows(roster)
    (output/'README.md').write_text('\n'.join(['# 从所选法器网生成的数据采集包','',
        '此目录只有计划和身份清单，没有生成真实测量。原始设计通过独立核验，不代表精度领域有效。',
        '填写protocol.json及observations.csv；保留其余文件，以便核对任务、活动、法器及来源。',
        'stage-roster.csv是环节清单，不是观测数据。UNASSIGNED表示真实法器版本尚未提供。',
        '规划快照及其指标是设计输入，不是实测精度或实际固件版本。',
        '填写目标标签、总体、标签来源、方法审核、版本、抽样和容差后，再冻结研究协议。',
        'input_from_previous_output及calibration_input_condition是可编辑采集假设，需要按实际研究核实。',
        '运行workflow_tools.design_observation_bridge --check-pack检查身份；实际统计另用validate_accuracy_observations。','']),encoding='utf-8')
    names=['source_joint_report.json','protocol-template.json','binding.json','stage-roster.csv','README.md']
    save(output/'pack-manifest.json',dict(version=VERSION,
        immutable_files=[dict(path=name,sha256=hashlib.sha256((output/name).read_bytes()).hexdigest()) for name in names],
        working_files=['protocol.json','observations.csv']))
    return check_pack(output)


def check_pack(output):
    manifest=read_json(output/'pack-manifest.json')
    require(manifest.get('version')==VERSION,'unsupported collection pack')
    expected={'source_joint_report.json','protocol-template.json','binding.json','stage-roster.csv','README.md'}
    require({f['path'] for f in manifest['immutable_files']}==expected and len(manifest['immutable_files'])==len(expected),'unexpected immutable files')
    for record in manifest['immutable_files']:
        require(hashlib.sha256((output/record['path']).read_bytes()).hexdigest()==record['sha256'],'immutable collection file changed: '+record['path'])
    binding=read_json(output/'binding.json');source=read_json(output/'source_joint_report.json')
    expected_protocol,expected_binding,expected_roster=build(source,binding['selected_tasks'],binding['declared_artifact_versions'])
    require(expected_binding==binding,'collection binding differs from independently verified source')
    require(read_json(output/'protocol-template.json')==expected_protocol,'original protocol template differs from source')
    with (output/'stage-roster.csv').open(encoding='utf-8',newline='') as handle:
        rows=list(csv.DictReader(handle))
    require(rows==[{k:str(v) for k,v in r.items()} for r in expected_roster],'stage roster differs from source')
    protocol=validate_protocol(read_json(output/'protocol.json'))
    expected_flows={f['flow_id']:f for f in expected_protocol['flows']}
    require({f['flow_id'] for f in protocol['flows']}==set(expected_flows),'working protocol changed candidate-bound flow identities')
    for flow in protocol['flows']:
        original=expected_flows[flow['flow_id']]
        require(len(flow['stages'])==len(original['stages']),'working protocol lost/added a stage')
        for stage,expected_stage in zip(flow['stages'],original['stages']):
            require(all(stage[k]==expected_stage[k] for k in ('stage_instance_id','activity_id','activity_version','artifact_id')),
                    'working protocol changed stage order or source assignment')
            if expected_stage['artifact_version']!='UNASSIGNED':
                require(stage['artifact_version']==expected_stage['artifact_version'],'declared version changed; export a new pack')
    return dict(version=VERSION,collection_plan_id=binding['collection_plan_id'],binding_status='passed',
        source_global_optimality=binding['source_global_optimality'],selected_tasks=binding['selected_tasks'],
        stage_counts={f['task_id']:len(f['stages']) for f in binding['flows']},
        protocol_readiness_blockers=readiness(protocol),observations_statistically_validated=False,
        domain_validity='pending',measurements_generated=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--joint-report',type=Path)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--task-id',action='append')
    parser.add_argument('--artifact-versions-json',type=Path)
    parser.add_argument('--check-pack',type=Path)
    args=parser.parse_args(argv)
    if args.check_pack:
        require(not any((args.joint_report,args.output_dir,args.task_id,args.artifact_versions_json)),'check separately from export')
        result=check_pack(args.check_pack)
    else:
        require(args.joint_report and args.output_dir,'joint report and output directory required')
        result=export(args.joint_report.read_bytes(),args.output_dir,task_ids=args.task_id,
                      artifact_versions=read_json(args.artifact_versions_json) if args.artifact_versions_json else None)
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
