"""Exercise real intake CLI with explicitly synthetic interface fixtures."""
import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil

from workflow_tools.benchmark_joint import ROOT,save
from workflow_tools.validate_accuracy_observations import main as cli
from tests.test_accuracy_observations import fixture_protocol,fixture_rows,write_rows


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv);output=args.output_dir.resolve()
    if output.exists():raise ValueError('choose a new output directory')
    output.mkdir(parents=True)
    protocol=fixture_protocol();save(output/'fixture-protocol.json',protocol)
    with redirect_stdout(io.StringIO()):
        code=cli(['--protocol',str(output/'fixture-protocol.json'),'--freeze-lock',str(output/'fixture-lock.json')])
    if code:raise AssertionError('fixture freeze failed')
    results=[]
    for name in ('empty_template','empty_observations','complete_small_fixture','missing_gold','not_executed',
                 'missing_stage','no_calibration','version_mismatch','cluster_leakage','unfrozen','protocol_changed'):
        directory=output/name;directory.mkdir()
        config=deepcopy(protocol);rows=fixture_rows();lock=output/'fixture-lock.json'
        expected='blocked';required=None
        if name=='empty_template':
            source=ROOT/'data/accuracy-observations/templates'
            config=json.loads((source/'protocol.json').read_text(encoding='utf-8'));rows=[];lock=None;required='no_real_data_collected'
        elif name=='empty_observations':rows=[];required='no_observations'
        elif name=='complete_small_fixture':expected='ready_for_statistical_assessment'
        elif name=='missing_gold':rows[-1]['gold_label']='';required='observation_integrity_failed'
        elif name=='not_executed':rows[-1].update(status='not_executed',output_label='');required='incomplete_observations'
        elif name=='missing_stage':rows.pop();required='incomplete_evaluation_flow:flow-A'
        elif name=='no_calibration':rows=[r for r in rows if r['split']=='evaluation'];required='insufficient_stage_calibration:flow-A'
        elif name=='version_mismatch':rows[-1]['artifact_version']='999';required='observation_integrity_failed'
        elif name=='cluster_leakage':
            for row in rows:
                if row['sample_id']=='evaluation-0':row['cluster_id']='calibration-0'
            required='observation_integrity_failed'
        elif name=='unfrozen':lock=None;required='protocol_not_frozen'
        else:config['acceptance']['absolute_error_tolerance']=.1;required='protocol_lock_mismatch'
        save(directory/'protocol.json',config);write_rows(directory/'observations.csv',rows)
        arguments=['--protocol',str(directory/'protocol.json'),'--observations',str(directory/'observations.csv'),
                   '--output-dir',str(directory/'report')]
        if lock:arguments.extend(['--lock',str(lock)])
        with redirect_stdout(io.StringIO()):code=cli(arguments)
        report=json.loads((directory/'report/observation_report.json').read_text(encoding='utf-8'))
        if code!=(0 if expected=='ready_for_statistical_assessment' else 2):raise AssertionError('wrong CLI exit status: '+name)
        if report['data_readiness']!=expected or (required and required not in report['blockers']):raise AssertionError('unexpected report: '+name)
        if report['domain_validity']!='pending' or report['domain_approval_automatic']:raise AssertionError('fixture became domain evidence')
        if name=='complete_small_fixture' and report['flows'][0]['statistical_assessment']['decision']!='inconclusive':
            raise AssertionError('small fixture should not prove equivalence')
        results.append(dict(case=name,passed=True,cli_exit=code,data_readiness=expected,blockers=report['blockers']))
    save(output/'verification.json',dict(status='passed',cases=results,fixtures_are_synthetic=True,
        domain_validity='pending',real_domain_data_available=False))
    print(json.dumps(dict(status='passed',cli_cases=len(results),domain_validity='pending',output=str(output)),ensure_ascii=False))


if __name__=='__main__':main()
