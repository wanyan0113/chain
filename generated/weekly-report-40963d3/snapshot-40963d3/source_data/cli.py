"""JSON stdout CLI: ingest, validate, export and independently query sources."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .common import InputError, ValidationError, dumps, read_json, report, write_outputs
from .ingest import ingest
from .load import bundle_graph, load_source
from .query import query_issues, query_materials, query_records, query_unconfirmed
from .validate import validate_bundle
from .case_contract import cleaning_outputs, is_case_mapping


def validate_entry(bundle, **kwargs):
    result = validate_bundle(bundle, **kwargs)
    if not result['counts']['ERROR'] and is_case_mapping(bundle['metadata']['mapping']):
        result = validate_bundle(bundle, check_case=True, **kwargs)
    return result


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(message)


def require_valid(result):
    if result["counts"]["ERROR"]:
        raise ValidationError("; ".join(result["errors"]))


def main(argv=None):
    parser = JsonParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=JsonParser)
    imp = sub.add_parser("ingest", help="只读导入，输出来源包和报告")
    imp.add_argument("input", type=Path)
    imp.add_argument("--mapping", type=Path, required=True)
    imp.add_argument("--output-dir", type=Path, required=True)
    imp.add_argument("--overwrite", action="store_true")
    imp.add_argument("--rdf", action="store_true", help="同时导出通用来源 RDF")
    val = sub.add_parser("validate")
    val.add_argument("bundle", type=Path)
    val.add_argument("--original", type=Path)
    clean = sub.add_parser('clean', help='独立校验和清洗案例JSON，不生成本体实例')
    clean.add_argument('input', type=Path)
    clean.add_argument('--output-dir', type=Path, required=True)
    clean.add_argument('--overwrite', action='store_true')
    export = sub.add_parser("export")
    export.add_argument("bundle", type=Path)
    export.add_argument("--output-dir", type=Path, required=True)
    export.add_argument("--overwrite", action="store_true")
    dictionary = sub.add_parser("dictionary", help="生成资料管理层字段目录")
    dictionary.add_argument("bundle", type=Path)
    dictionary.add_argument("--output-dir", type=Path, required=True)
    dictionary.add_argument("--overwrite", action="store_true")
    query = sub.add_parser("query")
    query.add_argument("inputs", nargs="+", type=Path)
    query.add_argument("--kind", choices=["records", "issues", "unconfirmed", "materials"], default="records")
    for name in ("material", "table", "record", "status"):
        query.add_argument("--" + name)
    try:
        args = parser.parse_args(argv)
        if args.command == "ingest":
            bundle = ingest(args.input, args.mapping)
            result = validate_entry(bundle, original_path=args.input)
            require_valid(result)
            outputs = {"bundle.json": json.dumps(bundle, ensure_ascii=False, indent=2) + "\n",
                       "validation_report.json": json.dumps(result, ensure_ascii=False, indent=2) + "\n"}
            if is_case_mapping(bundle['metadata']['mapping']):
                outputs.update(cleaning_outputs(bundle['metadata']['raw_document']))
            if args.rdf:
                # Export uses the fixed source RDF contract; no adapter or domain import.
                outputs["provenance.ttl"] = bundle_graph(bundle).serialize(format="turtle")
            write_outputs(args.output_dir, outputs, overwrite=args.overwrite, protected=[args.input, args.mapping])
        elif args.command == "validate":
            result = validate_entry(read_json(args.bundle), original_path=args.original)
            print(dumps(result))
            return int(bool(result["counts"]["ERROR"]))
        elif args.command == 'clean':
            outputs = cleaning_outputs(read_json(args.input))
            write_outputs(args.output_dir, outputs, overwrite=args.overwrite, protected=[args.input])
            result = json.loads(outputs['cleaning_report.json'])
        elif args.command == "dictionary":
            from .dictionary import field_dictionary, dictionary_markdown
            catalogue = field_dictionary(read_json(args.bundle))
            write_outputs(args.output_dir, {
                "field_dictionary.json": json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n",
                "field_dictionary.md": dictionary_markdown(catalogue)},
                overwrite=args.overwrite, protected=[args.bundle])
            result = {"status": catalogue["status"], "counts": catalogue["counts"]}
        elif args.command == "export":
            bundle = read_json(args.bundle)
            result = validate_entry(bundle)
            require_valid(result)
            write_outputs(args.output_dir, {"provenance.ttl": bundle_graph(bundle).serialize(format="turtle")},
                          overwrite=args.overwrite, protected=[args.bundle])
        else:
            if (args.kind != "records" and any((args.material, args.table, args.record))) or (args.kind != "issues" and args.status):
                raise InputError("查询过滤条件与 --kind 不匹配")
            graph, result = load_source(args.inputs)
            require_valid(result)
            if args.kind == "records":
                rows = query_records(graph, material=args.material, table=args.table, record=args.record)
            elif args.kind == "issues":
                rows = query_issues(graph, status=args.status)
            elif args.kind == "materials":
                rows = query_materials(graph)
            else:
                rows = query_unconfirmed(graph)
            result = {**result, "count": len(rows), args.kind: rows}
        print(dumps(result))
        return 0
    except Exception as exc:
        print(dumps(report([str(exc)])))
        print(str(exc), file=sys.stderr)
        return 1 if isinstance(exc, ValidationError) else 2


if __name__ == "__main__":
    raise SystemExit(main())
