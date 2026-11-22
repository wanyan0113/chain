"""Release fixture contracts remain reproducible without optimizing in the checks."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from fdna.model import ModelError
from fdna.release_acceptance import check_cases,run
from fdna.network_workflow import read_samples


class ReleaseAcceptanceTests(unittest.TestCase):
    directory=Path(__file__).resolve().parents[1]/'data/fdna/v1_acceptance'

    def test_saved_cases_match_their_declared_status_and_exit_codes(self):
        results,outputs=check_cases(self.directory)
        self.assertEqual(len(results),16)
        self.assertTrue(all(r['passed'] for r in results),results)
        self.assertEqual(len([n for n in outputs if n.startswith('案例日志/')]),16)

    def test_observed_fixtures_have_no_clean_predictor_escape_hatch(self):
        read=lambda n:json.loads((self.directory/n).read_text(encoding='utf-8'))
        network=read('network.json');config=read('config.json');rows=read('observed.json')
        self.assertEqual(config['input_role'],'observed')
        self.assertTrue(all('predictor_values' not in r for r in rows))
        self.assertEqual(len(rows),250)
        self.assertEqual(rows,read_samples(self.directory/'observed_wide.csv',network))
        self.assertEqual(rows,read_samples(self.directory/'observed_long.csv',network,'long'))

    def test_fixture_hash_mismatch_fails_before_any_optimization(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);(path/'manifest.json').write_text('{"data.json":"not_a_hash"}',encoding='utf-8')
            (path/'data.json').write_text('[]',encoding='utf-8')
            with patch('fdna.release_acceptance.fit_network',side_effect=AssertionError('must not fit')):
                with self.assertRaisesRegex(ModelError,'哈希不一致'):run(path)


if __name__=='__main__':unittest.main()
