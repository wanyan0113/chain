from copy import deepcopy
import unittest

from fdna.diagnostic_cards import build_cards, delta_contrasts, mapping_audit
from fdna.diagnostic_overview import evidence_rows, overview_markdown, audit_markdown
from fdna.model import ModelError
from tests.test_diagnostic_cards import fixture


class DiagnosticOverviewTests(unittest.TestCase):
    def setUp(self):
        self.report, self.rows, self.side = fixture()
        for job in self.report['jobs']:
            for metric in job['metrics']:
                metric.update(available=1, evaluated=1, mae=2, outside=0)
        self.cards = build_cards(self.report, self.rows, self.side)
        self.index = [dict(fit_id=c['fit_id'], card='诊断卡片/' + c['model'] + '.md') for c in self.cards]

    def test_metrics_identity_and_input_immutability(self):
        before = deepcopy((self.cards, self.index))
        summary = evidence_rows(self.cards, self.index)
        self.assertEqual((self.cards, self.index), before)
        self.assertEqual(len(summary), 2)
        for row in summary:
            card = next(c for c in self.cards if c['fit_id'] == row['fit_id'])
            self.assertEqual(row['data_sha256'], card['data_sha256'])
            for kind in ('local', 'recursive'):
                for split in ('train', 'holdout'):
                    original = next(m for m in card['metrics'] if m['prediction'] == kind and m['split'] == split)
                    self.assertEqual(row[kind + '_' + split + '_rmse'], original['rmse'])
            self.assertEqual(row['prediction_usability'], 'not_assessed_no_application_threshold')
            self.assertEqual(row['local_holdout_evidence'], 'evaluated_all_records')
            self.assertIn('缺少局部约束诊断', row['parameter_interpretation'])

    def test_partial_and_missing_holdout_are_not_complete(self):
        card = self.cards[0]
        card['split_counts']['holdout'] = 3
        local = next(m for m in card['metrics'] if m['split'] == 'holdout' and m['prediction'] == 'local')
        local.update(available=3, evaluated=1)
        recursive = next(m for m in card['metrics'] if m['split'] == 'holdout' and m['prediction'] == 'recursive')
        recursive.update(evaluated=0, rmse=None)
        row = evidence_rows(self.cards, self.index)[0]
        self.assertEqual(row['local_holdout_evidence'], 'evaluated_partial_records')
        self.assertEqual(row['recursive_holdout_evidence'], 'not_evaluated')
        # Even a subset labelled fully evaluated must not conceal omitted records.
        local.update(available=1)
        self.assertEqual(evidence_rows(self.cards, self.index)[0]['local_holdout_evidence'], 'evaluated_partial_records')

    def test_rank_boundary_and_coverage_are_separate(self):
        card = self.cards[1]
        card['sensitivity'] = dict(local_jacobian_rank=3, parameter_count=3, inactive_parameters=[])
        card['flags'] = ['MULTISTART_PARAMETER_SPREAD', 'DELTA_AT_BOUNDARY']
        card['prediction_coverage'] = [dict(split='holdout', local_outside_training=2, recursive_outside_training=4)]
        row = evidence_rows(self.cards, self.index)[1]
        self.assertIn('不证明全局唯一', row['parameter_interpretation'])
        self.assertIn('多初值存在参数跨度', row['parameter_interpretation'])
        self.assertTrue(row['delta_boundary'])
        self.assertEqual(row['recursive_holdout_outside_training'], 4)
        self.assertEqual(row['recursive_holdout_outside'], 0)
        card['sensitivity']['local_jacobian_rank'] = 2
        self.assertIn('局部参数约束不足', evidence_rows(self.cards, self.index)[1]['parameter_interpretation'])
        card['status'] = 'iteration_limit'
        self.assertIn('拟合未完成', evidence_rows(self.cards, self.index)[1]['parameter_interpretation'])

    def test_duplicate_and_unmatched_records_rejected(self):
        variants = [(self.cards + [self.cards[0]], self.index), (self.cards, self.index[:1]),
                    (self.cards, self.index + [self.index[0]])]
        for cards, index in variants:
            with self.subTest(index=index), self.assertRaises(ModelError):
                evidence_rows(cards, index)
        self.cards[0]['metrics'].append(deepcopy(self.cards[0]['metrics'][0]))
        with self.assertRaises(ModelError):
            evidence_rows(self.cards, self.index)

    def test_reading_views_link_cards_and_preserve_audit_boundaries(self):
        nodes, edges = mapping_audit(self.report, self.side, 'synthetic_scenario')
        stats = dict(experiment_id='exp', cards=2, delta_pairs=1, node_audit_rows=2,
                     candidate_audit_rows=1, exported_edges=1, flag_counts={})
        text = overview_markdown(evidence_rows(self.cards, self.index), delta_contrasts(self.cards),
                                 stats, 'synthetic_scenario')
        for entry in self.index:
            self.assertIn('](' + entry['card'] + ')', text)
        self.assertIn('未登记应用容许误差', text)
        self.assertIn('06未运行固定δ组', text)
        self.assertIn('不是已确认的真实依赖', audit_markdown(nodes, edges))


if __name__ == '__main__':
    unittest.main()
