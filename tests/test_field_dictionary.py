"""Source catalogue must preserve distinctions already present in the archive."""
from pathlib import Path
import unittest

from source_data.dictionary import field_dictionary, dictionary_markdown
from source_data.ingest import ingest

ROOT = Path(__file__).resolve().parent.parent


class FieldDictionaryTests(unittest.TestCase):
    def test_complete_coverage_and_separate_issue_scopes(self):
        bundle = ingest(ROOT / "data/article_source/raw_tables.json", ROOT / "source_mappings/article_source.json")
        result = field_dictionary(bundle)
        self.assertEqual(result["counts"]["fields"], len(bundle["fields"]))
        self.assertEqual(sum(row["value_count"] for row in result["fields"]), len(bundle["values"]))
        self.assertEqual(result["counts"]["headers_not_provided"], 31)
        self.assertEqual(len(result["issues"]), 13)
        self.assertEqual(len(result["gaps"]), 3)
        # A collection-level issue does not become a direct field-level assertion.
        row = next(row for row in result["fields"] if row["table_path"] == "table7" and row["original_key"] == "count")
        self.assertEqual(row["field_issues"], [])
        self.assertTrue(row["table_issues"])
        self.assertEqual(row["unit"]["status"], "unknown")
        self.assertTrue(row["sources"])
        self.assertIn("| table7 | count |", dictionary_markdown(result))


if __name__ == "__main__":
    unittest.main()
