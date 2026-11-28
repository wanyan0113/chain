"""Exercise bound study freeze/assessment using explicitly synthetic observations."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path

from task_flow.joint import run_joint
from workflow_tools.verify_joint_optimization import inputs
from workflow_tools.benchmark_joint import save
from workflow_tools.design_observation_bridge import export
from workflow_tools.collection_study import main as cli
from tests.test_collection_study import prepare_fixture,write_fixture


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv);output=args.output_dir.resolve()
    if output.exists():raise ValueError('choose a new output directory')
    output.mkdir(parents=True)
    source=run_joint(*inputs());encoded=json.dumps(source,ensure_ascii=False,indent=2).encode('utf-8')
    pack=output/'fixture-pack';export(encoded,pack,task_ids=['T2']);p=prepare_fixture(pack)
    lock=output/'collection-lock.json'
    with redirect_stdout(io.StringIO()):
        if cli(['freeze','--pack',str(pack),'--lock',str(lock)])!=0:raise AssertionError('freeze failed')
        code=cli(['analyze','--pack',str(pack),'--lock',str(lock),'--output-dir',str(output/'empty-data')])
    empty=json.loads((output/'empty-data/collection_study_report.json').read_text(encoding='utf-8'))
    if code!=2 or 'no_observations' not in empty['blockers']:raise AssertionError('missing data not blocked')
    write_fixture(pack,p)
    with redirect_stdout(io.StringIO()):
        code=cli(['analyze','--pack',str(pack),'--lock',str(lock),'--output-dir',str(output/'labelled-fixture')])
    result=json.loads((output/'labelled-fixture/collection_study_report.json').read_text(encoding='utf-8'))
    if code or result['status']!='analyzed' or len(result['linked_candidates'][0]['stage_bindings'])!=4:
        raise AssertionError('linked fixture not analyzed')
    if result['domain_validity']!='pending' or result['production_accuracy_updated']:raise AssertionError('fixture promoted to domain/model update')
    p['acceptance']['absolute_error_tolerance']=.2;save(pack/'protocol.json',p)
    with redirect_stdout(io.StringIO()):
        code=cli(['analyze','--pack',str(pack),'--lock',str(lock),'--output-dir',str(output/'changed-protocol')])
    changed=json.loads((output/'changed-protocol/collection_study_report.json').read_text(encoding='utf-8'))
    if code!=2 or 'protocol_changed_since_collection_freeze' not in changed['blockers']:
        raise AssertionError('changed threshold accepted')
    save(output/'verification.json',dict(status='passed',fixtures_are_synthetic=True,real_data_available=False,
        domain_validity='pending',cases=[dict(case='empty_data_blocked',passed=True),
        dict(case='actual_label_format_linked_to_four_stage_candidate',passed=True),
        dict(case='frozen_threshold_change_blocks_statistics',passed=True)]))
    print(json.dumps(dict(status='passed',cases=3,domain_validity='pending',output=str(output)),ensure_ascii=False))


if __name__=='__main__':main()
