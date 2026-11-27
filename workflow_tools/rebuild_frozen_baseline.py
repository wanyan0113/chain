"""Rebuild a pinned generic prototype in an isolated export and fresh venv.

Standard-library orchestrator: it never imports the old runtime's application
modules. Dependencies and tests run through the new interpreter explicitly.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT/'docs/task-flow/model-quality/baseline.json'
REQUIREMENTS = BASELINE.with_name('environment-requirements.txt')
TIME_KEYS = {'elapsed_ms', 'prepare_wall_ms', 'search_call_wall_ms', 'verification_wall_ms',
             'median_search_call_wall_ms', 'min_search_call_wall_ms', 'max_search_call_wall_ms'}
GENERIC_TESTS = ['test_task_flow_contracts', 'test_task_flow_state', 'test_task_flow_planner',
                 'test_task_flow_validator', 'test_task_flow_external', 'test_task_flow_research_audit',
                 'test_task_flow_study', 'test_expense_workflow']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8', newline='\n')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def run_path(value):
    path = value.resolve()
    require(path.is_relative_to((ROOT/'generated').resolve()) and path != (ROOT/'generated').resolve(),
            'run directory must be a dedicated directory inside workspace generated/')
    return path


def prepare(run, base_python):
    run = run_path(run)
    require(not run.exists(), 'choose a new run directory')
    baseline = read(BASELINE)
    require(re.fullmatch('[0-9a-f]{40}', baseline['baseline_commit']) is not None, 'invalid pinned commit')
    run.mkdir(parents=True)
    archive = run/'baseline-full.zip'
    subprocess.run(['git', 'archive', '--format=zip', '-o', str(archive), baseline['baseline_commit']], cwd=ROOT, check=True)
    source = run/'source'
    source.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            require((source/name).resolve().is_relative_to(source.resolve()), 'archive path outside export')
        bundle.extractall(source)
    (source/'tmp').mkdir(exist_ok=True)
    (run/'requirements-frozen.txt').write_bytes(REQUIREMENTS.read_bytes())
    subprocess.run([str(base_python), '-I', '-m', 'venv', str(run/'venv')], check=True)
    setup = {'baseline_commit': baseline['baseline_commit'], 'source_export': 'complete tracked Git tree at pinned commit',
             'source_modified': False, 'requirements_sha256': hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest(),
             'full_archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
             'run_directory': str(run), 'venv_created_from': str(base_python), 'old_runtime_lock_used': False}
    write(run/'setup.json', setup)
    return setup


def child_environment(run):
    # Also protects subprocesses spawned by the baseline tests, not only the
    # top-level interpreter. Do not log the surrounding environment or credentials.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PYTHON')}
    env['GIT_CEILING_DIRECTORIES'] = str(run.resolve())
    return env


def execute(run, label, args, *, source_cwd=True):
    start = time.perf_counter()
    result = subprocess.run([str(a) for a in args], cwd=run/'source' if source_cwd else run,
                            env=child_environment(run), capture_output=True, text=True, encoding='utf-8', errors='replace')
    (run/(label+'.log')).write_text(result.stdout+result.stderr, encoding='utf-8', newline='\n')
    record = {'label': label, 'args': [str(a) for a in args], 'returncode': result.returncode,
              'wall_ms': round((time.perf_counter()-start)*1000, 3), 'log': label+'.log'}
    write(run/(label+'-command.json'), record)
    return record, result


def install(run):
    run = run_path(run)
    python = run/'venv/Scripts/python.exe'
    record, _ = execute(run, 'install', [python, '-I', '-m', 'pip', 'install', '--isolated', '--no-cache-dir',
                                        '--disable-pip-version-check', '-r', run/'requirements-frozen.txt'], source_cwd=False)
    return record


def normalise(value):
    """Remove only enumerated timing fields; preserve statuses, budgets and types."""
    if isinstance(value, list):
        return [normalise(v) for v in value]
    if isinstance(value, dict):
        return {k: normalise(v) for k, v in value.items() if k not in TIME_KEYS}
    return value


def normalise_audit_case(value):
    result = normalise(value)
    observations = result['effective_inputs_and_calls']
    # The old audit IDs hash whole subjects, including timing. Re-key the normalized
    # subject content and its references together, without dropping actual records.
    ids = {old: digest(subject) for old, subject in observations['subjects'].items()}
    observations['subjects'] = {ids[old]: subject for old, subject in observations['subjects'].items()}
    for call in observations['calls']:
        if 'subject_id' in call:
            call['subject_id'] = ids[call['subject_id']]
    return result


def semantic_comparison(source, fresh):
    comparisons = []
    plans = [
        ('audit', source/'docs/task-flow/audit/phase1-4', [f'cases/C{i:02d}.json' for i in range(1, 24)]),
        ('phase5', source/'docs/task-flow/phase5/evidence',
         [c['file'] for c in read(source/'docs/task-flow/phase5/evidence/summary.json')['cases']]),
        ('phase6', source/'docs/task-flow/phase6/evidence', ['correctness.json', 'templates.json', 'model-risks.json', 'efficiency.json']),
        ('expense', source/'docs/task-flow/application-expense/evidence', [f'case-{i:02d}.json' for i in range(15)]+['policy-mutation.json'])]
    for group, reference, names in plans:
        for name in names:
            before, after = read(reference/name), read(fresh/group/name)
            if group == 'audit':
                before, after = normalise_audit_case(before), normalise_audit_case(after)
            else:
                before, after = normalise(before), normalise(after)
            expected, actual = digest(before), digest(after)
            comparisons.append({'group': group, 'file': name, 'expected_semantic_sha256': expected,
                                'actual_semantic_sha256': actual, 'equal': expected == actual})
    return comparisons


def source_inventory(run):
    mismatches = []
    checked = 0
    with zipfile.ZipFile(run/'baseline-full.zip') as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            checked += 1
            path = run/'source'/info.filename
            if not path.is_file() or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(archive.read(info.filename)).digest():
                mismatches.append(info.filename)
    return {'files_checked': checked, 'mismatches': mismatches, 'passed': not mismatches}


def check(run):
    run = run_path(run)
    require(not (run/'rebuild-summary.json').exists(), 'check result exists; preserve the previous run')
    setup, baseline = read(run/'setup.json'), read(BASELINE)
    require(setup['baseline_commit'] == baseline['baseline_commit'], 'wrong source baseline')
    require(hashlib.sha256((run/'baseline-full.zip').read_bytes()).hexdigest() == setup['full_archive_sha256'], 'archive changed')
    require((run/'requirements-frozen.txt').read_bytes() == REQUIREMENTS.read_bytes(), 'frozen requirements changed')
    python = run/'venv/Scripts/python.exe'
    commands = []

    def command(label, args):
        record, result = execute(run, label, [python, '-E', '-s', '-B', *args])
        commands.append(record)
        require(result.returncode == 0, label+' failed; see '+str(run/(label+'.log')))
        return result

    before = source_inventory(run)
    require(before['passed'], 'export source differs before execution')
    packages = json.loads(command('packages', ['-m', 'pip', 'list', '--isolated', '--format=json', '--disable-pip-version-check']).stdout)
    norm = lambda ps: {re.sub('[-_.]+', '-', p['name']).lower(): p['version'] for p in ps}
    require(norm(packages) == norm(baseline['environment']['packages']), 'installed package versions differ from frozen environment')
    command('pip-check', ['-m', 'pip', 'check', '--isolated', '--disable-pip-version-check'])
    probe = """import sys,site,json,importlib,importlib.metadata
names=['task_flow.contracts','workflow_tools.task_flow_study','tests.test_task_flow_contracts','workflow_tools.expense_workflow','jsonschema','numpy','owlready2']
print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,'python':sys.version,'user_site_enabled':site.ENABLE_USER_SITE,'sys_path':sys.path,'modules':{n:importlib.import_module(n).__file__ for n in names},'distribution_roots':{d.metadata['Name']:str(d.locate_file('')) for d in importlib.metadata.distributions()}},ensure_ascii=False))"""
    isolation = json.loads(command('isolation', ['-c', probe]).stdout)
    require(Path(isolation['prefix']).resolve() == (run/'venv').resolve(), 'not the fresh interpreter')
    require(isolation['user_site_enabled'] is False, 'user site unexpectedly enabled')
    require(isolation['python'] == baseline['environment']['python'], 'Python build differs')
    require(all(Path(p).resolve().is_relative_to((run/'venv').resolve()) for p in isolation['distribution_roots'].values()), 'package imported outside fresh environment')
    for name, path in isolation['modules'].items():
        base = run/'source' if name.startswith(('task_flow.', 'workflow_tools.', 'tests.')) else run/'venv'
        require(Path(path).resolve().is_relative_to(base.resolve()), 'module origin outside isolated export: '+name)
    write(run/'environment-proof.json', {'packages_match_frozen_snapshot': True, 'packages': packages,
                                        'isolation': isolation, 'venv_config': (run/'venv/pyvenv.cfg').read_text(encoding='utf-8')})
    tests = command('generic-tests', ['-m', 'unittest', *['tests.'+n for n in GENERIC_TESTS]])
    require(re.search(r'Ran 159 tests\b', tests.stderr+tests.stdout) is not None and 'skipped=' not in tests.stderr,
            'unexpected test count or skips')
    fresh = run/'experiments'
    fresh.mkdir()
    tools = [('audit','workflow_tools.task_flow_research_audit'), ('phase5','workflow_tools.task_flow_integration_checks'),
             ('phase6','workflow_tools.task_flow_study'), ('expense','workflow_tools.expense_evaluation')]
    for name, module in tools:
        args = ['-m', module]
        if name != 'phase5':
            args.append('run')
        command(name, [*args, '--output-dir', fresh/name])
    comparisons = semantic_comparison(run/'source', fresh)
    write(run/'semantic-comparison.json', {'normalization': sorted(TIME_KEYS),
           'audit_subject_ids_rekeyed_after_timing_normalization': True, 'comparisons': comparisons,
           'all_equal': all(c['equal'] for c in comparisons)})
    after = source_inventory(run)
    summary = {'status': 'passed' if after['passed'] and all(c['equal'] for c in comparisons) else 'failed',
               'baseline_commit': setup['baseline_commit'], 'scope': 'fresh_venv_same_host_and_python_build',
               'generic_tests': 159, 'baseline_audit': read(fresh/'audit/report.json')['status'],
               'reference_comparisons': len(comparisons), 'semantic_results_equal': all(c['equal'] for c in comparisons),
               'source_inventory_before': before, 'source_inventory_after': after, 'commands': commands,
               'independent_human_review_status': 'pending', 'cross_platform_reproduction_established': False}
    write(run/'rebuild-summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'install', 'check'])
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--base-python', type=Path, default=Path(sys.base_prefix)/'python.exe')
    args = parser.parse_args()
    try:
        result = prepare(args.run_dir,args.base_python) if args.command == 'prepare' else install(args.run_dir) if args.command == 'install' else check(args.run_dir)
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result.get('status','passed') == 'passed' and result.get('returncode',0) == 0 else 1
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status':'failed','reason':str(exc)},ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
