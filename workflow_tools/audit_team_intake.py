"""Actual six-table intake plus deliberately damaged synthetic packets."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from .team_observation_demo import inputs,write_packet,stamp
from .team_study_lock import create_lock
from .team_observation_intake import run as intake,save


def mutations(data):
    cases=[]
    d=deepcopy(data);d['member_observations'].pop(0)
    cases.append(('missing_member',d,{'MISSING_MEMBER_OBSERVATION'}))
    d=deepcopy(data)
    for r in d['member_observations']:
        if r['sample_id'].startswith('T2') and r['stage_instance_id'] in ('s2','s3'):
            r['stage_instance_id']='s3' if r['stage_instance_id']=='s2' else 's2'
    cases.append(('swapped_T2_stages',d,{'MEMBER_NOT_ASSIGNED'}))
    d=deepcopy(data);retry=deepcopy(d['stage_attempts'][0]);retry['registered_at']=retry['started_at'];d['stage_attempts'].append(retry)
    cases.append(('retry_claimed_as_first',d,{'DUPLICATE_KEY','FROZEN_PLAN_CHANGED'}))
    d=deepcopy(data);r=next(r for r in d['member_observations'] if r['stage_instance_id']=='s2')
    other=next(g for g in d['group_outputs'] if g['stage_instance_id']=='s1' and g['sample_id']!=r['sample_id'])
    r.update(input_reference=other['output_reference'],input_group_observation_ids=[other['observation_id']])
    cases.append(('cross_sample_input',d,{'CROSS_SAMPLE_GROUP_REFERENCE'}))
    d=deepcopy(data);d['member_observations'][0]['artifact_version']='changed-firmware'
    cases.append(('firmware_changed',d,{'ARTIFACT_VERSION_MISMATCH'}))
    d=deepcopy(data);d['samples'][2]['cluster_id']=d['samples'][0]['cluster_id']
    cases.append(('cluster_leaks_into_evaluation',d,{'CLUSTER_SPLIT_LEAKAGE'}))
    d=deepcopy(data);d['samples'][0]['pair_id']='tampered-pair'
    cases.append(('pair_id_changed',d,{'PAIR_ID_CHANGED'}))
    d=deepcopy(data);d['member_observations'][0].update(observation_status='missing',actual_label='wrong',missing_reason='output_lost')
    cases.append(('unknown_claimed_as_error',d,{'SCHEMA'}))
    d=deepcopy(data);d['stage_attempts'].pop(0)
    cases.append(('missing_actual_first_attempt',d,{'MISSING_ACTUAL_FIRST_ATTEMPT_RECORD','MISSING_FROZEN_PLAN_SLOT'}))
    d=deepcopy(data);d['task_endpoints'][0]['observation_id']=d['member_observations'][0]['observation_id']
    cases.append(('global_observation_id_collision',d,{'GLOBAL_OBSERVATION_ID_DUPLICATE'}))
    d=deepcopy(data);r=next(r for r in d['member_observations'] if r['stage_instance_id']=='s2');r['artifact_id']='R-Survey'
    cases.append(('ontology_role_conflict',d,{'MEMBER_NOT_ASSIGNED','OWL_OBSERVATION_CONSISTENCY'}))
    return cases


def run(output):
    if output.exists():raise ValueError('output exists')
    plan,data,p,sources=inputs()
    lock=create_lock(plan,p,sources,['synthetic-adversarial-study-review-only'],frozen_at=stamp(10))
    output.mkdir(parents=True)
    results=[]
    for name,variant,expected in [('valid_csv',data,set()),*mutations(data)]:
        packet=output/name/'packet';packet.parent.mkdir()
        write_packet(packet,variant,p,lock,sources,csv_mode=name=='valid_csv')
        imported=intake(packet/'dataset.json',packet/'protocol.json',packet/'lock.json',packet/'sources.json',packet.parent/'intake')
        if imported['status']=='format_error':
            issues=imported['issues'];verification=None
        else:
            verification=json.loads((packet.parent/'intake/verification.json').read_text(encoding='utf-8'));issues=verification['issues']
        actual={i['code'] for i in issues}
        if not expected <= actual:raise AssertionError((name,expected,actual))
        if name=='valid_csv' and (verification is None or verification['status']!='passed'):raise AssertionError(issues)
        if name=='missing_actual_first_attempt' and len(verification.get('first_attempt_plan',[]))!=48:
            raise AssertionError('frozen first-attempt slots were lost')
        results.append(dict(case=name,passed=True,expected_codes=sorted(expected),actual_codes=sorted(actual),
            ontology_status=verification['ontology']['status'] if verification and 'ontology' in verification else 'not_run',
            first_plan_slots=len(verification.get('first_attempt_plan',[])) if verification else 0))
        print(json.dumps(dict(completed_case=name,passed=True),ensure_ascii=False),flush=True)
    summary=dict(status='passed',cases=results,data_origin='synthetic_simulation',domain_validity='pending',production_writeback=False,
                 ontology_engine='HermiT',real_hardware_observations=False)
    save(output/'audit.json',summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,required=True)
    run(parser.parse_args().output_dir)


if __name__=='__main__':main()
