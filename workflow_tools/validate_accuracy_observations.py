"""Freeze a natural-observation protocol, or analyze labelled CSV observations."""
import argparse
from pathlib import Path

from workflow_tools.accuracy_observations import analyze,read_json,freeze_protocol
from workflow_tools.benchmark_joint import save


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol',type=Path,required=True)
    parser.add_argument('--freeze-lock',type=Path)
    parser.add_argument('--lock',type=Path)
    parser.add_argument('--observations',type=Path)
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args(argv)
    protocol=read_json(args.protocol)
    if args.freeze_lock:
        if args.lock or args.observations or args.output_dir:raise ValueError('freeze separately from analysis')
        freeze_protocol(protocol,args.freeze_lock);print('protocol lock written');return 0
    if not args.observations or not args.output_dir:raise ValueError('observations and output directory required')
    if args.output_dir.exists():raise ValueError('choose a new output directory')
    lock=read_json(args.lock) if args.lock else None
    snapshot=args.observations.read_bytes()
    report=analyze(protocol,args.observations,lock,observations_bytes=snapshot)
    save(args.output_dir/'observation_report.json',report)
    save(args.output_dir/'protocol_snapshot.json',protocol)
    if lock:save(args.output_dir/'lock_snapshot.json',lock)
    (args.output_dir/'observations_snapshot.csv').write_bytes(snapshot)
    print(report['data_readiness']+'; domain_validity=pending')
    return 0 if not report['blockers'] else 2


if __name__=='__main__':raise SystemExit(main())
