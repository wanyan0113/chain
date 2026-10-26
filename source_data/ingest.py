"""Read local JSON through a restricted, explicit source mapping."""
from __future__ import annotations

from copy import deepcopy
import re

from .common import (InputError, ValidationError, digest, identifier, json_type,
                     read_json, resolve)


def check_mapping(mapping):
    keys = {"format_version", "namespace", "materials_path", "checked_on_path", "units_path",
            "tables", "statements", "issues_path", "gaps_path"}
    if not isinstance(mapping, dict) or set(mapping) != keys or mapping["format_version"] != "1.0":
        raise InputError("来源映射版本或字段错误")
    if not isinstance(mapping["namespace"], str) or not re.fullmatch(r"[A-Za-z0-9_-]+", mapping["namespace"]):
        raise InputError("映射 namespace 必须为稳定 ASCII 标识")
    paths = []
    for name in ("tables", "statements"):
        if not isinstance(mapping[name], list):
            raise InputError(f"{name} 必须为数组")
        for entry in mapping[name]:
            expected = {"path", "mode", "source_keys"}
            if name == "tables":
                expected |= {"name", "identity_key", "fields"}
            if not isinstance(entry, dict) or set(entry) != expected or entry["mode"] not in {"object", "array"}:
                raise InputError(f"{name} 映射字段或读取模式错误")
            if not isinstance(entry["source_keys"], list) or not all(isinstance(x, str) for x in entry["source_keys"]):
                raise InputError("source_keys 必须是来源键数组")
            paths.append(entry["path"])
            if name == "tables":
                if not isinstance(entry["fields"], dict) or not entry["fields"]:
                    raise InputError("每张表必须显式声明字段")
                for spec in entry["fields"].values():
                    if not isinstance(spec, dict) or set(spec) != {"header", "unit_key", "unit_status"}:
                        raise InputError("字段映射错误")
                    if spec["unit_status"] not in {"confirmed", "unconfirmed", "unknown", "not_applicable", "dimensionless"}:
                        raise InputError("单位确认状态错误")
    if len(paths) != len(set(paths)):
        raise InputError("重复映射路径")


def ingest(input_path, mapping_path):
    """Return a bundle without changing either input file."""
    from pathlib import Path
    mapping = read_json(mapping_path)
    document = read_json(input_path)
    return build_bundle(document, mapping, input_filename=Path(input_path).name,
                        input_sha256=digest(input_path))


def build_bundle(document, mapping, *, input_filename, input_sha256):
    check_mapping(mapping)
    if not isinstance(document, dict):
        raise ValidationError("来源文档必须是 JSON 对象")
    bundle = {"format_version": "1.0", "metadata": {
        "input_filename": input_filename, "input_sha256": input_sha256,
        "mapping": deepcopy(mapping), "raw_document": deepcopy(document)}}
    for name in ("materials", "tables", "fields", "records", "values", "issues", "gaps", "statements"):
        bundle[name] = []
    def ident(kind, path):
        return identifier(mapping["namespace"], kind, path)
    source_files = resolve(document, mapping["materials_path"])
    units = resolve(document, mapping["units_path"])
    if not isinstance(source_files, dict) or not isinstance(units, dict):
        raise ValidationError("材料和单位目录必须是对象")
    material_by_key = {}
    for key, filename in source_files.items():
        if not isinstance(filename, str) or not filename.strip():
            raise ValidationError("材料文件名必须为非空字符串")
        material_id = ident("material", f"{mapping['materials_path']}.{key}")
        material_by_key[key] = material_id
        bundle["materials"].append({"id": material_id, "filename": filename,
            "locator": key, "transcription_sha256": input_sha256,
            "checked_on": resolve(document, mapping["checked_on_path"])})
    def materials(keys):
        try:
            return list(dict.fromkeys(material_by_key[key] for key in keys))
        except KeyError as exc:
            raise ValidationError(f"悬空材料键：{exc}") from exc
    path_targets = {}
    for spec in mapping["tables"]:
        path = spec["path"]
        raw = resolve(document, path)
        if spec["mode"] == "array":
            if not isinstance(raw, list):
                raise ValidationError(f"{path} 必须是数组表")
            rows = [(f"{path}[{i}]", row) for i, row in enumerate(raw)]
        else:
            if not isinstance(raw, dict):
                raise ValidationError(f"{path} 必须是单对象表")
            rows = [(path, raw)]
        table_id = ident("table", path)
        bundle["tables"].append({"id": table_id, "material_ids": materials(spec["source_keys"]),
                                 "name": spec["name"], "input_path": path})
        path_targets[path] = [("tables", table_id)]
        field_map = {}
        for key, field_spec in spec["fields"].items():
            field_id = ident("field", f"{path}.{key}")
            field_map[key] = field_id
            unit_key = field_spec["unit_key"]
            if unit_key is not None and unit_key not in units:
                raise ValidationError(f"单位引用不存在：{unit_key}")
            present = [row[key] for _, row in rows if isinstance(row, dict) and key in row]
            types = sorted({json_type(value) for value in present})
            cardinality = "mixed" if "array" in types and len(types) > 1 else "multiple" if types == ["array"] else "single"
            bundle["fields"].append({"id": field_id, "table_id": table_id, "key": key,
                "header": field_spec["header"], "types": types, "cardinality": cardinality,
                "unit": {"description": units.get(unit_key), "status": field_spec["unit_status"]}})
        original_codes = set()
        for index, (row_path, row) in enumerate(rows):
            if not isinstance(row, dict) or set(row) - set(field_map):
                raise ValidationError(f"{row_path} 不是对象或存在未映射字段")
            if spec["identity_key"] not in row:
                raise ValidationError(f"{row_path} 缺少原行标识字段")
            from .common import dumps
            code = dumps(row[spec["identity_key"]])
            if code in original_codes:
                raise ValidationError(f"{row_path} 原行标识重复")
            original_codes.add(code)
            record_id = ident("record", row_path)
            bundle["records"].append({"id": record_id, "table_id": table_id, "row_number": index + 1,
                "input_path": row_path, "original_code": deepcopy(row[spec["identity_key"]]), "raw_object": deepcopy(row)})
            path_targets.setdefault(row_path, []).append(("records", record_id))
            for key in field_map:
                value = row.get(key)
                value_path = f"{row_path}.{key}"
                value_id = ident("value", value_path)
                bundle["values"].append({"id": value_id, "record_id": record_id, "field_id": field_map[key],
                    "input_path": value_path, "raw_value": deepcopy(value), "raw_type": json_type(value),
                    "missing_status": "missing" if key not in row else "null" if value is None else "present", "normalized_value": None,
                    "normalization_basis": [], "issue_ids": []})
                path_targets[value_path] = [("values", value_id), ("records", record_id), ("fields", field_map[key])]
    for spec in mapping["statements"]:
        raw = resolve(document, spec["path"])
        if spec["mode"] == "array":
            if not isinstance(raw, list):
                raise ValidationError("归档陈述数组类型错误")
            entries = [(f"{spec['path']}[{i}]", value) for i, value in enumerate(raw)]
        else:
            if not isinstance(raw, dict):
                raise ValidationError("归档陈述对象类型错误")
            entries = [(spec["path"], raw)]
        path_targets[spec["path"]] = []
        for path, value in entries:
            sid = ident("statement", path)
            bundle["statements"].append({"id": sid, "kind": spec["path"], "input_path": path,
                                         "material_ids": materials(spec["source_keys"]), "raw": deepcopy(value)})
            path_targets[spec["path"]].append(("statements", sid))
            if path != spec["path"]:
                path_targets[path] = [("statements", sid)]
    gaps_path = mapping["gaps_path"]
    gaps = resolve(document, gaps_path)
    if not isinstance(gaps, list):
        raise ValidationError("资料缺口必须为数组")
    path_targets[gaps_path] = []
    for i, gap in enumerate(gaps):
        if not isinstance(gap, str) or not gap.strip():
            raise ValidationError("缺口说明必须是非空字符串")
        path = f"{gaps_path}[{i}]"
        gid = ident("gap", path)
        bundle["gaps"].append({"id": gid, "description": gap, "status": "open", "input_path": path, "raw": gap})
        path_targets[gaps_path].append(("gaps", gid))
        path_targets[path] = [("gaps", gid)]
    issues = resolve(document, mapping["issues_path"])
    if not isinstance(issues, list):
        raise ValidationError("疑点必须为数组")
    values_by_id = {value["id"]: value for value in bundle["values"]}
    ref_names = {"records": "record_ids", "fields": "field_ids", "values": "value_ids",
                 "tables": "table_ids", "statements": "statement_ids", "gaps": "gap_ids"}
    for index, raw in enumerate(issues):
        if not isinstance(raw, dict) or not all(key in raw for key in ("code", "detail", "field_paths")):
            raise ValidationError("疑点结构不完整")
        path = f"{mapping['issues_path']}[{index}]"
        issue = {"id": ident("issue", path), "code": raw["code"], "category": "source_issue",
                 "description": raw["detail"], "status": "open", "input_path": path,
                 "raw": deepcopy(raw), "basis": [], "material_ids": []}
        issue.update({name: [] for name in ref_names.values()})
        if not isinstance(raw["field_paths"], list) or not raw["field_paths"]:
            raise ValidationError("疑点必须有明确字段或集合路径")
        for referenced_path in raw["field_paths"]:
            resolve(document, referenced_path)
            if referenced_path not in path_targets:
                raise ValidationError(f"疑点路径未映射：{referenced_path}")
            for kind, target in path_targets[referenced_path]:
                if target not in issue[ref_names[kind]]:
                    issue[ref_names[kind]].append(target)
                if kind == "values":
                    values_by_id[target]["issue_ids"].append(issue["id"])
        for filename in raw.get("source_files", []):
            matches = [mat["id"] for mat in bundle["materials"] if filename in mat["filename"].split("、")]
            if not matches:
                raise ValidationError(f"疑点来源不在材料目录中：{filename}")
            issue["material_ids"].extend(x for x in matches if x not in issue["material_ids"])
        bundle["issues"].append(issue)
    # JSON object member order is not semantic and can change during RDF encoding.
    for collection in ("materials", "tables", "fields", "records", "values", "issues", "gaps", "statements"):
        for obj in bundle[collection]:
            for key, value in obj.items():
                if key.endswith("_ids") or key in {"basis", "normalization_basis"}:
                    value.sort()
        bundle[collection].sort(key=lambda obj: obj["id"])
    return bundle
