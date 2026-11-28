"""One observation command, explicit version routing, unchanged legacy handlers."""
import argparse
from contextlib import redirect_stdout
import csv
import io
from pathlib import Path
import tempfile

from .accuracy_observations import read_json
from .team_observation_intake import save

V1 = 'tfdo-observation-intake-1.0'
V2 = 'tfdo-observation-study-2.0'
TEAM = 'team-observation-contract-1.0'
VERSIONS = (V1, V2, TEAM)
OPTIONS = ('protocol', 'observations', 'joint_report', 'lock', 'sources', 'expected_receipt_hash')


class EntryError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def verification_input(path):
    if not path.is_dir():
        raise EntryError('VERIFY_INPUT_DIRECTORY', 'verify requires an existing team intake directory')
    if (path / 'entry-report.json').exists():
        wrapper = read_json(path / 'entry-report.json')
        if ((path / 'receipt.json').exists() or type(wrapper) is not dict or
                wrapper.get('version') != 'observation-entry-1.0' or
                wrapper.get('input_version') != TEAM or wrapper.get('operation') != 'import'):
            raise EntryError('UNSUPPORTED_VERIFICATION_FORMAT', 'independent replay requires a team import result')
        return path / 'backend'
    return path


def detect(path, operation, expected=None):
    """Read the discriminator only; never infer a format from equipment count."""
    if operation == 'verify':
        receipt = read_json(verification_input(path) / 'receipt.json')
        if type(receipt) is not dict or receipt.get('version') != 'team-observation-receipt-1.0':
            raise EntryError('UNSUPPORTED_VERIFICATION_FORMAT', 'only team receipt 1.0 supports this independent replay')
        version = TEAM
    else:
        obj = read_json(path)
        if type(obj) is not dict:
            raise EntryError('INPUT_OBJECT', 'expected a versioned JSON object')
        if 'version' in obj and 'contract_version' in obj:
            raise EntryError('AMBIGUOUS_VERSION', 'use the native version field; mixed discriminators are not accepted')
        version = obj.get('contract_version', obj.get('version'))
        if version is None:
            raise EntryError('MISSING_VERSION', 'an explicit version is required; no automatic legacy fallback')
        if type(version) is not str or version not in VERSIONS:
            raise EntryError('UNSUPPORTED_VERSION', 'supported versions: ' + ', '.join(VERSIONS))
        if (version == TEAM) != ('contract_version' in obj):
            raise EntryError('VERSION_FIELD_CONFLICT', 'version is in the wrong discriminator field')
        foreign = ({'enrollments', 'observations', 'endpoints', 'version'} if version == TEAM else
                   {'contract_version', 'table_files', 'samples', 'executions', 'stage_attempts',
                    'member_observations', 'group_outputs', 'task_endpoints'})
        if version == V1:
            foreign |= {'enrollments', 'observations', 'endpoints', 'protocol'}
        if foreign.intersection(obj):
            raise EntryError('MIXED_FORMAT', 'input contains fields from a different observation family')
    if expected is not None and expected != version:
        raise EntryError('VERSION_MISMATCH', 'requested version disagrees with input; conversion is not automatic')
    return version


def route_options(version, operation, options):
    if operation == 'verify':
        required, allowed = set(), {'expected_receipt_hash'}
    elif operation == 'preflight':
        if version != TEAM:
            raise EntryError('UNSUPPORTED_OPERATION', 'preflight is available only for team contract 1.0')
        required = allowed = {'protocol', 'sources'}
    elif version == TEAM:
        required = {'protocol', 'sources'} | ({'lock'} if operation == 'import' else set())
        allowed = required | {'lock'}
    elif version == V2:
        required = {'joint_report'} | ({'lock'} if operation == 'import' else set())
        allowed = required
    else:
        required = {'observations'} if operation == 'import' else set()
        allowed = required | {'observations'} | ({'lock'} if operation == 'import' else set())
    present = {k for k, v in options.items() if v is not None}
    if present - allowed:
        raise EntryError('UNEXPECTED_OPTION', 'unsupported options: ' + ', '.join(sorted(present - allowed)))
    if required - present:
        raise EntryError('MISSING_OPTION', 'required options: ' + ', '.join(sorted(required - present)))


def team_load(path, options):
    from .team_observation_io import load_packet
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / 'not-a-study-lock.json'
        empty.write_text('{}', encoding='utf-8')
        return load_packet(path, options['protocol'], options.get('lock') or empty, options['sources'])


def dispatch(version, operation, path, options, backend):
    """Return status, diagnostics, report filename, scope; backend artifacts stay native."""
    if operation == 'verify':
        from .team_observation_verify import verify_intake
        result = verify_intake(verification_input(path), expected_receipt_hash=options.get('expected_receipt_hash'))
        backend.mkdir()
        save(backend / 'verification.json', result)
        return result['status'] == 'passed', result.get('issues', []), 'verification.json', 'independent_raw_replay'
    if operation == 'preflight':
        from .team_collection_preflight import run
        result = run(path, options['protocol'], options['sources'], backend / 'preflight.json')
        return result['status'] == 'registration_software_checks_passed', result.get('issues', []), 'preflight.json', 'registration_not_collection_authorization'
    if operation == 'check':
        if version == TEAM:
            _, _, _, _, _, issues = team_load(path, options)
            scope = 'dataset_protocol_schema_and_csv_format_only_not_semantic_or_lock_verification'
        elif version == V2:
            from .observations_v2 import validate_study
            validate_study(read_json(path), read_json(options['joint_report']))
            issues, scope = [], 'study_structure_and_source_definitions_not_observation_row_semantics'
        else:
            from .accuracy_observations import validate_protocol, FIELDS
            validate_protocol(read_json(path))
            issues, scope = [], 'protocol_only'
            if options.get('observations'):
                with options['observations'].open(encoding='utf-8-sig', newline='') as handle:
                    rows = csv.DictReader(handle)
                    if rows.fieldnames != FIELDS:
                        raise EntryError('CSV_HEADER', 'legacy observation header differs from its native format')
                    for i, row in enumerate(rows, 1):
                        if None in row or any(v is None for v in row.values()):
                            raise EntryError('CSV_COLUMN_COUNT', 'legacy CSV column count at data row ' + str(i))
                scope = 'protocol_and_csv_header_column_counts_not_row_semantics'
        backend.mkdir()
        save(backend / 'format-report.json', dict(status='blocked' if issues else 'format_checked', scope=scope, issues=issues))
        return not issues, issues, 'format-report.json', scope
    if version == TEAM:
        from .team_observation_intake import run
        result = run(path, options['protocol'], options['lock'], options['sources'], backend)
        passed = result['status'] == 'imported_structure_only' and result['semantic_verification'] == 'passed'
        issues = list(result.get('issues', []))
        if (backend / 'verification.json').exists():
            issues += read_json(backend / 'verification.json').get('issues', [])
        return passed, issues, 'intake_report.json', 'native_team_intake_and_independent_verification'
    if version == V2:
        from .observations_v2 import main
        args = ['analyze', '--study', str(path), '--joint-report', str(options['joint_report']),
                '--lock', str(options['lock']), '--output-dir', str(backend)]
        code = main(args)
        report = read_json(backend / 'report.json')
        return code == 0, [dict(code='NATIVE_BLOCKER', message=s) for s in report['blockers']], 'report.json', 'native_v2_assessment_not_independent_verification'
    from .validate_accuracy_observations import main
    args = ['--protocol', str(path), '--observations', str(options['observations']), '--output-dir', str(backend)]
    if options.get('lock'):
        args += ['--lock', str(options['lock'])]
    code = main(args)
    report = read_json(backend / 'observation_report.json')
    return code == 0, report.get('issues', []) + [dict(code='NATIVE_BLOCKER', message=s) for s in report['blockers']], 'observation_report.json', 'native_v1_assessment_not_independent_verification'


def run(operation, path, output, *, expected_version=None, **options):
    if output.exists():
        raise EntryError('OUTPUT_EXISTS', 'choose a new output directory; existing evidence is never overwritten')
    output.mkdir(parents=True)
    summary = dict(version='observation-entry-1.0', operation=operation, input_version=None,
                   status='blocked', diagnostics=[], backend_report=None, domain_validity='pending',
                   production_writeback=False)
    try:
        if operation not in ('check', 'import', 'verify', 'preflight'):
            raise EntryError('UNSUPPORTED_OPERATION', 'use check, import, verify or preflight')
        if set(options) - set(OPTIONS):
            raise EntryError('UNEXPECTED_OPTION', 'unknown routing options')
        version = detect(path, operation, expected_version)
        summary['input_version'] = version
        route_options(version, operation, options)
        with redirect_stdout(io.StringIO()):
            passed, issues, filename, scope = dispatch(version, operation, path, options, output / 'backend')
        summary.update(status='passed' if passed else 'blocked', diagnostics=issues,
                       backend_report='backend/' + filename, scope=scope)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        summary['diagnostics'] = [getattr(exc, 'issue', None) or
                                  dict(code=getattr(exc, 'code', 'BACKEND_INPUT_ERROR'), message=str(exc))]
    save(output / 'entry-report.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['check', 'import', 'verify', 'preflight'])
    parser.add_argument('--input', type=Path, required=True, help='v1 protocol, v2 study, team dataset; verify: intake directory')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--expect-version', choices=VERSIONS)
    for option in OPTIONS:
        parser.add_argument('--' + option.replace('_', '-'), type=str if option == 'expected_receipt_hash' else Path)
    args = parser.parse_args(argv)
    try:
        result = run(args.operation, args.input, args.output_dir, expected_version=args.expect_version,
                     **{key: getattr(args, key) for key in OPTIONS})
    except EntryError as exc:
        parser.exit(2, exc.code + ': ' + str(exc) + '\n')
    print(result['status'] + '; report=' + str(args.output_dir / 'entry-report.json'))
    return 0 if result['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
