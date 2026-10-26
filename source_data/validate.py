"""Validate structure, referential integrity and preservation of source JSON."""
from __future__ import annotations

from collections import Counter
from datetime import date
import re

from jsonschema import Draft202012Validator

from .common import COLLECTIONS, MODULE, digest, dumps, read_json, report
from .ingest import build_bundle


def validate_bundle(bundle, *, original_path=None):
    errors = [f"{'.'.join(map(str, error.absolute_path))}: {error.message}"
              for error in Draft202012Validator(read_json(MODULE / "schema.json")).iter_errors(bundle)]
    if errors:
        return report(errors)
    all_ids = [obj["id"] for name in COLLECTIONS for obj in bundle[name]]
    duplicate_ids = [key for key, count in Counter(all_ids).items() if count > 1]
    if duplicate_ids:
        errors.append(f"重复标识：{duplicate_ids}")
    metadata = bundle["metadata"]
    if not re.fullmatch(r"[0-9a-f]{64}", metadata["input_sha256"]):
        errors.append("转录摘要必须为 SHA256")
    for material in bundle["materials"]:
        try:
            if material["checked_on"] is not None:
                date.fromisoformat(material["checked_on"])
        except ValueError:
            errors.append("核对日期格式错误")
    try:
        expected = build_bundle(metadata["raw_document"], metadata["mapping"],
            input_filename=metadata["input_filename"], input_sha256=metadata["input_sha256"])
        if dumps(expected) != dumps(bundle):
            errors.append("来源包与原始快照/声明式映射不一致（包括原值、引用、状态或规范化政策）")
        if original_path is not None:
            if digest(original_path) != metadata["input_sha256"]:
                errors.append("原文件字节摘要已变化")
            if dumps(read_json(original_path)) != dumps(metadata["raw_document"]):
                errors.append("原文件内容与来源包快照不一致")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        errors.append(str(exc))
    return report(errors, **{name: len(bundle[name]) for name in COLLECTIONS},
                  open_issues=sum(x["status"] != "resolved" for x in bundle["issues"]),
                  open_gaps=sum(x["status"] != "resolved" for x in bundle["gaps"]))
