"""Source table10 expectations; all example facts are explicitly synthetic."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from design.article_cooperation import check_cooperation
from design.engine import DesignError

ROOT=Path(__file__).resolve().parents[1]
DEMO=ROOT/'design/examples/table10_parallel_demo.json'


class ArticleCooperationTests(unittest.TestCase):
    def setUp(self):
        self.doc=json.loads(DEMO.read_text(encoding='utf-8'))

    def test_explicit_parallel_group_passes_all_source_conditions(self):
        report=check_cooperation(self.doc)
        self.assertEqual(report['status'],'pass')
        self.assertEqual(len(report['checks']),16)
        self.assertEqual({c['id'] for c in report['checks']},{'communicates','communication-distance',
            'controls','single-manifestation-use','shared-detection-range','shared-max-tracks','shared-max-processing'})
        self.assertTrue(all(c['source'] for c in report['checks']))

    def test_maxima_strictly_greater_than_sum_not_chain_count(self):
        for index,field in [(0,'max_tracks'),(1,'max_processing_targets')]:
            for maximum,expected in [(2,'fail'),(1,'fail'),(3,'pass'),(None,'unknown')]:
                doc=deepcopy(self.doc);doc['artifacts'][index][field]=maximum
                with self.subTest(field=field,maximum=maximum):
                    self.assertEqual(check_cooperation(doc)['status'],expected)
            doc=deepcopy(self.doc);doc['chains'][0]['target_count']=2
            self.assertEqual(check_cooperation(doc)['status'],'fail') # 3 == 2+1
            doc['chains'][0]['target_count']=None
            self.assertEqual(check_cooperation(doc)['status'],'unknown')

    def test_detection_checks_each_task_and_units(self):
        for distance,unit,expected in [(20,'nmi','fail'),(21,'nmi','fail'),(19,'nmi','pass'),(6,'km','unknown')]:
            doc=deepcopy(self.doc);doc['contexts'][1]['target_distance']={'value':distance,'unit':unit}
            with self.subTest(distance=distance,unit=unit):
                self.assertEqual(check_cooperation(doc)['status'],expected)
        self.doc['contexts'].pop()
        self.assertEqual(check_cooperation(self.doc)['status'],'unknown')

    def test_link_direction_missing_and_false_remain_distinct(self):
        for field in ('communicates','controls'):
            for value,expected in [(False,'fail'),(None,'unknown'),(True,'pass')]:
                doc=deepcopy(self.doc);doc['links'][1][field]=value
                self.assertEqual(check_cooperation(doc)['status'],expected)
        self.doc['links'][0]['from'],self.doc['links'][0]['to']='coordinator','eye'
        report=check_cooperation(self.doc)
        self.assertEqual(report['status'],'unknown')
        self.assertTrue(any(c['id']=='communicates' and c['status']=='unknown' for c in report['checks']))

    def test_communication_distance_is_strict_and_unknown_units(self):
        for distance,unit,expected in [(10,'nmi','fail'),(11,'nmi','fail'),(9,'nmi','pass'),(1,'km','unknown')]:
            doc=deepcopy(self.doc);doc['links'][0]['distance']={'value':distance,'unit':unit}
            self.assertEqual(check_cooperation(doc)['status'],expected)

    def test_manifestation_cannot_be_shared_even_when_metrics_unknown(self):
        self.doc['chains'][1]['manifestation']='action-1'
        self.doc['artifacts'][0]['max_tracks']=None
        report=check_cooperation(self.doc)
        self.assertEqual(report['status'],'fail')
        self.assertTrue(any(c['id']=='single-manifestation-use' and c['status']=='fail' for c in report['checks']))
        self.assertTrue(any(c['status']=='unknown' for c in report['checks']))

    def test_independent_resources_do_not_pool_counts(self):
        self.doc['artifacts'] += [dict(id='eye-2',role='insight',source_refs=[]),
                                  dict(id='coordinator-2',role='coordination',source_refs=[])]
        self.doc['chains'][1].update(insight='eye-2',coordination='coordinator-2')
        self.doc['links'] += [dict(self.doc['links'][0],**{'from':'eye-2','to':'coordinator-2'}),
                             dict(self.doc['links'][2],**{'from':'coordinator-2'})]
        self.doc['artifacts'][0].pop('max_tracks')
        self.doc['artifacts'][1].pop('max_processing_targets')
        report=check_cooperation(self.doc)
        self.assertEqual(report['status'],'pass')
        self.assertFalse(any(c['id'].startswith('shared-') for c in report['checks']))

    def test_snapshot_and_registered_input_issues(self):
        original=deepcopy(self.doc);report=check_cooperation(self.doc)
        self.assertEqual(self.doc,original)
        self.doc['artifacts'][0]['max_tracks']=5
        self.assertEqual(report['input'],original)
        report['input']['links'].clear()
        self.assertTrue(self.doc['links'])
        self.doc['blocking_issue_ids']=['unconfirmed-links']
        self.assertEqual(check_cooperation(self.doc)['status'],'unknown')

    def test_invalid_or_ambiguous_references_fail_fast(self):
        for change in ('task','chain','artifact','link','context','role','count','nan','mode'):
            doc=deepcopy(self.doc)
            if change=='task':doc['chains'][1]['task']='task-1'
            elif change=='chain':doc['chains'][1]['id']='chain-1'
            elif change=='artifact':doc['chains'][0]['insight']='missing'
            elif change=='link':doc['links'].append(deepcopy(doc['links'][0]))
            elif change=='context':doc['contexts'][0]['task']='missing'
            elif change=='role':doc['chains'][0]['insight']='action-1'
            elif change=='count':doc['chains'][0]['target_count']=0
            elif change=='nan':doc['links'][0]['distance']['value']=float('nan')
            else:doc['mode']='serial'
            with self.subTest(change=change),self.assertRaises(DesignError):
                check_cooperation(doc)

    def test_cli_reports_pass_failure_unknown_and_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'input.json'
            for change,code,status in [('pass',0,'pass'),('fail',1,'fail'),('unknown',1,'unknown'),('invalid',2,'invalid')]:
                doc=deepcopy(self.doc)
                if change=='fail':doc['links'][1]['controls']=False
                elif change=='unknown':doc['artifacts'][0].pop('max_tracks')
                elif change=='invalid':doc['mode']='serial'
                path.write_text(json.dumps(doc),encoding='utf-8')
                result=subprocess.run([sys.executable,'-B','-m','design.article_cooperation',str(path)],
                                      cwd=ROOT,capture_output=True,encoding='utf-8',timeout=15)
                self.assertEqual(result.returncode,code,result.stderr)
                self.assertEqual(json.loads(result.stdout)['status'],status)
