"""Pre-collection registration checks; never freezes a protocol or starts a study."""
import argparse
from pathlib import Path
import tempfile

from task_flow.joint_identity import sha
from .team_observation_io import load_packet, InputError
from .team_observation_intake import save
from .team_observation_verify import check_package


def preflight(data, protocol, sources):
    issues = []
    for table in ('samples', 'executions', 'stage_attempts'):
        if not data.get(table):
            issues.append(dict(code='EMPTY_REGISTRATION', table=table))
    for table in ('member_observations', 'group_outputs', 'task_endpoints'):
        if data.get(table):
            issues.append(dict(code='OBSERVATIONS_BEFORE_REGISTRATION', table=table))
    for table in ('executions', 'stage_attempts'):
        for i, row in enumerate(data.get(table, []), 1):
            if row.get('started_at') or row.get('ended_at'):
                issues.append(dict(code='EXECUTION_ALREADY_STARTED', table=table, row=i))
            if table == 'stage_attempts' and (row.get('attempt_id') != 1 or row.get('execution_status') != 'planned'):
                issues.append(dict(code='NOT_PLANNED_FIRST_SLOT', table=table, row=i))
    # Use the existing registration contract and real source/OWL verification.
    # Do not invent a study lock, alter protocol status or fill unknown labels.
    checked = check_package(data, protocol, None, sources, phase='registration')
    issues.extend(checked['issues'])
    return dict(version='team-collection-preflight-1.0',
                status='blocked' if issues else 'registration_software_checks_passed',
                study_id=protocol.get('study_id'), protocol_hash=sha(protocol),
                dataset_hash=sha(data), source_hashes={k:sha(v) for k,v in sources.items()},
                data_origin=data.get('data_origin'), issues=issues, verification=checked,
                human_review=[dict(item=item, status='unreviewed', evidence_reference=None) for item in (
                    'independent_ground_truth_and_labeling', 'live_equipment_and_firmware_versions',
                    'sample_population_cluster_and_held_out_split', 'pairing_reset_and_execution_order',
                    'actual_input_output_trace_and_retry_capture', 'missing_failure_unknown_label_training',
                    'external_preservation_of_protocol_and_registration_evidence')],
                real_collection_authorized=False, study_lock_created=False,
                external_facts_authenticated=False, domain_validity='pending',
                production_writeback=False)


def run(dataset, protocol, sources, output):
    if output.exists():
        raise ValueError('output exists; never overwrite an earlier preflight')
    try:
        with tempfile.TemporaryDirectory() as tmp:
            placeholder=Path(tmp)/'not-a-study-lock.json'
            placeholder.write_text('{}', encoding='utf-8')
            d,p,_,s,_,issues=load_packet(dataset, protocol, placeholder, sources)
        result = (dict(version='team-collection-preflight-1.0', status='blocked', issues=issues,
                       domain_validity='pending') if issues else preflight(d,p,s))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result=dict(version='team-collection-preflight-1.0', status='blocked', domain_validity='pending',
                    issues=[exc.issue if isinstance(exc,InputError) else dict(code='INPUT_READ', message=str(exc))])
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output,result)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('dataset','protocol','sources','output'):
        parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args(argv)
    result=run(args.dataset,args.protocol,args.sources,args.output)
    return 0 if result['status']=='registration_software_checks_passed' else 2


if __name__=='__main__':
    raise SystemExit(main())
