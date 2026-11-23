import unittest

import numpy as np

from workflow_tools.benchmark_prepared_prediction import compare_arrays


class PreparedBenchmarkTests(unittest.TestCase):
    def test_equal_nan_positions_and_bool_masks_pass(self):
        values=dict(numbers=np.array([1.,np.nan]),mask=np.array([True,False]))
        result=compare_arrays(values,{k:v.copy() for k,v in values.items()})
        self.assertTrue(result['bitwise_equal_including_missing'])
        self.assertEqual(result['numeric_values_compared'],2);self.assertEqual(result['mask_values_compared'],2)

    def test_reject_numeric_mask_dtype_shape_and_signed_zero_changes(self):
        for a,b in [(np.array([1.]),np.array([2.])),(np.array([True]),np.array([False])),
                    (np.array([1.]),np.array([1.],dtype=np.float32)),(np.array([1.]),np.array([[1.]])),
                    (np.array([0.]),np.array([-0.])),(np.array([np.nan,1.]),np.array([1.,np.nan]))]:
            with self.subTest(a=a,b=b),self.assertRaises(ValueError):compare_arrays({'v':a},{'v':b})


if __name__=='__main__':unittest.main()
