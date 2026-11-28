"""Explicit study pre-registration lock; distinct from actual runtime attempts."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from task_flow.joint_identity import sha
from task_flow.contracts import read_json
from .team_observation_io import CONTRACT, load_packet


def projections(data):
    contract=read_json(CONTRACT/'contract.json',allow_finite_floats=True)
    result={}
    for table,fields,keys in [('samples',contract['lock_plan_projections']['samples'],['sample_id']),
        ('executions',contract['lock_plan_projections']['executions'],['execution_id']),
        ('first_stage_attempts',contract['lock_plan_projections']['first_stage_attempts'],['execution_id','stage_instance_id','attempt_id'])]:
        rows=data['stage_attempts'] if table=='first_stage_attempts' else data[table]
        if table=='first_stage_attempts':rows=[r for r in rows if r['attempt_id']==1]
        result[table]=[{k:r[k] for k in fields} for r in sorted(rows,key=lambda r:tuple(r[k] for k in keys))]
    return result


def create_lock(data,protocol,sources,review_refs,*,frozen_at=None):
    if not review_refs or not all(type(s) is str and s.strip() for s in review_refs):raise ValueError('review evidence references required')
    if not all(data.get(t) for t in ('samples','executions','stage_attempts')):raise ValueError('nonempty sample, execution and first-slot registration required')
    if any(data[t] for t in ('member_observations','group_outputs','task_endpoints')):raise ValueError('pre-registration cannot contain observed outputs')
    if any(r['started_at'] or r['ended_at'] for r in data['executions']):raise ValueError('execution has already started')
    if any(r['execution_status']!='planned' or r['started_at'] or r['ended_at'] or r['attempt_id']!=1 for r in data['stage_attempts']):
        raise ValueError('only unexecuted first-attempt planning slots may be locked')
    from .team_observation_verify import check_package
    checked=check_package(data,protocol,None,sources,phase='registration')
    if checked['status']!='passed':raise ValueError('registration not ready: '+str(checked['issues']))
    frozen_at=frozen_at or datetime.now(timezone.utc).isoformat()
    when=datetime.fromisoformat(frozen_at.replace('Z','+00:00'))
    if when.tzinfo is None:raise ValueError('freeze timestamp requires timezone')
    if any(datetime.fromisoformat(r['registered_at'].replace('Z','+00:00'))>when for t in ('samples','executions','stage_attempts') for r in data[t]):
        raise ValueError('plan registration is later than the claimed freeze time')
    plan=projections(data)
    result=dict(version='team-study-lock-1.0',study_id=protocol['study_id'],protocol_hash=sha(protocol),
        source_hashes={k:sha(v) for k,v in sources.items()},plan=deepcopy(plan),plan_hash=sha(plan),
        frozen_at=frozen_at,review_evidence_refs=list(review_refs),data_origin=protocol['data_origin'],
        domain_validity='pending',external_signature_verified=False)
    result['lock_hash']=sha(result)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for k in ['dataset','protocol','sources','output']:parser.add_argument('--'+k,type=Path,required=True)
    parser.add_argument('--review-ref',action='append',required=True)
    a=parser.parse_args(argv)
    if a.output.exists():raise ValueError('lock exists; never overwrite')
    # The packet reader expects a lock path. Before locking, the supplied local
    # placeholder is an explicit empty JSON object and is never accepted as a lock.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        placeholder=Path(tmp)/'placeholder.json';placeholder.write_text('{}',encoding='utf-8')
        d,p,_,s,_,issues=load_packet(a.dataset,a.protocol,placeholder,a.sources)
    if issues:raise ValueError(str(issues))
    lock=create_lock(d,p,s,a.review_ref)
    from .team_observation_intake import save
    a.output.parent.mkdir(parents=True,exist_ok=True);save(a.output,lock)


if __name__=='__main__':main()
