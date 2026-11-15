"""Independent residual arithmetic, split isolation, report integrity and plotting."""
from contextlib import redirect_stdout
from copy import deepcopy
import csv
import importlib.util
import io
import json
import math
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, load_model
from fdna.__main__ import main
from fdna.residuals import diagnose_residuals, plot_residuals, residual_csv
from fdna.structure import digest

ROOT=Path(__file__).resolve().parents[1]
CASE=ROOT/'data/fdna/arcane_case.json'


class ResidualTests(unittest.TestCase):
    def setUp(self):
        self.model=load_model(CASE)
        self.model['edges']=[deepcopy(next(e for e in self.model['edges'] if e['feeder']=='S1' and e['receiver']=='S5'))]
        self.model['case_extension']['delta']={'S5':None}
        self.model['reported_fit']=[r for r in self.model['reported_fit'] if r['receiver']=='S5']
        self.settings=json.loads(CASE.with_name('fit_settings_lbfgsb_check.json').read_text(encoding='utf-8'))
        self.samples={'format_version':'1.0','id':'independent_residual_arithmetic','data_kind':'method_check',
                      'basis':'Artificial hand-computed residual fixture, not original records',
                      'case_sha256':digest(self.model),'records':[]}
        metrics={}
        for split,grid,residuals in [('train',[0,40,80],[-2,0,2]),('holdout',[10,90],[-1,3])]:
            points=[]
            for x,error in zip(grid,residuals):
                prediction=min(60+.4*x,x+20)
                observed=prediction-error
                identifier=f'{split}:{x}'
                self.samples['records'].append({'id':identifier,'split':split,'node_values':{'S1':x,'S5':observed}})
                points.append({'id':identifier,'observed':observed,'predicted':prediction,'residual':error})
            metrics[split]={'count':len(points),'rmse':math.sqrt(sum(r*r for r in residuals)/len(points)),
                            'max_absolute_error':max(abs(r) for r in residuals),'rows':points}
        self.fit={'status':'complete','case':self.model['id'],'data_kind':'method_check',
                  'samples_snapshot':self.samples,'settings_snapshot':self.settings,
                  'receivers':[{'receiver':'S5','status':'fitted','feeders':['S1'],
                                'parameters':[{'feeder':'S1','alpha':.4,'beta':20}], 'delta':None,'metrics':metrics}]}
        self.rebind()

    def rebind(self):
        self.fit['input_sha256']=digest({'case':self.model,'samples':self.samples,'settings':self.settings})

    def test_hand_calculated_statistics_and_residual_sign_by_split(self):
        before=deepcopy((self.model,self.fit))
        report=diagnose_residuals(self.model,self.fit)
        train,holdout=report['series']
        self.assertAlmostEqual(train['metrics']['rmse'],math.sqrt(8/3))
        self.assertAlmostEqual(train['metrics']['mae'],4/3)
        self.assertEqual(train['metrics']['mean_residual'],0)
        self.assertAlmostEqual(holdout['metrics']['rmse'],math.sqrt(5))
        self.assertEqual(holdout['metrics']['mae'],2)
        self.assertEqual(holdout['metrics']['mean_residual'],1)
        self.assertEqual((self.model,self.fit),before)
        self.assertFalse(report['case_reproduction_ready'])
        self.assertIsNone(report['policies']['pseudo_r_squared'])
        rows=list(csv.DictReader(io.StringIO(residual_csv(report))))
        self.assertEqual(len(rows),5)
        self.assertEqual(rows[0]['split'],'train')
        self.assertEqual(float(rows[0]['residual']),-2)

    def test_missing_holdout_and_insufficient_samples_not_fabricated(self):
        self.samples['records']=self.samples['records'][:3]
        self.fit['receivers'][0]['metrics']['holdout']=None
        self.rebind()
        report=diagnose_residuals(self.model,self.fit)
        self.assertEqual(report['series'][1]['status'],'no_samples')
        self.samples['records']=self.samples['records'][:1]
        self.fit.update(status='blocked',receivers=[{'receiver':'S5','status':'insufficient_samples'}])
        self.rebind()
        report=diagnose_residuals(self.model,self.fit)
        self.assertEqual(report['status'],'blocked')
        self.assertTrue(all(s['metrics'] is None for s in report['series']))

    def test_nonconverged_fit_keeps_status_and_still_audits_predictions(self):
        self.fit['status']='blocked'
        self.fit['receivers'][0]['status']='iteration_limit'
        report=diagnose_residuals(self.model,self.fit)
        self.assertEqual(report['source_fit_status'],'blocked')
        self.assertTrue(all(s['fit_status']=='iteration_limit' for s in report['series']))

    def test_tampered_rows_metrics_parameters_and_snapshots_rejected(self):
        before=deepcopy(self.fit)
        for change in ('residual','prediction','observation','parameter','count','summary','duplicate','split','hash','missing','status','bool'):
            self.fit=deepcopy(before)
            receiver=self.fit['receivers'][0]
            if change=='residual': receiver['metrics']['train']['rows'][0]['residual']*= -1
            elif change=='prediction': receiver['metrics']['train']['rows'][0]['predicted']+=1
            elif change=='observation': receiver['metrics']['train']['rows'][0]['observed']+=1
            elif change=='parameter': receiver['parameters'][0]['beta']=21
            elif change=='count': receiver['metrics']['train']['count']=4
            elif change=='summary': receiver['metrics']['train']['rmse']=0
            elif change=='duplicate': receiver['metrics']['train']['rows'].append(deepcopy(receiver['metrics']['train']['rows'][0]))
            elif change=='split': receiver['metrics']['holdout']['rows'][0]['id']='train:0'
            elif change=='hash': self.fit['input_sha256']='0'*64
            elif change=='missing': receiver['metrics']['train']['rows'].pop()
            elif change=='status': self.fit['status']='blocked'
            else: receiver['parameters'][0]['alpha']=True
            with self.subTest(change=change),self.assertRaises(ModelError): diagnose_residuals(self.model,self.fit)

    def test_additive_delta_predictions_are_audited_without_clipping(self):
        settings=self.fit['settings_snapshot']
        settings.update(model='fdna_additive_delta',delta_policy='per_receiver_bounded',delta_bounds={'S5':[-10,10]},output_policy='no_clipping')
        receiver=self.fit['receivers'][0]
        receiver['delta']=2
        for split in ('train','holdout'):
            for point in receiver['metrics'][split]['rows']:
                point['predicted']+=2
                point['residual']+=2
            errors=[p['residual'] for p in receiver['metrics'][split]['rows']]
            receiver['metrics'][split].update(rmse=math.sqrt(sum(r*r for r in errors)/len(errors)),max_absolute_error=max(abs(r) for r in errors))
        self.rebind()
        report=diagnose_residuals(self.model,self.fit)
        self.assertEqual(report['series'][0]['metrics']['mean_residual'],2)

    @unittest.skipUnless(importlib.util.find_spec('matplotlib'),'Optional matplotlib')
    def test_plots_are_png_for_data_and_empty_splits(self):
        report=diagnose_residuals(self.model,self.fit)
        self.assertTrue(plot_residuals(report).startswith(b'\x89PNG\r\n\x1a\n'))
        report['series'][1].update(rows=[],metrics=None,status='no_samples')
        self.assertTrue(plot_residuals(report).startswith(b'\x89PNG\r\n\x1a\n'))

    def test_cli_reads_existing_report_exports_csv_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            case,fit=folder/'case.json',folder/'fit.json'
            case.write_text(json.dumps(self.model),encoding='utf-8')
            fit.write_text(json.dumps(self.fit),encoding='utf-8')
            args=['residuals',str(case),'--fit-report',str(fit),'--output-dir',str(folder/'out')]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args),0)
                self.assertEqual(main(args),2)
            report=json.loads((folder/'out/fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['fit_snapshot'],self.fit)
            self.assertTrue((folder/'out/residuals.csv').is_file())


if __name__=='__main__': unittest.main()
