"""A source bundle becomes only generic source classes and references."""
from pathlib import Path

from source_data.common import InputError, read_json
from source_data.load import REFERENCES, bundle_graph


def source_to_rdf(bundle):
    mapping = read_json(Path(__file__).resolve().parent.parent / "adapter_mappings/source_metadata.json")
    if mapping != {"format_version": "1.0", "namespace": "urn:source-data:", "references": REFERENCES}:
        raise InputError("来源 RDF 映射与来源契约不一致")
    return bundle_graph(bundle)
