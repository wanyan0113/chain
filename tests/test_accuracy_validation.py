import csv
from copy import deepcopy
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from workflow_tools.accuracy_validation import Evidence,FIELDS,load,summarize,wilson
from workflow_tools.validate_accuracy_model import matrix,operate,rows,freeze,execute
from workflow_tools.audit_accuracy_data import reference_endpoint

POLICY=dict(alpha=.05,absolute_error_tolerance=.05,minimum_stratum_class_samples=1)


class AccuracyValidationTests(unittest.TestCase):
    def spec(self,name):return next(c for c in matrix() if c['id']==name)
    def finite_evidence(self,case,patterns):
        data=Evidence(len(case['stages']))
        for split in ('calibration','evaluation'):
            for k in range(3000):
                for faults in patterns:
                    gold=k%2;value=gold;shadow=[];serial=[]
                    for i,fault in enumerate(faults):
                        shadow.append(str(operate(case,i,gold,gold,fault)))
                        if split=='evaluation':value=operate(case,i,value,gold,fault);serial.append(str(value))
                    data.add(dict(split=split,gold=str(gold),stratum='all',fault_bits=''.join(str(int(x)) for x in faults),
                        shadow_outputs=''.join(shadow).replace('-1','x'),serial_outputs=''.join(serial).replace('-1','x')))
        return data

    def test_independent_absorbing_control_and_conditional_chain_rule(self):
        case=self.spec('B-independent-2');data=self.finite_evidence(case,[(0,0),(0,1),(1,0),(1,1)])
        r=summarize(data,case,POLICY)
        self.assertEqual(r['prediction'],.25);self.assertEqual(r['observed'],.25)
        self.assertEqual(r['decision'],'equivalent_within_tolerance')
        self.assertEqual(r['conditional_chain_rule_product'],.25)
        self.assertAlmostEqual(r['shadow_error_correlations'][0]['phi'],0)

    def test_independent_flip_errors_recover_and_break_endpoint_product(self):
        case=self.spec('F-flip-2');data=self.finite_evidence(case,[(0,0),(0,1),(1,0),(1,1)])
        r=summarize(data,case,POLICY)
        self.assertEqual(r['prediction'],.25);self.assertEqual(r['observed'],.5)
        self.assertEqual(r['decision'],'systematic_underprediction')
        self.assertEqual(r['conditional_chain_rule_product'],.25)
        self.assertGreater(r['recovery_counts'][1],0)
        self.assertEqual(r['shadow_error_correlations'][0]['phi'],0)

    def test_repeated_operator_exposes_perfect_error_correlation(self):
        case=self.spec('E-repeated-2');r=summarize(self.finite_evidence(case,[(0,0),(1,1)]),case,POLICY)
        self.assertEqual(r['shadow_error_correlations'][0]['phi'],1)
        self.assertEqual(r['observed'],.5);self.assertEqual(r['prediction'],.25)

    def test_exclusive_errors_overpredict_and_population_mismatch_blocks(self):
        case=self.spec('D-exclusive-errors-2')
        r=summarize(self.finite_evidence(case,[(1,0),(0,1)]),case,POLICY)
        self.assertEqual(r['prediction'],.25);self.assertEqual(r['observed'],0)
        self.assertEqual(r['decision'],'systematic_overprediction')
        shifted=deepcopy(case);shifted['population_evaluation']['hard_probability']=.9
        r=summarize(self.finite_evidence(case,[(0,0),(1,1)]),shifted,POLICY)
        self.assertEqual(r['decision'],'blocked_population_mismatch')

    def test_repair_clean_input_ceiling_is_not_zero_error_correlation(self):
        case=self.spec('F-repair-2');r=summarize(self.finite_evidence(case,[(0,0),(1,0),(1,1)]),case,POLICY)
        self.assertIsNone(r['shadow_error_correlations'][0]['phi'])
        self.assertEqual(r['shadow_error_correlations'][0]['reason'],'zero_variance')
        self.assertGreater(r['observed'],r['prediction'])

    def test_exact_analytic_mechanisms_and_reproducible_subjects(self):
        self.assertAlmostEqual(reference_endpoint(self.spec('F-flip-2')),.58)
        self.assertAlmostEqual(reference_endpoint(self.spec('F-repair-2')),.96)
        self.assertAlmostEqual(reference_endpoint(self.spec('D-exclusive-errors-2')),.3)
        case=self.spec('D-shared-difficulty-4')
        self.assertEqual(list(rows(case,17,10,10)),list(rows(case,17,10,10)))
        labels=list(rows(case,17,10,10))
        self.assertEqual(len({r['sample_id'] for r in labels}),20)

    def test_missing_labels_duplicate_subjects_and_split_leakage_are_rejected(self):
        case=self.spec('A-observe-1');valid=list(rows(case,1,2,2))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'data.csv.gz'
            for records in ([valid[0],valid[0],valid[-1]], [dict(valid[0],gold='')]+valid[1:],
                            [dict(valid[0],serial_outputs='0')]+valid[1:]):
                with gzip.open(path,'wt',encoding='utf-8',newline='') as handle:
                    writer=csv.DictWriter(handle,fieldnames=FIELDS);writer.writeheader();writer.writerows(records)
                with self.assertRaises(ValueError):load(path,1)

    def test_protocol_freeze_guards_source_and_empty_ci_is_not_zero(self):
        self.assertIsNone(wilson(0,0))
        self.assertGreater(wilson(0,100)[1],0)
        self.assertLess(wilson(100,100)[0],1)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'protocol.json';freeze(path)
            with self.assertRaises(ValueError):freeze(path)
            p=json.loads(path.read_text(encoding='utf-8'));p['source_hashes']['workflow_tools/accuracy_validation.py']='bad'
            path.write_text(json.dumps(p),encoding='utf-8')
            with self.assertRaises(ValueError):execute(path,Path(directory)/'output')
            self.assertFalse((Path(directory)/'output').exists())


if __name__=='__main__':unittest.main()
