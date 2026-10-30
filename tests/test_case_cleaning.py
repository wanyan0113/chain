"""Standalone cleaning and consistent case checks at public CLI entry points."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from source_data.case_contract import cleaning_outputs
from source_data.cli import main
from source_data.common import digest, read_json
from source_data.ingest import build_bundle, ingest
from source_data.common import ValidationError
from source_data.validate import validate_bundle

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data/article_source/case_records.json'
MAPPING = ROOT / 'source_mappings/case_fields.json'


class CaseCleaningTests(unittest.TestCase):
    def setUp(self):
        self.document = read_json(RAW)
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.workspace = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.addCleanup(self.workspace.cleanup)
        self.directory = Path(self.workspace.name)

    def call(self, *args):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            status = main(list(map(str, args)))
        return status, json.loads(output.getvalue())

    def save(self):
        path = self.directory / 'input.json'
        path.write_text(json.dumps(self.document, ensure_ascii=False), encoding='utf-8')
        return path

    def test_standalone_clean_is_lossless_and_idempotent(self):
        self.document['table8'][0]['insight_artifact'] = ' R-Eye-A '
        self.document['table8'][0]['target_types'] = [' A ', 'A', 'B']
        snapshot = deepcopy(self.document)
        path = self.save()
        original_hash = digest(path)
        out = self.directory / 'clean'
        status, report = self.call('clean', path, '--output-dir', out)
        self.assertEqual(status, 0)
        self.assertEqual(report['case_contract'], 'passed')
        self.assertEqual(len(report['changes']), 2)
        self.assertTrue(report['findings'])
        cleaned = read_json(out / 'cleaned_case.json')
        self.assertEqual(cleaned['table8'][0]['insight_artifact'], 'R-Eye-A')
        self.assertEqual(cleaned['table8'][0]['target_types'], ['A', 'B'])
        self.assertEqual(cleaned['table9'][3]['max_range_nm'], 0.2)
        self.assertEqual(digest(path), original_hash)
        self.assertEqual(self.document, snapshot)
        self.assertFalse(list(out.glob('*.ttl')))
        second = json.loads(cleaning_outputs(cleaned)['cleaning_report.json'])
        self.assertEqual(second['changes'], [])

    def test_bad_data_rejected_by_clean_ingest_and_validate(self):
        for table, index, key, value in [
            ('table9', 0, 'attunement_probability_percent', 200),
            ('table8', 0, 'longitude_column_raw', 'garbage'),
            ('table8', 0, 'altitude_ft', -1),
        ]:
            with self.subTest(key=key):
                self.document = read_json(RAW)
                self.document[table][index][key] = value
                path = self.save()
                with self.assertRaises(ValidationError):
                    ingest(path, MAPPING)
                bundle = build_bundle(self.document, read_json(MAPPING), input_filename=path.name, input_sha256=digest(path))
                bundle_path = self.directory / 'bundle.json'
                bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding='utf-8')
                for command in ('clean', 'ingest', 'validate'):
                    out = self.directory / (key + command)
                    args = ['validate', bundle_path] if command == 'validate' else [command, path, '--output-dir', out]
                    if command == 'ingest':
                        args += ['--mapping', MAPPING]
                    status, report = self.call(*args)
                    self.assertNotEqual(status, 0)
                    self.assertEqual(report['status'], 'invalid')
                    self.assertFalse(out.exists())
                    if command == 'validate':
                        self.assertEqual(report['validation_scope']['source_integrity'], 'passed')
                        self.assertEqual(report['validation_scope']['case_contract'], 'failed')

    def test_ingest_exports_cleaning_without_rewriting_source(self):
        self.document['table7'][0]['name'] += ' '
        path = self.save()
        out = self.directory / 'ingest'
        status, report = self.call('ingest', path, '--mapping', MAPPING, '--output-dir', out, '--rdf')
        self.assertEqual(status, 0)
        self.assertEqual(report['validation_scope']['case_contract'], 'passed')
        self.assertEqual(read_json(out / 'bundle.json')['metadata']['raw_document'], self.document)
        self.assertEqual(read_json(out / 'cleaned_case.json')['table7'][0]['name'], self.document['table7'][0]['name'].strip())
        self.assertEqual(len(read_json(out / 'cleaning_report.json')['changes']), 1)
        self.assertTrue((out / 'provenance.ttl').exists())

    def test_archive_validation_explicitly_reports_its_scope(self):
        self.document['table9'][0]['attunement_probability_percent'] = 200
        path = self.save()
        bundle = build_bundle(self.document, read_json(MAPPING), input_filename=path.name, input_sha256=digest(path))
        archived = validate_bundle(bundle)
        checked = validate_bundle(bundle, check_case=True)
        self.assertEqual(archived['validation_scope']['case_contract'], 'not_run')
        self.assertEqual(checked['validation_scope']['case_contract'], 'failed')
        self.assertEqual(checked['validation_scope']['source_integrity'], 'passed')

    def test_standalone_unit_conversion_and_large_integer_fidelity(self):
        self.document['units']['distance'] = 'km'
        self.document['table8'][0]['detection_range_nm'] = 1.852
        self.document['table8'][0]['altitude_ft'] = 123456789012345678901234567890
        outputs = cleaning_outputs(self.document)
        cleaned = json.loads(outputs['cleaned_case.json'])
        report = json.loads(outputs['cleaning_report.json'])
        values = {item['path']: item for item in report['normalized_numbers']}
        self.assertAlmostEqual(float(values['table8[0].detection_range_nm']['normalized_value']), 1)
        self.assertEqual(Decimal(values['table8[0].altitude_ft']['normalized_value']), self.document['table8'][0]['altitude_ft'])
        self.assertEqual(Decimal(values['table9[0].attunement_probability_percent']['normalized_value']), Decimal('0.95'))
        self.assertEqual(cleaned['table8'][0]['detection_range_nm'], 1.852)
        self.assertEqual(cleaned['units']['distance'], 'km')


if __name__ == '__main__':
    unittest.main()
