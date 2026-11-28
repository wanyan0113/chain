"""Run actual OWL designs through collection export and empty-data intake."""
import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path

from task_flow.joint import run_joint
from workflow_tools.verify_joint_optimization import inputs
from workflow_tools.benchmark_joint import save
from workflow_tools.design_observation_bridge import main as bridge_cli,check_pack
from workflow_tools.validate_accuracy_observations import main as intake_cli


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv);output=args.output_dir.resolve()
    if output.exists():raise ValueError('choose a new output directory')
    output.mkdir(parents=True)
    jobs,scene,mapping=inputs();report=run_joint(jobs,scene,mapping)
    save(output/'joint_report.json',report);results=[]
    for name,tasks in [('all_selected',[]),('task_T2',['--task-id','T2'])]:
        pack=output/name
        with redirect_stdout(io.StringIO()):
            code=bridge_cli(['--joint-report',str(output/'joint_report.json'),'--output-dir',str(pack),*tasks])
        if code:raise AssertionError('export failed')
        check=check_pack(pack)
        expected={'T1':3,'T2':4,'T3':4} if not tasks else {'T2':4}
        if check['stage_counts']!=expected:raise AssertionError('stage sequence was lost')
        save(output/(name+'-binding-check.json'),check)
        with redirect_stdout(io.StringIO()):
            code=intake_cli(['--protocol',str(pack/'protocol.json'),'--observations',str(pack/'observations.csv'),
                             '--output-dir',str(output/(name+'-intake'))])
        intake=json.loads((output/(name+'-intake')/'observation_report.json').read_text(encoding='utf-8'))
        if code!=2 or 'no_observations' not in intake['blockers'] or intake['domain_validity']!='pending':
            raise AssertionError('planning packet became observed data')
        results.append(dict(case=name,passed=True,stage_counts=expected,binding_status='passed',intake_status='blocked_no_observations'))
    limited=deepcopy(scene);limited['policies']['max_network_search_nodes']=8
    incumbent=run_joint(jobs,limited,mapping);save(output/'incumbent_joint_report.json',incumbent)
    with redirect_stdout(io.StringIO()):
        bridge_cli(['--joint-report',str(output/'incumbent_joint_report.json'),'--task-id','T2','--output-dir',str(output/'incumbent_T2')])
    check=check_pack(output/'incumbent_T2')
    if check['source_global_optimality']!='not_proven':raise AssertionError('incumbent was upgraded')
    results.append(dict(case='incumbent_preserves_not_proven',passed=True))
    bad=deepcopy(report);cid=bad['selected_candidate_ids'][0];bad['candidates'][cid]['chain']['metrics']['accuracy']=.01
    bad['verification']={'status':'passed'};save(output/'tampered_report.json',bad)
    try:
        with redirect_stdout(io.StringIO()):
            bridge_cli(['--joint-report',str(output/'tampered_report.json'),'--output-dir',str(output/'invalid_export')])
    except ValueError:pass
    else:raise AssertionError('tampered report was accepted')
    if (output/'invalid_export').exists():raise AssertionError('invalid export directory was created')
    results.append(dict(case='tampered_passed_flag_rejected',passed=True))
    save(output/'verification.json',dict(status='passed',source='actual HermiT on synthetic design fixture; no real measurements',
         domain_validity='pending',cases=results,real_observations_generated=False))
    print(json.dumps(dict(status='passed',cases=len(results),stage_counts={'T1':3,'T2':4,'T3':4},
                         domain_validity='pending',output=str(output)),ensure_ascii=False))


if __name__=='__main__':main()
