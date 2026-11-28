"""Versioned team input entry, separate from existing single-equipment intake."""
import argparse
import hashlib
import json
from pathlib import Path

from task_flow.joint_identity import sha, new_run_id
from .team_observation_io import load_packet, InputError


def save(path, value):
    path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))


def run(dataset,protocol,lock,sources,output):
    if output.exists(): raise ValueError('output exists; choose a new directory')
    try:
        data,p,l,s,files,issues=load_packet(dataset,protocol,lock,sources)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        output.mkdir(parents=True)
        issue=exc.issue if isinstance(exc,InputError) else dict(code='INPUT_READ',message=str(exc),path=[])
        result=dict(version='team-observation-intake-1.0',status='format_error',issues=[issue],domain_validity='pending')
        save(output/'intake_report.json',result)
        return result
    output.mkdir(parents=True);(output/'raw').mkdir()
    entries=[]
    for i,file in enumerate(files):
        name=f'raw/{i:04d}.bin';(output/name).write_bytes(file['raw'])
        entries.append(dict(role=file['role'],original_path=file['path'],snapshot=name,sha256=file['sha256']))
    normalized=dict(dataset=data,protocol=p,lock=l,sources=s)
    save(output/'normalized.json',normalized)
    receipt=dict(version='team-observation-receipt-1.0',run_id=new_run_id(),files=entries,normalized_sha256=sha(normalized))
    save(output/'receipt.json',receipt)
    result=dict(version='team-observation-intake-1.0',status='format_error' if issues else 'imported_structure_only',
        issues=issues,receipt_sha256=sha(receipt),semantic_verification='not_run',domain_validity='pending',production_writeback=False)
    save(output/'intake_report.json',result)
    if not issues:
        from .team_observation_verify import verify_intake
        verification=verify_intake(output,expected_receipt_hash=result['receipt_sha256'])
        save(output/'verification.json',verification)
        result['semantic_verification']=verification['status']
        save(output/'intake_report.json',result)
    print(json.dumps(dict(status=result['status'],semantic_verification=result.get('semantic_verification'),issues=len(issues)),ensure_ascii=False))
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for k in ['dataset','protocol','lock','sources','output-dir']:parser.add_argument('--'+k,type=Path,required=True)
    a=parser.parse_args(argv)
    r=run(a.dataset,a.protocol,a.lock,a.sources,a.output_dir)
    return 0 if r['status']=='imported_structure_only' and r['semantic_verification']=='passed' else 2


if __name__=='__main__':raise SystemExit(main())
