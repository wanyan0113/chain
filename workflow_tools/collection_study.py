"""Mandatory design binding -> protocol freeze -> linked observation assessment."""
import argparse
from contextlib import contextmanager
import hashlib
from pathlib import Path
import tempfile

from workflow_tools.accuracy_observations import (ROOT,read_json,canonical_hash,freeze_protocol,analyze,require)
from workflow_tools.design_observation_bridge import check_pack
from workflow_tools.benchmark_joint import save

VERSION='tfdo-collection-study-1.0'
PACK_FILES=['pack-manifest.json','source_joint_report.json','binding.json','stage-roster.csv','README.md',
            'protocol-template.json','protocol.json','observations.csv']


def digest(data):return hashlib.sha256(data).hexdigest()


def gateway_sources():
    return {p:digest((ROOT/p).read_bytes().replace(b'\r\n',b'\n')) for p in
        ('workflow_tools/collection_study.py','workflow_tools/design_observation_bridge.py','task_flow/joint_verify.py')}


@contextmanager
def captured_pack(pack):
    # Verify and analyze the same retained bytes, including editable protocol/CSV.
    (ROOT/'tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
        snapshot=Path(directory)
        for name in PACK_FILES:(snapshot/name).write_bytes((pack/name).read_bytes())
        check=check_pack(snapshot)
        yield snapshot,check


def context(snapshot,check):
    return dict(collection_plan_id=check['collection_plan_id'],
        source_report_sha256=digest((snapshot/'source_joint_report.json').read_bytes()),
        binding_sha256=digest((snapshot/'binding.json').read_bytes()),
        pack_manifest_sha256=digest((snapshot/'pack-manifest.json').read_bytes()),
        source_global_optimality=check['source_global_optimality'])


def freeze(pack,output):
    require(not output.exists(),'collection lock already exists')
    with captured_pack(pack) as (snapshot,check):
        protocol=read_json(snapshot/'protocol.json')
        inner=freeze_protocol(protocol,snapshot/'inner-lock.json')
        lock=dict(version=VERSION,collection_context=context(snapshot,check),
            gateway_sources=gateway_sources(),protocol_lock=inner,domain_validity='pending',
            scope='binds a frozen research protocol to exact verified source design; not a provenance certification')
        save(output,lock)
        return lock


def assess(pack,lock_path,output):
    require(not output.exists(),'choose a new study result directory')
    lock=read_json(lock_path)
    require(lock.get('version')==VERSION,'unsupported collection lock')
    with captured_pack(pack) as (snapshot,check):
        actual_context=context(snapshot,check);protocol=read_json(snapshot/'protocol.json')
        blockers=[]
        if actual_context!=lock.get('collection_context'):blockers.append('collection_context_changed_since_freeze')
        if gateway_sources()!=lock.get('gateway_sources'):blockers.append('gateway_source_changed_since_freeze')
        inner=lock.get('protocol_lock')
        require(type(inner) is dict,'missing inner protocol lock')
        if inner.get('protocol_hash')!=canonical_hash(protocol):blockers.append('protocol_changed_since_collection_freeze')
        observation=None
        if not blockers:
            observation=analyze(protocol,snapshot/'observations.csv',inner)
            blockers.extend(observation['blockers'])
        binding=read_json(snapshot/'binding.json')
        by_flow={f['flow_id']:f for f in observation['flows']} if observation else {}
        protocol_flows={f['flow_id']:f for f in protocol['flows']}
        links=[]
        for flow in binding['flows']:
            fid=flow['observation_flow_id'];stats=by_flow.get(fid)
            links.append(dict(task_id=flow['task_id'],source_flow_id=flow['source_flow_id'],candidate_id=flow['candidate_id'],
                observation_flow_id=fid,stage_bindings=protocol_flows[fid]['stages'],
                statistical_assessment=stats['statistical_assessment'] if stats else None,domain_validity='pending'))
        summary=dict(version=VERSION,status='blocked' if blockers else 'analyzed',blockers=sorted(set(blockers)),
            collection_context=actual_context,protocol_hash=canonical_hash(protocol),
            observations_sha256=digest((snapshot/'observations.csv').read_bytes()),
            observation_report_canonical_hash=canonical_hash(observation) if observation else None,
            linked_candidates=links,domain_validity='pending',domain_approval_automatic=False,
            production_accuracy_updated=False,source_report_updated=False)
        output.mkdir(parents=True)
        for name in PACK_FILES:(output/name).write_bytes((snapshot/name).read_bytes())
        save(output/'collection_lock_snapshot.json',lock)
        save(output/'collection_study_report.json',summary)
        if observation is not None:save(output/'observation_report.json',observation)
        return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    freezer=sub.add_parser('freeze');freezer.add_argument('--pack',type=Path,required=True);freezer.add_argument('--lock',type=Path,required=True)
    scorer=sub.add_parser('analyze');scorer.add_argument('--pack',type=Path,required=True);scorer.add_argument('--lock',type=Path,required=True)
    scorer.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.command=='freeze':
        freeze(args.pack,args.lock);print('verified collection protocol frozen; domain_validity=pending');return 0
    result=assess(args.pack,args.lock,args.output_dir)
    print(result['status']+'; domain_validity=pending')
    return 2 if result['blockers'] else 0


if __name__=='__main__':raise SystemExit(main())
