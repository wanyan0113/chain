"""Run frozen-design acceptance cases with actual HermiT and an independent oracle."""
import argparse
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
import io
import json
from pathlib import Path
import uuid

from task_flow.design_bridge import main as bridge_cli
from task_flow.joint import run_joint,export_joint
from task_flow.joint_network import optimize
from tests.joint_reference import audit_joint

ROOT=Path(__file__).resolve().parents[1]
EXAMPLE=ROOT/'task_flow/examples/joint_optimization'


def require(value,message):
    if not value:raise RuntimeError(message)


def inputs():
    read=lambda name:json.loads((EXAMPLE/name).read_text(encoding='utf-8'))
    return [dict(task_id=f'T{i}',catalog=read(f'catalog-{i}.json'),request=read(f'request-{i}.json')) for i in range(1,4)],read('scene.json'),read('mapping.json')


def score_quick(quick,joint):
    attempt=quick['attempts'][quick['selected_attempt']]
    ids=[]
    for task in attempt['design_report']['tasks']:
        chain=next(c for c in task['chains'] if c['id'] in attempt['design_report']['network']['chains'])
        instances=next(t['instances'] for t in attempt['compilation']['tasks'] if t['task_id']==task['task'])
        matches=[cid for cid,c in joint['candidates'].items() if c['task_id']==task['task'] and
                 c['identity_payload']['activity_instance_sequence']==instances and
                 [s['artifact'] for s in c['chain']['plan']['steps']]==[s['artifact'] for s in chain['plan']['steps']]]
        require(len(matches)==1,'quick solution not found in common joint candidate pool')
        ids.extend(matches)
    return dict(candidate_ids=ids,evaluation_context_hash=joint['evaluation']['evaluation_context_hash'],
                score=sum(joint['evaluation']['candidates'][cid]['score'] for cid in ids)/len(ids))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args(argv)
    output=args.output_dir or ROOT/'generated/task-flow'/('joint-verified-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    require(not output.exists(),'choose a new output directory')
    jobs,scene,mapping=inputs()
    results=[]
    # Actual public CLI, not a replacement mock optimizer.
    with redirect_stdout(io.StringIO()):
        code=bridge_cli(['--mode','joint_candidate_optimization','--jobs',str(EXAMPLE/'jobs.json'),
            '--scene',str(EXAMPLE/'scene.json'),'--mapping',str(EXAMPLE/'mapping.json'),'--output-dir',str(output/'full')])
    require(code==0,'full joint CLI failed')
    full=json.loads((output/'full/joint_report.json').read_text(encoding='utf-8'))
    require(full['global_optimality']=='bounded_global_optimum','complete proof missing')
    oracle=audit_joint(full)
    require(oracle['raw_network_combinations']==96 and oracle['feasible_network_combinations']==32,'independent enumeration mismatch')
    results.append(dict(case='complete_joint',actual=full['global_optimality'],passed=True))
    with redirect_stdout(io.StringIO()):
        code=bridge_cli(['--jobs',str(EXAMPLE/'jobs.json'),'--scene',str(EXAMPLE/'scene.json'),
            '--mapping',str(EXAMPLE/'mapping.json'),'--output-dir',str(output/'quick')])
    require(code==0,'quick CLI failed')
    quick=json.loads((output/'quick/bridge_report.json').read_text(encoding='utf-8'))
    baseline=score_quick(quick,full)
    require(full['design_report']['network']['score']>baseline['score'],'joint improvement not established on common basis')
    results.append(dict(case='quick_vs_joint_common_basis',quick_score=baseline['score'],
                        joint_score=full['design_report']['network']['score'],
                        evaluation_context_hash=baseline['evaluation_context_hash'],passed=True))
    for name,expected in [('partial_flows','candidate_set_optimum'),('partial_chains','candidate_set_optimum'),
                          ('incumbent','not_proven'),('no_stock','bounded_global_infeasible'),('missing_metrics','not_proven')]:
        variant=deepcopy(scene);kwargs={}
        if name=='partial_flows':kwargs['max_flows_per_task']=1
        elif name=='partial_chains':variant['policies']['max_chain_combinations']=1
        elif name=='incumbent':
            groups=[[{**deepcopy(full['candidates'][cid]['chain']),**full['evaluation']['candidates'][cid]}
                     for cid in pool['candidate_ids']] for pool in full['frozen_candidate_sets']['sets']]
            roles={a['artifact']:a['role'] for a in full['design_report']['ontology_inference']['artifacts']}
            for limit in range(1,100):
                prepared=deepcopy(scene);prepared['policies']['max_network_search_nodes']=limit
                for artifact in prepared['artifacts']:artifact['role']=roles[artifact['id']]
                best,stats=optimize(groups,prepared,mapping)
                if best is not None and not stats['search_complete']:break
            require(best is not None and not stats['search_complete'],'incumbent fixture budget not found')
            variant['policies']['max_network_search_nodes']=limit
        elif name=='no_stock':
            for artifact in variant['artifacts']:
                if artifact['id'].startswith('R-Seal-'):artifact['inventory']=0
        else:next(a for a in variant['artifacts'] if a['id']=='R-Analysis')['activity_metrics']['decide']=None
        report=run_joint(jobs,variant,mapping,**kwargs)
        require(report['global_optimality']==expected,(name,report['global_optimality']))
        export_joint(report,output/name)
        if name=='incumbent':
            require((output/name/'incumbent.ttl').exists() and not (output/name/'design.ttl').exists(),'incumbent export mislabeled')
        if name=='missing_metrics':require(report['integration_status']=='blocked','missing metric not blocked')
        results.append(dict(case=name,actual=report['global_optimality'],status=report['integration_status'],passed=True))
    repeat=run_joint(jobs,scene,mapping)
    require(repeat['candidate_set_hash']==full['candidate_set_hash'] and
            repeat['selected_candidate_ids']==full['selected_candidate_ids'] and repeat['run_id']!=full['run_id'],
            'repeat identity/determinism failed')
    require(not set(e['candidate_evidence_id'] for e in repeat['candidate_evidence']) &
            set(e['candidate_evidence_id'] for e in full['candidate_evidence']),'run evidence merged across executions')
    export_joint(repeat,output/'repeat')
    results.append(dict(case='repeat_identity_independent_evidence',passed=True))
    summary=dict(verification='passed',baseline='TFDO-DESIGN-1.0',output=str(output),
        domain_validity='pending',independent_oracle=oracle,cases=results)
    (output/'verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
