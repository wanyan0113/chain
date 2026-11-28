"""Generate auditable label operations, then validate held-out product predictions."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import platform
import random
import sys

from workflow_tools.accuracy_validation import VERSION,FIELDS,Evidence,load,summarize
from workflow_tools.benchmark_joint import ROOT,save


def case(identifier,group,mechanism,probabilities,**kwargs):
    length=len(probabilities)
    population=dict(id='balanced_binary_controlled_v1',hard_probability=.5 if mechanism=='shared_difficulty' else 0.)
    return dict(id=identifier,group=group,mechanism=mechanism,
        stages=[dict(id=f'step-{i+1}',version='1.0',activity_type=('decide' if group=='E' else 'observe' if i==0 else 'act' if i==length-1 else 'decide'),
                     artifact='repeated-operator' if group=='E' else f'operator-{i+1}',clean_pass_probability=p) for i,p in enumerate(probabilities)],
        population_calibration=population,population_evaluation=dict(population),expected_strata=['easy','hard'] if mechanism=='shared_difficulty' else ['all'],**kwargs)


def matrix():
    result=[case('A-observe-1','A','absorbing',[.9]),case('A-decide-1','A','absorbing',[.85]),case('A-act-1','A','absorbing',[.95]),
        case('B-independent-2','B','absorbing',[.9,.85])]
    result[1]['stages'][0]['activity_type']='decide';result[2]['stages'][0]['activity_type']='act'
    result += [case(f'C-independent-{n}','C','absorbing',[.9]+[.85]*(n-2)+[.95]) for n in (3,4,6)]
    result += [case(f'D-shared-difficulty-{n}','D','shared_difficulty',[.7]*n,easy_pass=.98,hard_pass=.42) for n in (2,4,6)]
    result += [case('D-independent-matched-4','D','absorbing',[.7]*4),case('D-exclusive-errors-2','D','exclusive',[.65]*2)]
    result += [case(f'E-repeated-{n}','E','repeated',[.9]*n) for n in (2,4,6)]
    result += [case('F-repair-2','F','repair',[.8,1.],repair_probability=.8)]
    result += [case(f'F-flip-{n}','F','flip',[.7]*n) for n in (2,4)]
    shifted=case('M-population-shift-4','M','shared_difficulty',[.7]*4,easy_pass=.98,hard_pass=.42)
    shifted['population_calibration']['hard_probability']=.1
    shifted['population_evaluation']['hard_probability']=.9
    result.append(shifted)
    return result


def fault_draw(spec,rng,stratum):
    probabilities=[s['clean_pass_probability'] for s in spec['stages']];mode=spec['mechanism']
    if mode=='repeated':return [rng.random()>=probabilities[0]]*len(probabilities)
    if mode=='exclusive':
        u=rng.random();return [u<.35,u>=.65]
    if mode=='shared_difficulty':return [rng.random()>=(spec['hard_pass'] if stratum=='hard' else spec['easy_pass']) for _ in probabilities]
    if mode=='repair':return [rng.random()>=probabilities[0],rng.random()>=spec['repair_probability']]
    return [rng.random()>=p for p in probabilities]


def operate(spec,index,value,gold,fault):
    mode=spec['mechanism']
    if mode=='repair' and index==1:return gold if value==gold or not fault else value
    if mode in ('flip','repair'):return 1-value if fault else value
    # Explicit abstention is absorbing; downstream stages never silently repair it.
    return -1 if value==-1 or fault else value


def rows(spec,seed,n_calibration,n_evaluation):
    for split,n in [('calibration',n_calibration),('evaluation',n_evaluation)]:
        number=int.from_bytes(hashlib.sha256(f"{spec['id']}:{seed}:{split}".encode()).digest(),'big')
        rng=random.Random(number)
        population=spec['population_'+split]
        for i in range(n):
            gold=rng.randrange(2)
            stratum=('hard' if rng.random()<population['hard_probability'] else 'easy') if spec['mechanism']=='shared_difficulty' else 'all'
            faults=fault_draw(spec,rng,stratum);shadow=[];serial=[];value=gold
            for index,fault in enumerate(faults):
                shadow.append(operate(spec,index,gold,gold,fault))
                if split=='evaluation':
                    value=operate(spec,index,value,gold,fault);serial.append(value)
            token=lambda values:''.join('x' if v==-1 else str(v) for v in values)
            yield dict(sample_id=f"{spec['id']}:{seed}:{split[0]}:{i}",split=split,stratum=stratum,gold=str(gold),
                fault_bits=''.join(str(int(f)) for f in faults),shadow_outputs=token(shadow),serial_outputs=token(serial))


def freeze(path):
    if path.exists():raise ValueError('protocol already exists')
    save(path,dict(version=VERSION,raw_policy='sequential_product_v2',interpretation='binary_correctness_with_abstention_v1',
        provenance='controlled_synthetic_labels_and_explicit_fault_injection_not_real_domain_observations',
        seeds=[1103,2207,3301,4409,5519],n_calibration_per_seed=5000,n_evaluation_per_seed=10000,
        acceptance=dict(alpha=.05,absolute_error_tolerance=.05,minimum_stratum_class_samples=100,
            primary_decision='pooled_within_identical_scenario; per-seed sensitivity reported',
            systematic_direction_rule='pooled interval outside tolerance and all five seeds have same error sign',
            interval_method='Wilson + Bonferroni simultaneous envelope for L calibration marginals and endpoint',
            multiplicity='per-scenario 95%; no family-wide confidence claim across scenarios',
            tolerance_scope='first controlled screening threshold, not a domain/business acceptance threshold'),
        cases=matrix(),software=dict(python=sys.version,platform=platform.platform()),
        source_hashes={p:hashlib.sha256((ROOT/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in
            ['workflow_tools/accuracy_validation.py','workflow_tools/validate_accuracy_model.py','task_flow/joint_candidates.py']}))


def execute(protocol_path,output):
    if output.exists():raise ValueError('choose a new output directory')
    protocol=json.loads(protocol_path.read_text(encoding='utf-8'))
    if protocol['version']!=VERSION:raise ValueError('unsupported protocol')
    for name,expected in protocol['source_hashes'].items():
        actual=hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
        if actual!=expected:raise ValueError('source changed after protocol freeze: '+name)
    output.mkdir(parents=True);save(output/'protocol.json',protocol);result=[];inventory=[]
    for spec in protocol['cases']:
        directory=output/spec['id'];directory.mkdir();pooled=Evidence(len(spec['stages']));repetitions=[]
        for seed in protocol['seeds']:
            path=directory/f'seed-{seed}.csv.gz'
            with path.open('wb') as raw:
                with gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as compressed:
                    with io.TextIOWrapper(compressed,encoding='utf-8',newline='') as text:
                        writer=csv.DictWriter(text,fieldnames=FIELDS,lineterminator='\n');writer.writeheader()
                        writer.writerows(rows(spec,seed,protocol['n_calibration_per_seed'],protocol['n_evaluation_per_seed']))
            data=load(path,len(spec['stages']));pooled.merge(data)
            repetitions.append(dict(seed=seed,result=summarize(data,spec,protocol['acceptance'])))
            inventory.append(dict(path=str(path.relative_to(output)),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bytes=path.stat().st_size,rows=sum(data.n.values())))
        summary=summarize(pooled,spec,protocol['acceptance'])
        signs=[r['result']['signed_error_observed_minus_predicted'] for r in repetitions]
        summary['all_seed_error_signs_agree']=all(e>0 for e in signs) or all(e<0 for e in signs)
        if summary['decision'].startswith('systematic_') and not summary['all_seed_error_signs_agree']:
            summary['decision']='inconclusive_direction_instability'
        result.append(dict(case=spec,pooled=summary,repetitions=repetitions))
        save(output/'results.json',dict(version=VERSION,cases=result,domain_validity='pending',
             real_domain_evidence=False,provenance=protocol['provenance']))
        print(spec['id'],summary['decision'],round(summary['prediction'],4),round(summary['observed'],4),flush=True)
    save(output/'raw-inventory.json',inventory)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-protocol',type=Path)
    parser.add_argument('--protocol',type=Path)
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args(argv)
    if args.freeze_protocol:return freeze(args.freeze_protocol)
    if not args.protocol or not args.output_dir:raise ValueError('protocol and output directory required')
    execute(args.protocol,args.output_dir)


if __name__=='__main__':main()
