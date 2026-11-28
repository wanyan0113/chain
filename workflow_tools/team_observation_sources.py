"""Observation collection must not require a pre-existing scored quality model."""


def source_payload(report):
    if report.get('format_version')=='task-flow-team-feasibility-1.0':return report
    if report.get('format_version') in ('task-flow-team-optimization-1.0','task-flow-team-optimization-1.1'):return report['source_report']
    raise ValueError('unsupported team chain source; legacy observation files are not team sources')
