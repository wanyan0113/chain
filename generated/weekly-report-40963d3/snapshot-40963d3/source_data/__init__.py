"""Standalone, lossless source management; no project or environment imports."""

__version__ = "1.0.0"

from .ingest import ingest
from .load import load_source
from .query import query_issues, query_records, query_unconfirmed
from .validate import validate_bundle

__all__ = ["ingest", "load_source", "validate_bundle", "query_records", "query_issues", "query_unconfirmed"]
