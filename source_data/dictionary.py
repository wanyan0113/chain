"""Build a source-only field catalogue for archival review."""
from __future__ import annotations

from .common import ValidationError
from .validate import validate_bundle


def field_dictionary(bundle):
    """Summarize declared fields without inferring meanings or domain relations."""
    validation = validate_bundle(bundle)
    if validation["counts"]["ERROR"]:
        raise ValidationError("; ".join(validation["errors"]))
    tables = {obj["id"]: obj for obj in bundle["tables"]}
    materials = {obj["id"]: obj for obj in bundle["materials"]}
    issues = bundle["issues"]
    rows = []
    for field in bundle["fields"]:
        table = tables[field["table_id"]]
        values = [obj for obj in bundle["values"] if obj["field_id"] == field["id"]]
        direct = [obj for obj in issues if field["id"] in obj["field_ids"]]
        table_issues = [obj for obj in issues if table["id"] in obj["table_ids"]]
        rows.append({
            "field_id": field["id"], "table_path": table["input_path"],
            "original_key": field["key"], "original_header": field["header"],
            "header_status": "provided" if field["header"] is not None else "not_provided",
            "description_status": "not_independently_verified",
            "description_note": "机器键来自既有转录；本目录未重新核对截图表头或推定业务含义。",
            "types": field["types"], "cardinality": field["cardinality"],
            "unit": field["unit"], "value_count": len(values),
            "missing_status_counts": {status: sum(value["missing_status"] == status for value in values)
                                      for status in ("present", "null", "missing", "unknown", "not_applicable")},
            "input_paths": [value["input_path"] for value in values],
            "sources": [materials[mid] for mid in table["material_ids"]],
            "field_issues": [{"id": issue["id"], "code": issue["code"],
                              "status": issue["status"], "description": issue["description"]} for issue in direct],
            "table_issues": [{"id": issue["id"], "code": issue["code"],
                              "status": issue["status"], "description": issue["description"]} for issue in table_issues],
            "normalization": "none; original values retained",
        })
    return {"format_version": "1.0", "status": "valid", "counts": {
        "ERROR": 0, "tables": len(tables), "fields": len(rows),
        "headers_not_provided": sum(row["original_header"] is None for row in rows),
        "fields_with_direct_issues": sum(bool(row["field_issues"]) for row in rows),
        "fields_under_table_issues": sum(bool(row["table_issues"]) for row in rows)},
        "input_sha256": bundle["metadata"]["input_sha256"],
        "scope": "来源字段目录；不含业务本体对应、实例生成、规则或计算可用性判定。",
        "notes": ["表级疑点与字段级疑点分开显示，不将表级缺口伪装为逐字段结论。",
                  "单位 confirmed 仅为已有来源映射中的标记，不代表该字段的身份、数值或业务含义均已确认。",
                  "未声明单位不等于无量纲；机器键不等于原表头。"],
        "fields": rows,
        "gaps": bundle["gaps"], "issues": bundle["issues"]}


def dictionary_markdown(catalogue):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    lines = ["# 来源资料字段目录", "", catalogue["scope"], "",
             f"来源 SHA256：`{catalogue['input_sha256']}`", "",
             "原表头未提供时保持未知。字段名称沿用转录机器键，不新增业务含义。", "",
             "| 表路径 | 原键 | 类型 | 原单位说明 | 单位状态 | 值数 | 字段疑点数 | 表级疑点数 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in catalogue["fields"]:
        values = [row["table_path"], row["original_key"], ", ".join(row["types"]),
                  row["unit"]["description"] or "未提供", row["unit"]["status"], row["value_count"],
                  len(row["field_issues"]), len(row["table_issues"])]
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    lines.extend(["", "完整输入路径、出处、缺失统计及疑点明细见同目录 field_dictionary.json。", ""])
    lines.extend(catalogue["notes"])
    return "\n".join(lines) + "\n"
