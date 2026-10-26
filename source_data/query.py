"""Queries return original JSON values and explicit source references."""
from .common import InputError
from .load import bundle_from_graph


def query_records(graph, *, material=None, table=None, record=None):
    bundle = bundle_from_graph(graph)
    for selected, collection in ((material, "materials"), (table, "tables"), (record, "records")):
        if selected is not None and selected not in {obj["id"] for obj in bundle[collection]}:
            raise InputError(f"不存在的来源标识：{selected}")
    tables = {obj["id"]: obj for obj in bundle["tables"]}
    materials = {obj["id"]: obj for obj in bundle["materials"]}
    fields = {obj["id"]: obj for obj in bundle["fields"]}
    rows = []
    for row in bundle["records"]:
        tab = tables[row["table_id"]]
        if ((table is not None and tab["id"] != table) or
            (material is not None and material not in tab["material_ids"]) or
            (record is not None and record != row["id"])):
            continue
        values = [{**value, "field": fields[value["field_id"]]} for value in bundle["values"] if value["record_id"] == row["id"]]
        issues = [issue for issue in bundle["issues"] if row["id"] in issue["record_ids"] or tab["id"] in issue["table_ids"]]
        rows.append({**row, "table_info": tab, "materials": [materials[mid] for mid in tab["material_ids"]],
                     "fields": values, "issues": issues})
    return rows


def query_issues(graph, *, status=None):
    if status is not None and status not in {"open", "resolved", "deferred"}:
        raise InputError("疑点状态须为 open/resolved/deferred")
    return [obj for obj in bundle_from_graph(graph)["issues"] if status is None or obj["status"] == status]


def query_unconfirmed(graph):
    bundle = bundle_from_graph(graph)
    fields = {obj["id"]: obj for obj in bundle["fields"]}
    return [{**obj, "field": fields[obj["field_id"]]} for obj in bundle["values"]
            if obj["issue_ids"] or obj["missing_status"] != "present" or
            fields[obj["field_id"]]["unit"]["status"] in {"unknown", "unconfirmed"}]


def query_materials(graph):
    bundle = bundle_from_graph(graph)
    return [{**material, "tables": [table for table in bundle["tables"] if material["id"] in table["material_ids"]]}
            for material in bundle["materials"]]
