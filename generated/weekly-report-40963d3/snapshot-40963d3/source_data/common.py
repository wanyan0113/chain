"""Local-only JSON, deterministic identifiers, reports and safe output."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote

MODULE = Path(__file__).resolve().parent
COLLECTIONS = {"materials": "Material", "tables": "Table", "fields": "Field",
               "records": "Record", "values": "FieldValue", "issues": "Issue",
               "gaps": "Gap", "statements": "SourceStatement"}


class InputError(ValueError):
    """Unreadable input or invalid mapping/command."""


class ValidationError(ValueError):
    """Structurally readable data violates its contract."""


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def read_json(path):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise InputError(f"重复 JSON 键：{key}")
            obj[key] = value
        return obj
    def invalid(value):
        raise InputError(f"非有限 JSON 数值：{value}")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                          object_pairs_hook=pairs, parse_constant=invalid)
    except (OSError, ValueError) as exc:
        raise InputError(f"{path}: {exc}") from exc


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identifier(namespace, kind, path):
    # Paths, not display names, identify records. Stable across repeated imports.
    return f"{namespace}/{kind}/{path}"


def source_iri(identifier):
    return "urn:source-data:entity:" + quote(identifier, safe="")


def json_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float) and math.isfinite(value):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise ValidationError("不是有限 JSON 值")


def resolve(data, path):
    """Restricted key[index].key paths; never evaluates expressions."""
    if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z_][\w-]*(?:\[\d+\]|\.[A-Za-z_][\w-]*)*", path):
        raise InputError(f"非法输入路径：{path}")
    current = data
    try:
        for key, index in re.findall(r"([A-Za-z_][\w-]*)|\[(\d+)\]", path):
            current = current[key] if key else current[int(index)]
        return current
    except (KeyError, IndexError, TypeError) as exc:
        raise ValidationError(f"路径不存在：{path}") from exc


def report(errors=(), **counts):
    return {"status": "invalid" if errors else "valid", "counts": {"ERROR": len(errors), **counts},
            "errors": list(errors)}


def write_outputs(directory, outputs, *, overwrite=False, protected=()):
    """Validate targets first, stage all bytes, then atomically replace each file.

    Each individual file replacement is atomic; this is not a multi-file transaction.
    Generated output names are fixed by callers and cannot contain directories.
    """
    directory = Path(directory).resolve()
    protected_paths = {Path(p).resolve() for p in protected}
    # Only new, ignored generated directories inside a code tree are output locations.
    repo = MODULE.parent
    forbidden = [repo / part for part in ("source_data", "adapters", "source_mappings",
                 "adapter_mappings", "environment", "integration", "design", "tests", "data", "docs", ".git", ".venv")]
    if any(directory == p or p in directory.parents for p in forbidden):
        raise InputError("输出目录不能位于来源、配置、模块或项目资料目录中")
    targets = []
    for name, payload in outputs.items():
        if Path(name).name != name or name in {".", ".."}:
            raise InputError("输出文件名不得包含目录")
        target = directory / name
        if target.is_symlink() or target.resolve() in protected_paths:
            raise InputError(f"输出不能覆盖输入或符号链接：{target}")
        if target.exists() and (not overwrite or not target.is_file()):
            raise InputError(f"输出已存在（如需覆盖请使用 --overwrite）：{target}")
        targets.append((target, payload))
    directory.mkdir(parents=True, exist_ok=True)
    staged = []
    try:
        for target, payload in targets:
            fd, temporary = tempfile.mkstemp(prefix=".source-", dir=directory)
            staged.append((temporary, target))
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload.encode("utf-8") if isinstance(payload, str) else payload)
                stream.flush()
                os.fsync(stream.fileno())
        for temporary, target in staged:
            if overwrite:
                os.replace(temporary, target)
            else:
                # Hard-link creation is atomic and refuses a concurrent existing target.
                os.link(temporary, target)
    finally:
        for temporary, _ in staged:
            Path(temporary).unlink(missing_ok=True)
