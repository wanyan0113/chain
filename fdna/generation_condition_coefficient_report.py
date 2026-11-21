"""Terminal R task/environment coefficient sensitivity report."""
import argparse
from pathlib import Path

from source_data.common import read_json, write_outputs
from .generation_support_report import build_report as build_factor_report


def build_report(*args):
    return build_factor_report(*args,condition=True)


def main():
    p=argparse.ArgumentParser(description='末端R条件函数系数的配对敏感性汇报')
    for flag in ('experiment-dir','reference-dir'):p.add_argument('--'+flag,type=Path,required=True)
    a=p.parse_args()
    def load(d):return [read_json(d/f) for f in ('generation/generation_report.json','base/fit_report.json',
        'comparison/delta_comparison_report.json','comparison/delta_repeats_report.json')]
    write_outputs(a.experiment_dir/'report',build_report(*load(a.experiment_dir),*load(a.reference_dir)))


if __name__=='__main__':main()
