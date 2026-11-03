"""Table10 conditions participate in real Excel design/search/export outcomes."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
from http.server import HTTPServer
from urllib.request import Request, urlopen
import unittest
from unittest.mock import patch

from design.engine import design, DesignError
from design.excel import read_design
from design.rdf import design_to_rdf
from excel_entry.app import ROOT, Handler, run_design, run_import
from excel_entry.importer import read_xlsx
from excel_entry.unified import convert_design
from design.article_integration import cooperation

EXAMPLE=ROOT/'excel_entry/templates/表10设计演示.xlsx'


class ArticleIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=EXAMPLE.read_bytes()

    def setUp(self):
        self.doc=read_design(self.data)

    def test_real_excel_enabled_search_rejects_reused_manifestation(self):
        self.assertTrue(self.doc['policies']['article_table10'])
        report=design(self.doc)
        self.assertEqual(report['status'],'complete')
        self.assertEqual(report['network_search']['feasible'],2)
        self.assertEqual(report['article_table10']['selected']['status'],'pass')
        self.assertEqual(len(report['article_table10']['combinations']),4)
        self.assertEqual(report['network']['score'],.5)
        self.assertTrue(all(s['table10']['status']=='pass' for t in report['tasks'] for s in t['selection'] if 'table10' in s))
        graph=design_to_rdf(self.doc,report,require_canonical=True)
        self.assertTrue(len(graph))
        report['article_table10']['selected']['groups'][0]['checks'][0]['status']='fail'
        with self.assertRaises(DesignError):
            design_to_rdf(self.doc,report,require_canonical=True)

    def test_strict_screening_overrides_more_permissive_engine_rules(self):
        # Existing custom insight condition permits equality (ge); table10 gt
        # must still exclude that detector and prevent complete network output.
        self.doc['artifacts'][0]['attributes']['detection_range']['value']=5
        report=design(self.doc)
        self.assertEqual(report['status'],'no_feasible_design')
        self.assertIsNone(report['network'])
        check=report['tasks'][0]['selection'][0]
        self.assertFalse(check['accepted'])
        self.assertEqual(check['table10']['status'],'fail')

    def test_missing_screening_data_blocks_and_source_value_changes_selection(self):
        del self.doc['artifacts'][0]['attributes']['detection_range']
        report=design(self.doc)
        self.assertEqual(report['status'],'blocked')
        self.assertIsNone(report['network'])
        self.assertTrue(any(b['code']=='UNKNOWN_TABLE10_SCREENING' for t in report['tasks'] for b in t['blockers']))
        sheets=read_xlsx(self.data)
        sheets['作用能力'][2][6]=1 # resonance equals impedance; strict gt fails
        doc,_=convert_design(sheets)
        report=design(doc)
        self.assertEqual(report['status'],'no_feasible_design') # slow resource cannot serve both tasks
        self.assertTrue(all(not s['accepted'] for t in report['tasks'] for s in t['selection'] if s['artifact']=='demo-fast'))

    def test_shared_target_counts_are_strict_and_missing_is_unknown(self):
        for field,index in [('max_tracks',0),('max_processing_targets',1)]:
            for value,status in [(2,'no_feasible_design'),(3,'complete'),(None,'blocked')]:
                doc=deepcopy(self.doc)
                doc['artifacts'][index]['attributes'][field]=None if value is None else {'value':value,'unit':'1'}
                with self.subTest(field=field,value=value):
                    report=design(doc)
                    self.assertEqual(report['status'],status)
                    if status!='complete':self.assertIsNone(report['network'])
        self.doc['tasks'][0]['attributes'].pop('target_count')
        self.assertEqual(design(self.doc)['status'],'blocked')

    def test_unknown_possible_combination_blocks_optimality_even_with_known_solution(self):
        self.doc['tasks'][1]['start_s']=3
        self.doc['artifacts'][0]['attributes'].pop('max_tracks')
        report=design(self.doc)
        self.assertEqual(report['status'],'blocked')
        self.assertIsNone(report['network'])
        candidates=report['article_table10']['combinations']
        self.assertTrue(any(c['status']=='pass' and not c['engineering_conflicts'] for c in candidates))
        self.assertTrue(any(c['status']=='unknown' and not c['engineering_conflicts'] for c in candidates))

    def test_serial_endpoints_allow_shared_insight_without_parallel_counts(self):
        self.doc['tasks'][1]['start_s']=5
        for artifact in self.doc['artifacts']:
            artifact['attributes'].pop('max_tracks',None)
            artifact['attributes'].pop('max_processing_targets',None)
        report=design(self.doc)
        self.assertEqual(report['status'],'complete')
        self.assertTrue(all(len(g['chains'])==1 for g in report['article_table10']['selected']['groups']))
        self.assertEqual(report['network_search']['feasible'],2) # repeated manifestation still forbidden

    def test_overlap_is_not_transitive_and_unused_facts_do_not_block(self):
        third=deepcopy(self.doc['artifacts'][-1]);third['id']='third';self.doc['artifacts'].append(third)
        self.doc['tasks'].append(dict(deepcopy(self.doc['tasks'][0]),id='task-3'))
        self.doc['links'].append(dict(deepcopy(self.doc['links'][-1]),**{'to':'third'}))
        chosen=[{'id':f'c{i}','task':task,'start_s':start,'end_s':start+3,
                 'artifacts':{'insight':'demo-eye','coordination':'demo-coordinator','manifestation':action}}
                for i,(task,start,action) in enumerate([('demo-task-1',0,'demo-fast'),('demo-task-2',2,'demo-slow'),('task-3',4,'third')])]
        context=deepcopy(self.doc['contexts'][0]);context['task']='task-3';self.doc['contexts'].append(context)
        # An unused edge and unrelated context with issues must not be consumed.
        self.doc['links'].append(dict(deepcopy(self.doc['links'][0]),**{'from':'demo-slow','to':'demo-fast','blocking_issue_ids':['unused']}))
        self.doc['contexts'].append(dict(task='task-3',artifact='demo-fast',attributes={},source_refs=[],blocking_issue_ids=['unused']))
        report=cooperation(chosen,self.doc)
        self.assertEqual(report['status'],'pass')
        self.assertEqual([len(g['chains']) for g in report['groups']],[1,2,2])

    def test_optional_mode_preserves_legacy_and_invalid_count_rejected(self):
        self.doc['policies']['article_table10']=False
        report=design(self.doc)
        self.assertEqual(report['status'],'complete')
        self.assertEqual(report['network_search']['feasible'],3)
        self.assertNotIn('article_table10',report)
        for value in [1,{'value':2.5,'unit':'1'},{'value':2,'unit':'targets'}]:
            doc=deepcopy(self.doc);doc['policies']['article_table10']=True
            doc['tasks'][0]['attributes']['target_count']=value
            with self.assertRaises(DesignError):design(doc)

    def test_http_real_excel_saves_checks_and_blocked_export_has_no_rdf(self):
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with tempfile.TemporaryDirectory() as directory:
                source=run_import(self.data,'article.xlsx',output_root=Path(directory)/'source')
                self.assertTrue(source['records'])
                def run(data,filename):return run_design(data,filename,output_root=Path(directory))
                with patch('excel_entry.app.run_design',side_effect=run):
                    with urlopen(base+'/article-design-example.xlsx') as response:
                        self.assertEqual(response.read(),self.data)
                    with urlopen(Request(base+'/design-run?article.xlsx',data=self.data,method='POST')) as response:
                        result=json.load(response)
                    self.assertEqual(result['rdf_export']['status'],'exported')
                    saved=json.loads((Path(result['output'])/'design_report.json').read_text(encoding='utf-8'))
                    self.assertEqual(saved['article_table10']['selected']['status'],'pass')
                    with urlopen(base+'/design') as response:
                        page=response.read().decode('utf-8')
                        self.assertIn('article-checks',page)
                from tests.test_rule_review import edit_cell
                # Last row in this fixture's settings enables table10; source
                # parameters remain unchanged, only max_tracks is made unknown.
                sheets=read_xlsx(self.data)
                row=next(r for r,c in sheets['属性'].items() if c.get(4)=='max_tracks')
                missing=edit_cell(self.data,list(sheets).index('属性')+1,f'F{row}',None)
                blocked=run(missing,'unknown.xlsx')
                self.assertEqual(blocked['report']['status'],'blocked')
                self.assertEqual(blocked['rdf_export']['status'],'not_ready')
                self.assertFalse((Path(blocked['output'])/'design.ttl').exists())
        finally:
            server.shutdown();server.server_close();thread.join()
