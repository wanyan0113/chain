from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fdna.model import ModelError
from fdna.structure import digest
from fdna.topology_preview import canonical_network, preview, run


def fixture():
    network = dict(id='preview_test', nodes=['A', 'B', 'F', 'X', 'Y'], edges=[
        dict(feeder='A', receiver='B'), dict(feeder='B', receiver='F'), dict(feeder='X', receiver='Y')])
    inputs = dict(A=['fa'], B=['ab'], F=['bf', 'af'], X=['fx'], Y=['xy'])
    outputs = dict(A=['ab', 'af', 'heartbeat'], B=['bf'], F=['fa', 'fx'], X=['xy'], Y=[])
    activities = {n: dict(subject='subject_'+n, chain_id='c', inputs=inputs[n], outputs=outputs[n]) for n in network['nodes']}
    candidates = []
    for cid, a, b, product, exported in [('ab','A','B','ab',True), ('bf','B','F','bf',True), ('xy','X','Y','xy',True),
        ('af','A','F','af',False), ('fa','F','A','fa',False), ('fx','F','X','fx',False), ('heartbeat','A','B','heartbeat',False)]:
        candidates.append(dict(id=cid, feeder=a, receiver=b, product=product,
            product_matched=product in inputs[b], status='approved_assumption' if exported else 'pending',
            exported=exported, equipment_link_evidence=[dict(**{'from':'subject_'+a,'to':'subject_'+b}, communicates=True)]))
    return network, dict(capability_instances=activities, candidates=candidates)


def proposal(network, remove=(), add=(), sidecar=None):
    return dict(id='change', source_network_sha256=digest(canonical_network(network)),
        source_sidecar_sha256=digest(sidecar if sidecar is not None else fixture()[1]),
        remove=[dict(candidate_id=cid, rationale='test removal') for cid in remove],
        add=[dict(candidate_id=cid, rationale='test addition') for cid in add])


class TopologyPreviewTests(unittest.TestCase):
    def test_sole_input_removal_preserves_targets_and_requires_root_policy(self):
        network, side = fixture(); change = proposal(network, remove=['ab'])
        before = deepcopy((network, side, change))
        result = preview(network, side, change)
        self.assertEqual(result['added_roots'], ['B'])
        self.assertEqual(result['requires_explicit_root_policy'], ['B'])
        self.assertEqual(result['local_equations_removed'], ['B'])
        self.assertEqual(result['local_equations_to_refit'], [])
        self.assertEqual(result['downstream_prediction_recompute'], ['B', 'F'])
        self.assertIn('B', result['original_evaluation_targets'])
        self.assertEqual(result['unchanged_nodes'], ['A', 'X', 'Y'])
        self.assertFalse(result['policies']['topology_applied'])
        self.assertEqual((network, side, change), before)

    def test_additional_input_invalidates_all_receiver_parameters_not_descendant_fits(self):
        network, side = fixture(); result = preview(network, side, proposal(network, add=['af']))
        self.assertEqual(result['local_equations_to_refit'], ['F'])
        self.assertEqual(result['parameter_reuse_forbidden_receivers'], ['F'])
        self.assertEqual(result['reachability'][0]['incoming_after'], ['A', 'B'])
        self.assertEqual(result['status'], 'review_required')
        self.assertFalse(result['policies']['mapping_approved'])

    def test_replacement_changes_incoming_set_without_fabricating_new_root(self):
        network, side = fixture(); result = preview(network, side, proposal(network, remove=['bf'], add=['af']))
        self.assertEqual(result['added_roots'], [])
        self.assertEqual(result['local_equations_to_refit'], ['F'])
        self.assertEqual(result['reachability'][0]['incoming_before'], ['B'])
        self.assertEqual(result['reachability'][0]['incoming_after'], ['A'])

    def test_root_becoming_receiver_and_new_descendants_are_reported(self):
        network, side = fixture(); result = preview(network, side, proposal(network, add=['fx']))
        self.assertEqual(result['lost_roots'], ['X'])
        self.assertEqual(result['affected_nodes'], ['X', 'Y'])
        self.assertEqual(result['local_equations_to_refit'], ['X'])
        self.assertTrue(result['policies']['external_information_changed'])

    def test_product_shortcut_blocked_even_with_channel_and_approval(self):
        network, side = fixture(); candidate = next(c for c in side['candidates'] if c['id'] == 'af')
        side['capability_instances']['F']['inputs'].remove('af')
        candidate.update(product_matched=False, status='approved_assumption')
        result = preview(network, side, proposal(network, add=['af'], sidecar=side))
        self.assertEqual(result['status'], 'semantic_blocked')
        self.assertIn('NO_DIRECT_PRODUCT_CONSUMPTION', result['actions'][0]['semantic_blockers'])

    def test_candidate_evidence_and_source_topology_must_match(self):
        network, side = fixture(); side['candidates'][0]['exported'] = False
        with self.assertRaisesRegex(ModelError, '审计导出边'):
            preview(network, side, proposal(network, remove=['ab']))
        network, side = fixture(); side['candidates'][0]['product_matched'] = False
        with self.assertRaisesRegex(ModelError, '产品匹配'):
            preview(network, side, proposal(network, remove=['ab']))

    def test_malformed_product_definitions_cannot_pass_by_string_membership(self):
        network, side = fixture(); side['capability_instances']['B']['inputs'] = 'ab'
        with self.assertRaisesRegex(ModelError, '产品输入输出须为数组'):
            preview(network, side, proposal(network, remove=['ab'], sidecar=side))

    def test_channel_cross_chain_and_rejected_candidate_are_separate_blockers(self):
        network, side = fixture(); candidate = next(c for c in side['candidates'] if c['id'] == 'af')
        candidate['equipment_link_evidence'] = []; candidate['status'] = 'rejected'
        # Keep original exported B->F consistent while this candidate is cross-chain.
        network['edges'] = [e for e in network['edges'] if e['receiver'] != 'F']
        next(c for c in side['candidates'] if c['id'] == 'bf').update(exported=False, status='pending')
        side['capability_instances']['F']['chain_id'] = 'other'
        result = preview(network, side, proposal(network, add=['af'], sidecar=side))
        self.assertEqual(set(result['actions'][0]['semantic_blockers']),
            {'NO_DIRECT_CHANNEL_EVIDENCE','CROSS_CHAIN_NOT_SUPPORTED','CANDIDATE_REJECTED'})

    def test_cycles_unknown_duplicate_and_noop_changes_rejected(self):
        network, side = fixture()
        for change in [proposal(network, add=['fa']), proposal(network, add=['unknown']),
                       proposal(network, remove=['ab','ab']), proposal(network, add=['heartbeat']), proposal(network)]:
            with self.subTest(change=change), self.assertRaises(ModelError): preview(network, side, change)

    def test_stale_binding_rejected_and_input_order_does_not_change_preview_identity(self):
        network, side = fixture(); change = proposal(network, remove=['ab'])
        original = preview(network, side, change)
        network['nodes'].reverse(); network['edges'].reverse()
        self.assertEqual(original, preview(network, side, change))
        change['source_network_sha256'] = 'stale'
        with self.assertRaisesRegex(ModelError, '源网络已变化'): preview(network, side, change)

    def test_all_edges_removed_is_structural_only_and_paths_are_capped(self):
        network, side = fixture(); result = preview(network, side, proposal(network, remove=['ab','bf','xy']), max_paths=1)
        self.assertTrue(result['empty_edge_graph'])
        self.assertEqual(result['local_equations_removed'], ['B', 'F', 'Y'])
        self.assertTrue(next(p for p in result['paths'] if p['from_node'] == 'A')['before_truncated'])
        self.assertFalse(result['policies']['fitted'])

    def test_nonempty_output_refused_and_fitter_never_called(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory); (out/'keep').write_text('keep', encoding='utf-8')
            with self.assertRaises(ModelError), patch('fdna.network_workflow.fit_network') as fitter:
                try: run('missing','missing','missing',out)
                finally: fitter.assert_not_called()

    def test_valid_run_writes_only_review_artifacts_and_never_calls_fitter(self):
        network, side = fixture(); change = proposal(network, remove=['ab'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in [('network',network),('side',side),('proposal',change)]:
                (root/(name+'.json')).write_text(json.dumps(value), encoding='utf-8')
            with patch('fdna.network_workflow.fit_network') as fitter, redirect_stdout(io.StringIO()):
                result = run(root/'network.json', root/'side.json', root/'proposal.json', root/'out')
            fitter.assert_not_called()
            self.assertEqual(result['added_roots'], ['B'])
            self.assertEqual({p.name for p in (root/'out').iterdir()}, {'preview_report.json','结果说明.md','manifest.json'})
            self.assertEqual(json.loads((root/'network.json').read_text()), network)
        side['capability_instances']['B']['review_note'] = 'changed assumption'
        with self.assertRaisesRegex(ModelError, '审计依据已变化'): preview(network, side, change)


if __name__ == '__main__': unittest.main()
