from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

import numpy as np

from fdna.absolute_seed_propagation import PROTOCOL,replay_absolute,summarize,run
from fdna.refit_propagation import replay
from fdna.model import ModelError


class AbsoluteSeedTests(unittest.TestCase):
    def setUp(self):
        self.net=dict(id='test',nodes=['A','B','F'],edges=[dict(feeder='A',receiver='B'),dict(feeder='B',receiver='F')])
        self.rows=[dict(fit_group='g',base_case_id='one',split='holdout',node_values=dict(A=80.,B=999.,F=999.))]
        self.jobs=[('g','base')];self.cases=['one'];self.pairs=[('A','B'),('B','F')];self.fractions=[0,.5,1]
        self.ranges={('g','B','A'):(70,90),('g','F','B'):(80,95)}
        self.records=[dict(fits=[dict(node=n,fit_group='g',model='base',status='fitted',parameters=[dict(feeder=p,alpha=a,beta=50)],delta=None)
            for n,p,a in [('B','A',alpha),('F','B',.8)]]) for alpha in (.5,.6)]
        self.relative=[replay(self.net,r,self.rows,self.jobs,self.cases,self.pairs,self.fractions,self.ranges) for r in self.records]
        self.reference=self.relative[0]['baseline']
    def fixed(self,record,rows=None,reference=None,fractions=None):
        return replay_absolute(self.net,record,rows or self.rows,self.jobs,self.cases,self.pairs,
            self.fractions if fractions is None else fractions,self.ranges,self.reference if reference is None else reference)

    def test_clamps_identical_across_seeds_and_controls_match(self):
        a,b=[self.fixed(r) for r in self.records]
        np.testing.assert_array_equal(a['forced'],b['forced'])
        for key in ('forced','changed','decrease'):np.testing.assert_array_equal(a[key],self.relative[0][key])
        np.testing.assert_array_equal(b['changed'][:,0],self.relative[1]['changed'][:,0])
        np.testing.assert_array_equal(b['changed'][:,:,0],self.relative[1]['changed'][:,:,0])

    def test_fraction_one_can_increase_output_and_is_not_clipped(self):
        b=self.fixed(self.records[1]);key=0,1,2,0
        self.assertTrue(b['reference_exceeds_own'][key]);self.assertTrue(b['negative_decrease'][key])
        self.assertAlmostEqual(b['decrease'][key],-1.6)
        self.assertAlmostEqual(self.relative[1]['decrease'][key],0.)

    def test_paired_decomposition_closes_with_baseline_and_output_separated(self):
        runs=[self.fixed(r) for r in self.records]
        fixed={k:np.stack([r[k] for r in runs]) for k in runs[0]}
        relative={k:np.stack([r[k] for r in self.relative]) for k in self.relative[0]}
        axes=dict(jobs=self.jobs,pairs=self.pairs,fractions=self.fractions,seeds=[1,2],nodes=['A','B','F'],base_cases=self.cases)
        summary,means,error=summarize(fixed,relative,axes);self.assertLess(error,1e-12)
        r=next(r for r in means if r['seed']==2 and r['intervened_node']=='B' and r['retained_fraction']==1)
        self.assertAlmostEqual(r['fixed'],r['baseline_contrast']-r['output_contrast'])
        self.assertAlmostEqual(r['relative']-r['fixed'],r['reference_contribution'])

    def test_failed_model_is_unavailable_and_keeps_denominator(self):
        r=deepcopy(self.records[1]);r['fits'][1]['status']='numerical_failure'
        result=self.fixed(r);self.assertFalse(result['valid'].any());self.assertTrue(np.isnan(result['decrease']).all())

    def test_internal_truth_not_used_and_source_not_mutated(self):
        before=deepcopy((self.records,self.rows,self.reference));rows=deepcopy(self.rows);rows[0]['node_values'].update(B=-999,F=-999)
        np.testing.assert_array_equal(self.fixed(self.records[0])['decrease'],self.fixed(self.records[0],rows)['decrease'])
        self.assertEqual(self.records,before[0]);self.assertEqual(self.rows,before[1]);np.testing.assert_array_equal(self.reference,before[2])

    def test_invalid_reference_fraction_and_duplicate_panel_rejected(self):
        with self.assertRaises(ModelError):self.fixed(self.records[0],reference=np.zeros((1,1,2)))
        for fractions in ([True],[0,0],[float('nan')],[-.1],[1.1]):
            with self.subTest(fractions=fractions),self.assertRaises(ModelError):self.fixed(self.records[0],fractions=fractions)
        with self.assertRaises(ModelError):self.fixed(self.records[0],rows=self.rows*2)

    def test_output_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'keep').write_text('keep');protocol=out/'protocol.json';protocol.write_text(json.dumps(PROTOCOL))
            with self.assertRaises(ModelError):run('missing',protocol,out)
            self.assertEqual((out/'keep').read_text(),'keep')


if __name__=='__main__':unittest.main()
