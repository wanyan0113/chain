"""Independent table 10 expected comparisons; fixture numbers are synthetic."""
from copy import deepcopy
import unittest

from design.article_rules import screen_table10
from design.engine import DesignError


def fixture(role='manifestation'):
    # Deliberately small constructed quantities, NOT article case parameters.
    return {'role': role, 'task': {'target_type': '法球', 'impedance': {'value':10,'unit':'test-power'},
        'target_speed': {'value':10,'unit':'kn'}, 'target_altitude': {'value':10,'unit':'ft'}},
        'artifact': {'target_types': ['法球'], 'detection_range': {'value':20,'unit':'nmi'},
        'max_range': {'value':20,'unit':'nmi'}, 'min_range': {'value':1,'unit':'nmi'},
        'resonance': {'value':20,'unit':'test-power'}, 'max_target_speed': {'value':20,'unit':'kn'},
        'min_target_altitude': {'value':1,'unit':'ft'}, 'max_target_altitude': {'value':20,'unit':'ft'},
        'min_activation_altitude': {'value':1,'unit':'ft'}, 'max_activation_altitude': {'value':20,'unit':'ft'}},
        'context': {'target_distance': {'value':10,'unit':'nmi'}, 'platform_altitude': {'value':10,'unit':'ft'}}}


class ArticleTable10Tests(unittest.TestCase):
    def test_source_condition_coverage_and_positive(self):
        for role, ids in [('insight',['I1','I2']), ('manifestation',[f'M{i}' for i in range(1,10)])]:
            report=screen_table10(fixture(role))
            self.assertEqual(report['status'],'pass')
            self.assertEqual([c['id'] for c in report['checks']],ids)
            self.assertTrue(all(c['quote'] for c in report['checks']))

    def test_every_numeric_comparison_strict_boundary_opposite_and_unknown(self):
        # Hand-transcribed expectations from table10, independent of the rule
        # file: attribute, right-hand scope/field, passing direction.
        comparisons=[('insight','detection_range','context','target_distance',1),
            ('manifestation','max_range','context','target_distance',1),
            ('manifestation','min_range','context','target_distance',-1),
            ('manifestation','resonance','task','impedance',1),
            ('manifestation','max_target_speed','task','target_speed',1),
            ('manifestation','min_target_altitude','task','target_altitude',-1),
            ('manifestation','max_target_altitude','task','target_altitude',1),
            ('manifestation','min_activation_altitude','context','platform_altitude',-1),
            ('manifestation','max_activation_altitude','context','platform_altitude',1)]
        for role,field,scope,right,direction in comparisons:
            for offset,expected in [(direction,'pass'),(0,'fail'),(-direction,'fail')]:
                doc=fixture(role)
                doc['artifact'][field]['value']=doc[scope][right]['value']+offset
                with self.subTest(field=field,offset=offset):
                    self.assertEqual(screen_table10(doc)['status'],expected)
            for missing_scope,missing_field in [('artifact',field),(scope,right)]:
                doc=fixture(role); del doc[missing_scope][missing_field]
                with self.subTest(field=field,missing=missing_scope):
                    self.assertEqual(screen_table10(doc)['status'],'unknown')
            doc=fixture(role); doc['artifact'][field]['unit']='different-unit'
            self.assertEqual(screen_table10(doc)['status'],'unknown',field)

    def test_target_type_membership_missing_and_wrong(self):
        for role in ('insight','manifestation'):
            doc=fixture(role); doc['artifact']['target_types']=['其他目标']
            self.assertEqual(screen_table10(doc)['status'],'fail')
            doc['artifact'].pop('target_types')
            self.assertEqual(screen_table10(doc)['status'],'unknown')
            doc=fixture(role); doc['task'].pop('target_type')
            self.assertEqual(screen_table10(doc)['status'],'unknown')

    def test_article_zero_activation_interval_does_not_pass_strict_rule(self):
        doc=fixture()
        for field in ('min_activation_altitude','max_activation_altitude'):
            doc['artifact'][field]['value']=0
        doc['context']['platform_altitude']['value']=0
        report=screen_table10(doc)
        self.assertEqual(report['status'],'fail')
        self.assertEqual([c['status'] for c in report['checks'][-2:]],['fail','fail'])

    def test_report_snapshot_and_fail_with_unknown(self):
        doc=fixture(); saved=deepcopy(doc); report=screen_table10(doc)
        doc['artifact']['max_range']['value']=999
        self.assertEqual(report['input'],saved)
        report['input']['task']['target_type']='changed'
        self.assertEqual(doc['task']['target_type'],'法球')
        doc['artifact']['target_types']=['其他目标']; doc['context'].clear()
        report=screen_table10(doc)
        self.assertEqual(report['status'],'fail')
        self.assertTrue(any(c['status']=='unknown' for c in report['checks']))

    def test_invalid_shape_role_and_nonfinite_rejected(self):
        for change in ('role','bare','nan'):
            doc=fixture()
            if change=='role': doc['role']='coordination'
            elif change=='bare': doc['artifact']['max_range']=20
            else: doc['artifact']['max_range']['value']=float('nan')
            with self.subTest(change=change),self.assertRaises(DesignError):
                screen_table10(doc)
