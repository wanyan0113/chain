"""Phase-two contract boundaries; no planner or resource optimizer is exercised."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from task_flow.contracts import (ContractError, SCHEMA, canonical_sha256, prepare_problem,
                                 read_json, validate_instance, validate_sequence)
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'task_flow/examples/document_workflow'


def fixture():
    return read_json(EXAMPLES / 'catalog.json'), read_json(EXAMPLES / 'basic.json')


def bind(catalog, request):
    request['catalog_sha256'] = canonical_sha256(catalog)
    return prepare_problem(catalog, request)


def instance(name='collect', document='doc-1'):
    return {'activity_id': name, 'activity_version': '1.0', 'parameters': {'document': document}}


class TaskFlowContractTests(unittest.TestCase):
    def test_schema_is_valid(self):
        Draft202012Validator.check_schema(SCHEMA)

    def test_all_eight_requests_compile_without_input_mutation(self):
        cat, _ = fixture()
        for row in read_json(EXAMPLES / 'manual_cases.json')['cases']:
            req = read_json(EXAMPLES / row['request'])
            original = deepcopy((cat, req))
            problem = prepare_problem(cat, req)
            self.assertEqual((cat, req), original)
            self.assertEqual(len(problem.instances), 5)
            self.assertEqual(len(problem.fact_specs), 6)
            problem.catalog['version'] = 'changed-copy'
            self.assertEqual((cat, req), original)

    def test_duplicate_json_and_nonfinite_literals_rejected(self):
        for payload in ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1.0}', '{"x":1e999}']:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'input.json'
                path.write_text(payload, encoding='utf-8')
                with self.assertRaises(ContractError):
                    read_json(path)

    def test_non_json_types_and_noncanonical_unicode_rejected(self):
        for obj in [{'v': (1, 2)}, {'v': 2 ** 54}, {'v': 'e\u0301'}, {'v': '\ud800'}, {1: True}]:
            with self.subTest(obj=repr(obj)), self.assertRaises(ContractError):
                canonical_sha256(obj)

    def test_canonical_digest_sorts_maps_but_preserves_lists_and_types(self):
        self.assertEqual(canonical_sha256({'a': 1, 'b': 2}), canonical_sha256({'b': 2, 'a': 1}))
        self.assertNotEqual(canonical_sha256([1, 2]), canonical_sha256([2, 1]))
        self.assertNotEqual(canonical_sha256(True), canonical_sha256(1))

    def test_identity_version_and_digest_are_bound(self):
        for field in ('catalog_id', 'catalog_version', 'catalog_sha256'):
            cat, req = fixture()
            req[field] = '0' * 64 if field.endswith('sha256') else 'different'
            with self.subTest(field=field), self.assertRaises(ContractError):
                prepare_problem(cat, req)

    def test_strict_missing_and_unknown_fields(self):
        for missing in ('version', 'required_capabilities', 'source_refs'):
            cat, req = fixture()
            del cat['activities'][0][missing]
            with self.subTest(missing=missing), self.assertRaises(ContractError):
                bind(cat, req)
        cat, req = fixture()
        req['plan'] = []
        with self.assertRaises(ContractError):
            prepare_problem(cat, req)

    def test_required_knowledge_missing_is_distinct_from_unknown_state(self):
        cat, req = fixture()
        del req['goal']
        with self.assertRaises(ContractError) as cm:
            prepare_problem(cat, req)
        self.assertEqual(cm.exception.code, 'input_incomplete')
        cat, req = fixture()
        req['initial_state'][0]['value'] = None
        self.assertIsNone(prepare_problem(cat, req).initial_base[('collected', 'doc-1')])

    def test_boolean_not_accepted_as_search_integer(self):
        cat, req = fixture()
        req['search']['max_nodes'] = True
        with self.assertRaises(ContractError):
            prepare_problem(cat, req)

    def test_duplicate_identity_and_undeclared_capability(self):
        for kind in ('facts', 'activities', 'capabilities', 'derived_rules'):
            cat, req = fixture()
            cat[kind].append(deepcopy(cat[kind][0]))
            with self.subTest(kind=kind), self.assertRaises(ContractError):
                bind(cat, req)
        cat, req = fixture()
        cat['capabilities'] = []
        with self.assertRaises(ContractError):
            bind(cat, req)

    def test_no_unsupported_claim_of_ontology_mapping(self):
        cat, req = fixture()
        cat['capabilities'][0]['mapping_status'] = 'mapped'
        with self.assertRaises(ContractError):
            bind(cat, req)

    def test_two_objects_ground_both_activities_and_facts(self):
        cat, req = fixture()
        cat['objects']['document'].append('doc-2')
        problem = bind(cat, req)
        self.assertEqual(len(problem.instances), 10)
        self.assertEqual(len(problem.fact_specs), 11)
        self.assertEqual(len(problem.derived_rules), 2)
        self.assertEqual(problem.instances[0]['parameters'], {'document': 'doc-1'})
        validate_instance(problem, instance(document='doc-2'))

    def test_ground_limit_checked_before_large_allocation(self):
        cat, req = fixture()
        with self.assertRaisesRegex(ContractError, 'instantiation limit'):
            prepare_problem(cat, req, max_groundings=2)
        for bad in (True, 0, 100001):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                prepare_problem(cat, req, max_groundings=bad)

    def test_unknown_domains_and_wrong_parameter_types(self):
        cat, req = fixture()
        cat['activities'][0]['parameters']['document'] = 'missing'
        with self.assertRaises(ContractError):
            bind(cat, req)
        cat, req = fixture()
        cat['objects']['other'] = ['other-1']
        cat['activities'][1]['parameters']['document'] = 'other'
        with self.assertRaisesRegex(ContractError, 'outside domain'):
            bind(cat, req)

    def test_instance_requires_exact_binding_and_version(self):
        cat, req = fixture()
        problem = prepare_problem(cat, req)
        for candidate in [instance(document='absent'), dict(instance(), activity_version='2'),
                          dict(instance(), parameters={}), dict(instance(), parameters={'document': 'doc-1', 'extra': 'doc-1'})]:
            with self.subTest(candidate=candidate), self.assertRaises(ContractError):
                validate_instance(problem, candidate)

    def test_duplicate_ground_assignment_including_conflict_rejected(self):
        for value in (False, True):
            cat, req = fixture()
            duplicate = deepcopy(req['initial_state'][0])
            duplicate['value'] = value
            req['initial_state'].append(duplicate)
            with self.subTest(value=value), self.assertRaises(ContractError):
                prepare_problem(cat, req)

    def test_static_and_derived_cannot_be_written(self):
        for name, args in [('source_available', []), ('ready', [{'param': 'document'}])]:
            cat, req = fixture()
            cat['activities'][0]['effects'][0]['fact'] = {'predicate': name, 'args': args}
            with self.subTest(name=name), self.assertRaises(ContractError):
                bind(cat, req)

    def test_aliasing_writes_rejected_after_grounding(self):
        cat, req = fixture()
        action = cat['activities'][0]
        action['parameters']['second'] = 'document'
        second = deepcopy(action['effects'][0])
        second['fact']['args'] = [{'param': 'second'}]
        action['effects'].append(second)
        with self.assertRaisesRegex(ContractError, 'conflicting writes'):
            bind(cat, req)

    def test_derived_cycle_and_missing_rules_rejected(self):
        cat, req = fixture()
        cat['derived_rules'][0]['when']['all'][0]['test']['fact']['predicate'] = 'ready'
        with self.assertRaisesRegex(ContractError, 'cyclic'):
            bind(cat, req)
        cat, req = fixture()
        cat['derived_rules'] = []
        with self.assertRaises(ContractError):
            bind(cat, req)

    def test_explicit_static_ontology_evidence_preserves_provenance(self):
        cat, req = fixture()
        req['static_facts'] = []
        evidence = {'fact': {'predicate': 'source_available', 'args': []}, 'value': True,
                    'source_ref': 'synthetic-snapshot', 'knowledge_sha256': 'a' * 64, 'rule_ref': 'declared-static-fact'}
        req['ontology_evidence'] = [evidence]
        problem = prepare_problem(cat, req)
        self.assertIs(problem.initial_base[('source_available',)], True)
        self.assertEqual(problem.request['ontology_evidence'][0], evidence)
        req['static_facts'] = [{'fact': evidence['fact'], 'value': False}]
        with self.assertRaisesRegex(ContractError, 'conflicts'):
            prepare_problem(cat, req)

    def test_sequence_contract_does_not_claim_semantic_replay(self):
        cat, req = fixture()
        problem = prepare_problem(cat, req)
        seq = {'format_version': 'task-flow-1.0', 'catalog_sha256': problem.catalog_sha256,
               'request_sha256': problem.request_sha256, 'steps': [instance('approve')]}
        # Structurally valid, but approve cannot execute in the initial state.
        self.assertEqual(validate_sequence(problem, seq), seq)
        seq['steps'] *= 2
        with self.assertRaisesRegex(ContractError, 'occurrence'):
            validate_sequence(problem, seq)
        seq['steps'] = []
        seq['request_sha256'] = '0' * 64
        with self.assertRaisesRegex(ContractError, 'digest'):
            validate_sequence(problem, seq)

    def test_cli_preflight_success_and_invalid_input(self):
        command = [sys.executable, '-B', '-m', 'task_flow', 'check', '--catalog', str(EXAMPLES / 'catalog.json')]
        valid = subprocess.run(command + ['--request', str(EXAMPLES / 'basic.json')], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(valid.returncode, 0, valid.stderr)
        result = json.loads(valid.stdout)
        self.assertFalse(result['planning_executed'])
        self.assertEqual(result['ground_activity_instances'], 5)
        bad = subprocess.run(command + ['--request', str(EXAMPLES / 'not-found.json')], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(json.loads(bad.stdout)['status'], 'invalid_input')

    def test_cli_initial_constraint_rejected_and_unknown_exit_one(self):
        cat, req = fixture()
        condition = {'test': {'fact': {'predicate': 'collected', 'args': ['doc-1']}, 'op': 'eq', 'value': True}}
        req['forbidden_conditions'] = [condition]
        for value, status in ((True, 'rejected'), (None, 'blocked_unknown')):
            req['initial_state'][0]['value'] = value
            with self.subTest(value=value), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'request.json'
                path.write_text(json.dumps(req), encoding='utf-8')
                result = subprocess.run([sys.executable, '-B', '-m', 'task_flow', 'check', '--catalog', str(EXAMPLES / 'catalog.json'),
                                         '--request', str(path)], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(json.loads(result.stdout)['initial_constraint_status'], status)

    def test_unknown_predicate_arity_and_unbound_variable_rejected(self):
        for mode in ('predicate', 'arity', 'variable'):
            cat, req = fixture()
            ref = cat['activities'][1]['preconditions']['all'][0]['test']['fact']
            if mode == 'predicate':
                ref['predicate'] = 'missing'
            elif mode == 'arity':
                ref['args'] = []
            else:
                ref['args'] = [{'param': 'missing'}]
            with self.subTest(mode=mode), self.assertRaises(ContractError):
                bind(cat, req)

    def test_duplicate_ontology_evidence_and_mutable_import_rejected(self):
        for mode in ('duplicate', 'mutable'):
            cat, req = fixture()
            evidence = {'fact': {'predicate': 'source_available', 'args': []}, 'value': True,
                        'source_ref': 'snapshot', 'knowledge_sha256': 'a' * 64, 'rule_ref': 'static-rule'}
            req['ontology_evidence'] = [evidence]
            if mode == 'duplicate':
                req['ontology_evidence'].append(deepcopy(evidence))
            else:
                evidence['fact'] = {'predicate': 'collected', 'args': ['doc-1']}
            with self.subTest(mode=mode), self.assertRaises(ContractError):
                prepare_problem(cat, req)


if __name__ == '__main__':
    unittest.main()
