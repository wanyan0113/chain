"""Integrity checks for rebuild isolation and typed semantic comparison."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from workflow_tools.rebuild_frozen_baseline import (ROOT, child_environment, digest, normalise,
    normalise_audit_case, prepare, run_path, semantic_comparison, source_inventory)


class FrozenRebuildTests(unittest.TestCase):
    def test_normalization_preserves_budgets_statuses_and_json_types(self):
        left = {'elapsed_ms': 1.2, 'limits': {'timeout_ms': 30000, 'max_nodes': 1},
                'verification_status': 'passed', 'value': True, 'steps': [{'id': 'elapsed_ms'}]}
        right = deepcopy(left)
        right['elapsed_ms'] = 99
        self.assertEqual(digest(normalise(left)), digest(normalise(right)))
        right['value'] = 1
        self.assertNotEqual(digest(normalise(left)), digest(normalise(right)))
        right = deepcopy(left)
        right['limits']['timeout_ms'] = 1
        self.assertNotEqual(digest(normalise(left)), digest(normalise(right)))
        self.assertEqual(normalise(left)['steps'], [{'id': 'elapsed_ms'}])

    def test_audit_subject_ids_are_rekeyed_with_call_references(self):
        def case(identifier, elapsed):
            subject = {'statistics': {'elapsed_ms': elapsed}, 'candidate': {'valid': True}}
            return {'elapsed_ms': elapsed, 'effective_inputs_and_calls': {
                'subjects': {identifier: subject}, 'calls': [{'operation': 'verify', 'subject_id': identifier}],
                'models': {}, 'search_runs': []}}
        left, right = normalise_audit_case(case('old-subject', 1)), normalise_audit_case(case('new-subject', 5))
        self.assertEqual(digest(left), digest(right))
        identifier = left['effective_inputs_and_calls']['calls'][0]['subject_id']
        self.assertIn(identifier, left['effective_inputs_and_calls']['subjects'])

    def test_wrong_module_or_candidate_content_is_not_normalized_away(self):
        baseline = {'candidate_id': 'flow-a', 'verification_status': 'passed', 'steps': [{'activity_id': 'submit'}]}
        for key, value in [('candidate_id','flow-b'), ('verification_status','rejected'), ('steps',[])]:
            changed = deepcopy(baseline)
            changed[key] = value
            self.assertNotEqual(digest(normalise(baseline)), digest(normalise(changed)))

    def test_source_inventory_detects_missing_and_modified_export_files(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run/'source').mkdir()
            with zipfile.ZipFile(run/'baseline-full.zip','w') as archive:
                archive.writestr('model.json', b'{"goal":true}\n')
            path = run/'source/model.json'
            path.write_bytes(b'{"goal":true}\n')
            self.assertTrue(source_inventory(run)['passed'])
            path.write_bytes(b'{"goal":false}\n')
            self.assertEqual(source_inventory(run)['mismatches'], ['model.json'])
            path.unlink()
            self.assertFalse(source_inventory(run)['passed'])

    def test_python_environment_leaks_and_parent_git_discovery_are_blocked(self):
        with patch.dict(os.environ, {'PYTHONPATH':'old-site', 'PythonHome':'old-python', 'PATH':'unchanged'}, clear=True):
            env = child_environment(ROOT/'generated/example')
        self.assertFalse(any(k.upper().startswith('PYTHON') for k in env))
        self.assertEqual(env['PATH'], 'unchanged')
        self.assertEqual(Path(env['GIT_CEILING_DIRECTORIES']), (ROOT/'generated/example').resolve())

    def test_run_directory_cannot_target_workspace_or_existing_export(self):
        with self.assertRaises(ValueError):
            run_path(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            path = workspace/'generated/existing'
            path.mkdir(parents=True)
            marker = path/'preserve.txt'
            marker.write_text('keep', encoding='utf-8')
            with patch('workflow_tools.rebuild_frozen_baseline.ROOT', workspace):
                with self.assertRaisesRegex(ValueError, 'new run'):
                    prepare(path, Path('unused-python.exe'))
            self.assertEqual(marker.read_text(), 'keep')

    def test_semantic_comparison_checks_actual_records_not_only_summary_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            source, fresh = Path(directory)/'source', Path(directory)/'fresh'
            audit = {'actual': {'goal': True}, 'elapsed_ms': 1, 'effective_inputs_and_calls': {'subjects':{}, 'calls':[]}}
            groups = [
                ('audit','docs/task-flow/audit/phase1-4',[f'cases/C{i:02d}.json' for i in range(1,24)]),
                ('phase5','docs/task-flow/phase5/evidence',['one.json']),
                ('phase6','docs/task-flow/phase6/evidence',['correctness.json','templates.json','model-risks.json','efficiency.json']),
                ('expense','docs/task-flow/application-expense/evidence',[f'case-{i:02d}.json' for i in range(15)]+['policy-mutation.json'])]
            summary=source/'docs/task-flow/phase5/evidence/summary.json'
            summary.parent.mkdir(parents=True)
            summary.write_text(json.dumps({'cases':[{'file':'one.json'}]}),encoding='utf-8')
            for group, prefix, names in groups:
                for name in names:
                    for path in [source/prefix/name, fresh/group/name]:
                        path.parent.mkdir(parents=True,exist_ok=True)
                        path.write_text(json.dumps(audit),encoding='utf-8')
            self.assertTrue(all(c['equal'] for c in semantic_comparison(source,fresh)))
            changed=deepcopy(audit)
            changed['actual']['goal']=1
            (fresh/'phase5/one.json').write_text(json.dumps(changed),encoding='utf-8')
            mismatch=[c for c in semantic_comparison(source,fresh) if not c['equal']]
            self.assertEqual([(c['group'],c['file']) for c in mismatch],[('phase5','one.json')])

    def test_human_review_materials_keep_original_inputs_and_pending_status(self):
        folder=ROOT/'docs/task-flow/domain-review'
        status=json.loads((folder/'review-status.json').read_text(encoding='utf-8'))
        if status['status']=='pending_human_review':
            self.assertIsNone(status['independent_reviewer'])
            self.assertFalse(status['review_completed'])
        else:
            self.assertTrue(status['independent_reviewer'])
            self.assertTrue(status['review_completed'])
            self.assertTrue(status['independent_first_pass_requirements_received'])
        raw=json.loads((folder/'phase-a-raw-cases.json').read_text(encoding='utf-8'))
        self.assertEqual(len(raw['cases']),4)
        original=json.loads((ROOT/'task_flow/examples/expense_workflow/cases.json').read_text(encoding='utf-8'))
        originals={c['id']:c['input'] for c in original['cases']}
        for case in raw['cases']:
            self.assertEqual(case, originals[case['case_id']])
        manifest=json.loads((folder/'materials-manifest.json').read_text(encoding='utf-8'))
        self.assertFalse(manifest['independent_human_review_completed'])
        for name, expected in manifest['artifact_sha256'].items():
            self.assertEqual(hashlib.sha256((folder/name).read_bytes()).hexdigest(),expected)


if __name__=='__main__':
    unittest.main()
