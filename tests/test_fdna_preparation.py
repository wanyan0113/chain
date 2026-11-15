"""Real extraction-to-regression handoff, lineage, run splits and missing SE."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from fdna import ModelError, load_model
from fdna.__main__ import main
from fdna.fitting import fit_parameters
from fdna.preparation import prepare_fit_samples
from fdna.structure import digest

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT/'data/fdna/arcane_case.json'


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.model=load_model(CASE)
        self.structure,self.plan,self.batch,self.extraction,self.calibration,self.partition=[
            json.loads(CASE.with_name(n).read_text(encoding='utf-8')) for n in (
                'arcane_structure.json','experiment_plan_check.json','indicator_records_check.json',
                'extraction_check.json','se_calibration_check.json','fit_partition_check.json')]

    def prepare(self):
        return prepare_fit_samples(self.model,self.structure,self.plan,self.batch,
                                   self.extraction,self.calibration,self.partition)

    def rebind(self):
        self.extraction['batch_sha256']=digest(self.batch)
        self.partition.update(batch_sha256=digest(self.batch),extraction_sha256=digest(self.extraction))

    def test_measured_intermediate_se_is_preserved_with_lineage_and_no_propagation(self):
        before=deepcopy((self.model,self.structure,self.plan,self.batch,self.extraction,self.calibration,self.partition))
        report=self.prepare()
        row=report['samples']['records'][0]
        self.assertEqual(row['split'],'train')
        self.assertEqual(row['node_values']['S1'],75)
        self.assertEqual(row['node_values']['D2'],75)
        self.assertEqual(row['node_values']['F7'],50)
        self.assertEqual(report['samples']['data_kind'],'method_check')
        self.assertEqual(report['counts']['holdout_runs'],0)
        self.assertEqual(report['lineage'][0]['closures'],['closure_check','closure_check_KC2'])
        self.assertEqual(report['samples_sha256'],digest(report['samples']))
        self.assertTrue(all(not r['base_solver_sample_minimum_met'] for r in report['local_sample_counts']))
        self.assertFalse(report['case_reproduction_ready'])
        self.assertEqual((self.model,self.structure,self.plan,self.batch,self.extraction,self.calibration,self.partition),before)

    def test_unknown_calibration_stays_null_and_cannot_be_omitted(self):
        self.calibration['rules']['S5']=None
        report=self.prepare()
        self.assertEqual(report['status'],'blocked')
        self.assertIsNone(report['samples']['records'][0]['node_values']['S5'])
        self.assertEqual(report['lineage'][0]['unknown_nodes'][0]['node'],'S5')
        f7=next(r for r in report['local_sample_counts'] if r['receiver']=='F7')
        self.assertEqual(f7['complete_local_rows']['train'],0)
        self.calibration=None
        with self.assertRaises(ModelError): self.prepare()

    def test_partitions_are_complete_bound_to_inputs_and_split_whole_runs(self):
        original=deepcopy(self.partition)
        for change in ('missing','extra','value','batch','groups'):
            self.partition=deepcopy(original)
            if change=='missing': self.partition['run_splits'].clear()
            elif change=='extra': self.partition['run_splits']['other']='holdout'
            elif change=='value': self.partition['run_splits']['run_check']='automatic'
            elif change=='batch': self.partition['batch_sha256']='0'*64
            else: self.partition['extraction_sha256']='0'*64
            with self.subTest(change=change),self.assertRaises(ModelError): self.prepare()
        self.partition=deepcopy(original)
        self.partition['run_splits']['run_check']='holdout'
        self.assertEqual(self.prepare()['samples']['records'][0]['split'],'holdout')

    def test_closure_reuse_rejected_even_with_distinct_sample_identifiers(self):
        duplicate=deepcopy(self.extraction['groups'][0])
        duplicate['id']='duplicate_group'
        self.extraction['groups'].append(duplicate)
        self.rebind()
        with self.assertRaises(ModelError): self.prepare()

    def test_separate_groups_from_same_run_cannot_land_in_different_splits(self):
        group=deepcopy(self.extraction['groups'][0])
        self.extraction['groups'][0]['closures']=[group['closures'][0]]
        group.update(id='second_task_group',closures=[group['closures'][1]])
        self.extraction['groups'].append(group)
        self.partition['run_splits']['run_check']='holdout'
        self.rebind()
        report=self.prepare()
        self.assertTrue(all(r['split']=='holdout' for r in report['samples']['records']))
        self.assertEqual(report['counts']['holdout_runs'],1)
        self.assertEqual(report['counts']['holdout_records'],2)

    def test_no_records_or_implicit_holdout_created(self):
        self.extraction['groups']=[]
        self.partition['run_splits']={}
        self.rebind()
        report=self.prepare()
        self.assertEqual(report['status'],'no_records')
        self.assertEqual(report['samples']['records'],[])

    @unittest.skipUnless(importlib.util.find_spec('scipy'),'Optional SciPy dependency')
    def test_existing_fitter_accepts_export_and_reports_insufficient_samples(self):
        settings=json.loads(CASE.with_name('fit_settings_lbfgsb_check.json').read_text(encoding='utf-8'))
        report=fit_parameters(self.model,self.prepare()['samples'],settings)
        self.assertEqual(report['status'],'blocked')
        self.assertTrue(all(r['status']=='insufficient_samples' for r in report['receivers']))

    @unittest.skipUnless(importlib.util.find_spec('scipy'),'Optional SciPy dependency')
    def test_multiple_run_handoff_and_holdout_facts_do_not_influence_optimization(self):
        original_run=deepcopy(self.batch['runs'][0])
        original_group=deepcopy(self.extraction['groups'][0])
        self.batch['runs']=[]
        self.extraction['groups']=[]
        self.partition['run_splits']={}
        for i in range(14):
            run=deepcopy(original_run)
            run['id']='artificial_run_'+str(i)
            for closure in run['closures']:
                closure['id']+='_'+str(i)
                for event in closure['events']:
                    event['id']+='_'+str(i)
                    for fact in event['facts']:
                        if fact['name']=='effectiveness': fact['value']=.3+i*.02
                        elif fact['name']=='discovery_time': fact['value']=10+i
            group=deepcopy(original_group)
            group.update(id='artificial_sample_'+str(i),run=run['id'],closures=[c['id'] for c in run['closures']])
            self.batch['runs'].append(run)
            self.extraction['groups'].append(group)
            self.partition['run_splits'][run['id']]='train' if i<12 else 'holdout'
        self.rebind()
        prepared=self.prepare()
        self.assertEqual(prepared['counts']['train_runs'],12)
        self.assertEqual(prepared['counts']['holdout_runs'],2)
        self.assertTrue(all(r['base_solver_sample_minimum_met'] for r in prepared['local_sample_counts']))
        settings=json.loads(CASE.with_name('fit_settings_lbfgsb_check.json').read_text(encoding='utf-8'))
        settings.update(starts=2,max_nfev=200)
        original=fit_parameters(self.model,prepared['samples'],settings)
        for run in self.batch['runs'][-2:]:
            for closure in run['closures']:
                for event in closure['events']:
                    for fact in event['facts']:
                        if fact['name']=='effectiveness': fact['value']=.9
        self.rebind()
        changed=fit_parameters(self.model,self.prepare()['samples'],settings)
        for a,b in zip(original['receivers'],changed['receivers']):
            self.assertIn('parameters',a)
            self.assertEqual(a['attempts'],b['attempts'])
        self.assertNotEqual(original['receivers'][0]['metrics']['holdout'],changed['receivers'][0]['metrics']['holdout'])

    def test_cli_outputs_exact_samples_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'out'
            args=['prepare-fit',str(CASE),'--output-dir',str(out)]
            for flag,name in [('structure','arcane_structure.json'),('plan','experiment_plan_check.json'),
                              ('batch','indicator_records_check.json'),('extraction','extraction_check.json'),
                              ('calibration','se_calibration_check.json'),('partition','fit_partition_check.json')]:
                args.extend(['--'+flag,str(CASE.with_name(name))])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args),0)
                self.assertEqual(main(args),2)
            samples=json.loads((out/'fit_samples.json').read_text(encoding='utf-8'))
            report=json.loads((out/'fdna_report.json').read_text(encoding='utf-8'))
            self.assertEqual(samples,report['samples'])
            self.assertEqual(report['lineage'][0]['run'],'run_check')
            self.assertFalse((out/'fdna_summary.md').exists())


if __name__=='__main__': unittest.main()
