"""Fictional stabilization workflow experiment; no resource/legacy integration."""
from copy import deepcopy
import argparse
import hashlib
from itertools import count
import json
from pathlib import Path

from task_flow.contracts import ContractError, canonical_sha256, prepare_problem, read_json, require
from task_flow.planner import SearchSession
from task_flow.validator import verify_candidate, verify_sequence
from tests.astral_rift_reference import enumerate_reference, key, replay

ROOT=Path(__file__).resolve().parents[1]
EXAMPLES=ROOT/'task_flow/examples/astral_rift'
SCENARIO=EXAMPLES/'scenario.json'


def build(scenario, case):
    require(scenario['format_version']=='astral-rift-1.0' and scenario['object_id']=='rift-01','unsupported scenario')
    definitions=scenario['activity_definitions']
    require(len(definitions)==8 and {a['id'] for a in definitions}=={'S1','S2','S3','D1','D2','D3','A1','A2'},'expected eight unique activities')
    initial=deepcopy(scenario['initial_values'])
    require(set(case['initial_overrides'])<=initial.keys(),'unknown initial override')
    initial.update(case['initial_overrides'])
    require(type(case['goal_facts']) is list and bool(case['goal_facts']) and
            set(case['goal_facts'])<=initial.keys()-{'elapsed_seconds'},'expected nonempty declared Boolean goals')
    require(type(initial['elapsed_seconds']) is int and initial['elapsed_seconds']==0,'new-work delay must initially be zero')
    require(all(type(v) is bool or v is None for k,v in initial.items() if k!='elapsed_seconds'),'invalid Boolean initial state')
    for child,parent in scenario['history_dependencies']:
        require(not(initial[child] is True and initial[parent] is False),f'inconsistent initial history: {child} requires {parent}')
    refs=scenario['source_refs']
    arg=[{'param':'rift'}]

    def test(name, value=True, op='eq', *, bound=False):
        item={'test':{'fact':{'predicate':name,'args':['rift-01'] if bound else arg},'op':op,'value':value}}
        if name=='elapsed_seconds':
            item['test']['unit']='second'
        return item

    facts=[{'id':name,'arguments':['rift'],'kind':'static' if name in ['rift_exists','can_stabilize'] else 'mutable',
            'type':'boolean','source_refs':refs} for name in initial if name!='elapsed_seconds']
    facts.append({'id':'elapsed_seconds','arguments':['rift'],'kind':'mutable','type':'integer',
                  'minimum':0,'maximum':60,'unit':'second','source_refs':refs})
    activities=[]
    for a in definitions:
        require(type(a['delay_seconds']) is int and 1<=a['delay_seconds']<=60,'invalid synthetic delay')
        activities.append({'id':a['id'],'version':'1.0','type':a['type'],'parameters':{'rift':'rift'},
            'applicable_when':{'all':[]},'preconditions':{'all':[test(p) for p in a['requires']]},
            'effects':[{'fact':{'predicate':a['produces'],'args':arg},'set':{'value':True}},
                       {'fact':{'predicate':'elapsed_seconds','args':arg},'set':{'read':{'predicate':'elapsed_seconds','args':arg},'offset':a['delay_seconds'],'unit':'second'}}],
            'required_capabilities':['urn:astral-demo:'+a['id']],'max_occurrences':1,'source_refs':refs})
    catalog={'format_version':'task-flow-1.0','catalog_id':'astral-rift','version':'1.0','objects':{'rift':['rift-01']},
             'facts':facts,'derived_rules':[],'activities':activities,
             'capabilities':[{'uri':'urn:astral-demo:'+a['id'],'mapping_status':'unmapped','source_refs':refs} for a in definitions],
             'source_refs':refs}
    static=[]
    mutable=[]
    for name,value in initial.items():
        item={'fact':{'predicate':name,'args':['rift-01']},'value':value}
        if name=='elapsed_seconds':
            item['unit']='second'
        (static if name in ['rift_exists','can_stabilize'] else mutable).append(item)
    forbidden=[{'all':[test(c,bound=True),{'not':test(p,bound=True)}]} for c,p in scenario['history_dependencies']]
    budget=case['delay_budget_seconds']
    if budget is not None:
        require(type(budget) is int and 0<=budget<=60,'invalid business delay budget')
        forbidden.append(test('elapsed_seconds',budget,'gt',bound=True))
    search=dict(scenario['search_defaults'],mode='first_feasible')
    require(set(case['search_overrides'])<=set(scenario['search_defaults']),'unsupported search override')
    search.update(case['search_overrides'])
    require(type(search['max_length']) is int and 0<=search['max_length']<=8,'length outside experiment bounds')
    require(type(search['max_nodes']) is int and 1<=search['max_nodes']<=20000,'node budget outside experiment bounds')
    require(type(search['timeout_ms']) is int and 1<=search['timeout_ms']<=30000,'time budget outside experiment bounds')
    request={'format_version':'task-flow-1.0','request_id':case['id'],'catalog_id':catalog['catalog_id'],
             'catalog_version':catalog['version'],'catalog_sha256':canonical_sha256(catalog),
             'static_facts':static,'initial_state':mutable,'ontology_evidence':[],
             'goal':{'all':[test(f,bound=True) for f in case['goal_facts']]},'required_activities':[],
             'forbidden_conditions':forbidden,'activity_limits':{},'search':search,'source_refs':refs}
    prepare_problem(catalog,request)
    return catalog,request,initial


def counter(records):
    return next(r['value'] for r in records if r['fact']['predicate']=='elapsed_seconds')


def generation_case(scenario, case):
    record={'case':case,'scenario_sha256':canonical_sha256(scenario),'clock_kind':case['clock_kind']}
    try:
        catalog,request,initial=build(scenario,case)
    except ContractError as error:
        actual={'search_status':'input_rejected','conclusion':'not_run','candidate_count':0}
        record['failure']={'code':error.code,'reason':str(error)}
    else:
        clock=None if case['clock_kind']=='monotonic' else count().__next__
        require(case['clock_kind'] in ['monotonic','deterministic_test_clock'],'unknown clock kind')
        search=SearchSession(prepare_problem(catalog,request),clock=clock).run()
        checks=[verify_candidate(catalog,request,c) for c in search['candidates']]
        domain=[replay(initial,case['goal_facts'],c['sequence']['steps'],delay_budget=case['delay_budget_seconds'],max_length=request['search']['max_length']) for c in search['candidates']]
        actual={'search_status':search['search_status'],'termination_reason':search['termination_reason'],
                'conclusion':search['conclusion'],'candidate_count':len(search['candidates']),
                'first_steps':[s['activity_id'] for s in search['candidates'][0]['sequence']['steps']] if search['candidates'] else None,
                'first_delay_seconds':counter(checks[0]['final_state']) if checks else None,
                'verification_status':checks[0]['verification_status'] if checks else 'not_run'}
        record.update(catalog=catalog,request=request,initial_values=initial,search=search,verifications=checks,independent_domain_checks=domain)
    passed=all(type(actual[k]) is type(v) and actual[k]==v for k,v in case['expected'].items())
    if 'search' in record:
        passed=passed and all(c['verification_status']=='passed' for c in checks) and all(c['passed'] for c in domain)
        passed=passed and all(counter(c['final_state'])==d['delay_seconds'] for c,d in zip(checks,domain))
    record.update(actual=actual,expectation_met=passed)
    return record


def sequence(catalog,request,names):
    return {'format_version':'task-flow-1.0','catalog_sha256':canonical_sha256(catalog),'request_sha256':canonical_sha256(request),
            'steps':[{'activity_id':name,'activity_version':'1.0','parameters':{'rift':'rift-01'}} for name in names]}


def verification_probes(scenario):
    cases={c['id']:c for c in scenario['cases']}
    plans=[('missing_observation','T1',['S2'],0),('missing_plan','T3',['S1','S2','A1'],2),
           ('permission_false','permission_false',['S1','S2','D1','D2','A1','A2'],4),
           ('missing_stabilization','T3',['S1','S2','D1','D2','A2'],4),('repeated_activity','T1',['S1','S1','S2'],None),
           ('invalid_parameter','T1',['S1','S2'],None),('valid_optional_extension','T1',['S1','S2','S3','D3'],None)]
    records=[]
    for name,case_id,names,index in plans:
        catalog,request,initial=build(scenario,cases[case_id])
        seq=sequence(catalog,request,names)
        if name=='invalid_parameter':
            seq['steps'][0]['parameters']['rift']='rift-other'
        report=verify_sequence(catalog,request,seq)
        expected='passed' if name=='valid_optional_extension' else 'rejected'
        passed=report['verification_status']==expected and (index is None or report['failure']['step_index']==index)
        records.append({'id':name,'catalog':catalog,'request':request,'sequence':seq,'expected_status':expected,
                        'expected_failure_step':index,'verification':report,'expectation_met':passed})
    catalog,request,_=build(scenario,cases['T3'])
    candidate=SearchSession(prepare_problem(catalog,request)).run()['candidates'][0]
    candidate['final_state']=[]
    report=verify_candidate(catalog,request,candidate)
    records.append({'id':'tampered_final_state','catalog':catalog,'request':request,'candidate':candidate,
                    'expected_status':'rejected','verification':report,
                    'expectation_met':report['verification_status']=='rejected' and report['failure']['code']=='claimed_final_state'})
    return records


def enumeration_case(scenario, case):
    catalog,request,initial=build(scenario,case)
    request['search']['mode']='enumerate_bounded'
    reference=enumerate_reference(initial,case['goal_facts'])
    search=SearchSession(prepare_problem(catalog,request)).run()
    actual={key(c['sequence']['steps']) for c in search['candidates']}
    checks=[verify_candidate(catalog,request,c) for c in search['candidates']]
    delays_match=all(counter(v['final_state'])==reference['delays'].get(key(c['sequence']['steps'])) for c,v in zip(search['candidates'],checks))
    return {'id':case['id'],'raw_sequences_checked':reference['raw_sequences_checked'],
            'reference_sequences':sorted(reference['accepted']),'actual_sequences':sorted(actual),
            'reference_min_length':min(len(s) for s in reference['accepted']),
            'reference_min_delay_seconds':min(reference['delays'].values()),'search':search,'verifications':checks,
            'expectation_met':search['coverage']=='complete' and actual==reference['accepted'] and
                len(actual)==len(search['candidates']) and all(v['verification_status']=='passed' for v in checks) and delays_match}


def _write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8',newline='\n')


def _sources():
    return sorted([*ROOT.joinpath('task_flow').glob('*.py'),ROOT/'task_flow/schema.json',*EXAMPLES.rglob('*.json'),
        Path(__file__).resolve(),ROOT/'tests/astral_rift_reference.py',ROOT/'tests/test_astral_rift.py',ROOT/'docs/task-flow/astral-rift/README.md'])


def run(output):
    require(not output.exists(),'choose a new experiment directory')
    scenario=read_json(SCENARIO)
    require(len(scenario['cases'])==17 and len({c['id'] for c in scenario['cases']})==17,'expected seventeen distinct cases')
    output.mkdir(parents=True)
    _write(output/'scenario.json',scenario)
    generation=[generation_case(scenario,c) for c in scenario['cases']]
    probes=verification_probes(scenario)
    enumeration=[enumeration_case(scenario,c) for c in scenario['cases'] if c['id'] in ['T1','T2','T3']]
    for record in generation:
        _write(output/('generation-'+record['case']['id']+'.json'),record)
    _write(output/'verification-probes.json',probes)
    for record in enumeration:
        _write(output/('enumeration-'+record['id']+'.json'),record)
    summary={'status':'expectations_met_with_scope_limits' if all(c['expectation_met'] for c in [*generation,*probes,*enumeration]) else 'failed',
             'scenario_sha256':canonical_sha256(scenario),'generation_cases':len(generation),
             'cases_with_candidates':sum(c['actual']['candidate_count']>0 for c in generation),
             'verification_probes':len(probes),'enumeration_tasks':len(enumeration),
             'raw_reference_sequences_checked':sum(c['raw_sequences_checked'] for c in enumeration),
             'accepted_sequences':{c['id']:len(c['actual_sequences']) for c in enumeration},
             'legacy_integration_status':'not_run','external_execution_status':'not_run'}
    _write(output/'summary.json',summary)
    manifest={'source_sha256_lf_normalized':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in _sources()},
              'artifact_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob('*.json'))}}
    _write(output/'manifest.json',manifest)
    return summary


def verify(output):
    manifest=read_json(output/'manifest.json')
    require(set(manifest['artifact_sha256'])=={p.name for p in output.glob('*.json')} - {'manifest.json'},'artifact inventory changed')
    require(set(manifest['source_sha256_lf_normalized'])=={p.relative_to(ROOT).as_posix() for p in _sources()} and len(manifest['artifact_sha256'])==23,'incomplete evidence inventory')
    for name,digest in manifest['source_sha256_lf_normalized'].items():
        require(hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest,'source changed: '+name)
    for name,digest in manifest['artifact_sha256'].items():
        require(hashlib.sha256((output/name).read_bytes()).hexdigest()==digest,'artifact changed: '+name)
    summary=read_json(output/'summary.json')
    require(summary['status']=='expectations_met_with_scope_limits','experiment contains failed expectations')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['run','verify'])
    parser.add_argument('--output-dir',required=True,type=Path)
    args=parser.parse_args()
    try:
        result=run(args.output_dir) if args.command=='run' else verify(args.output_dir)
        print(json.dumps(result,ensure_ascii=False))
        return 1 if result['status']=='failed' else 0
    except (ContractError,OSError) as error:
        print(json.dumps({'status':'invalid_experiment','reason':str(error)},ensure_ascii=False))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
