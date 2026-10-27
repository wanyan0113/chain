"""Six-step design over explicit facts and user-supplied conjunctive rules.

This is a bounded exhaustive constraint search, not a full OWL reasoner.
Unknown conditions never count as satisfied. Inputs are not mutated.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations, product
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

ROLES = ("insight", "coordination", "manifestation")


class DesignError(ValueError):
    pass


class SearchLimitError(DesignError):
    pass


def validate_document(doc):
    # jsonschema accepts Python NaN/Infinity as numbers; reject these recursively.
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise DesignError("数值必须有限")
        if isinstance(value, dict):
            for item in value.values():
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)
    finite(doc)
    schema = json.loads(Path(__file__).with_name("schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(doc), key=lambda e: str(list(e.path)))
    if errors:
        raise DesignError("; ".join(f"{list(e.path)}: {e.message}" for e in errors[:8]))
    if not math.isclose(sum(doc["weights"].values()), 1, rel_tol=0, abs_tol=1e-9):
        raise DesignError("时间、精度、成本权重和必须为 1")
    for collection in ("tasks", "artifacts", "task_rules", "artifact_rules", "resource_groups"):
        ids = [item["id"] for item in doc.get(collection, [])]
        if len(ids) != len(set(ids)):
            raise DesignError(f"{collection} 包含重复 ID")
    tasks = {item["id"] for item in doc["tasks"]}
    artifacts = {item["id"]: item for item in doc["artifacts"]}
    for collection, keys in (("links", ("from", "to")), ("contexts", ("task", "artifact"))):
        seen = set()
        for item in doc.get(collection, []):
            pair = tuple(item[key] for key in keys)
            allowed = (artifacts, artifacts) if collection == "links" else (tasks, artifacts)
            if pair in seen or any(value not in options for value, options in zip(pair, allowed)):
                raise DesignError(f"{collection} 重复或悬空引用：{pair}")
            seen.add(pair)
            if collection == "links":
                if pair[0] == pair[1]:
                    raise DesignError("协同边必须连接不同法器")
                if item["distance"]["unit"] != item["max_distance"]["unit"]:
                    raise DesignError("传讯距离与最大距离单位不一致")
                if item["distance"]["value"] < 0 or item["max_distance"]["value"] <= 0:
                    raise DesignError("传讯距离必须非负，最大距离必须大于零")
    for item in artifacts.values():
        metrics = item["metrics"]
        if metrics and (metrics["cost"]["unit"] != doc["policies"]["cost_unit"] or metrics["cost"]["value"] < 0):
            raise DesignError(f"{item['id']} 成本单位不一致或成本为负")
    for rule in doc["task_rules"]:
        if any(not c["field"].startswith("task.") or ("ref" in c and not c["ref"].startswith("task.")) for c in rule["when"]):
            raise DesignError("任务—能力规则只能引用 task 字段")
    for group in doc.get("resource_groups", []):
        if any(a not in artifacts for a in group["artifacts"]):
            raise DesignError(f"资源组 {group['id']} 含悬空法器")
    edges = set()
    for dep in doc.get("dependencies", []):
        pair = dep["before"], dep["after"]
        if any(t not in tasks for t in pair) or pair[0] == pair[1] or pair in edges:
            raise DesignError("任务依赖重复、自指或引用不存在的任务")
        edges.add(pair)
    pending = set(tasks)
    while pending:
        roots = {t for t in pending if not any(b == t and a in pending for a, b in edges)}
        if not roots:
            raise DesignError("任务依赖存在环")
        pending -= roots


def condition_result(condition, scopes):
    def lookup(path):
        scope, key = path.split(".", 1)
        return scopes.get(scope, {}).get(key)
    left = lookup(condition["field"])
    right = lookup(condition["ref"]) if "ref" in condition else condition["value"]
    if left is None or right is None:
        return "unknown", "字段缺失或值为 null"
    op = condition["op"]
    if op == "contains":
        if not isinstance(left, list) or not isinstance(right, str):
            return "unknown", "contains 要求字符串数组和字符串"
        passed = right in left
    elif op == "eq":
        if isinstance(left, dict) or isinstance(right, dict):
            if not isinstance(left, dict) or not isinstance(right, dict) or left["unit"] != right["unit"]:
                return "unknown", "数值单位不一致"
        if type(left) is not type(right):
            return "unknown", "类型不一致"
        passed = left == right
    else:
        if not isinstance(left, dict) or not isinstance(right, dict):
            return "unknown", "数值比较必须显式提供 value 和 unit"
        if left["unit"] != right["unit"]:
            return "unknown", "数值单位不一致，不自动换算"
        a, b = left["value"], right["value"]
        passed = {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}[op]
    return ("pass", "条件成立") if passed else ("fail", "条件不成立")


def evaluate(rule, scopes):
    checks = [{"condition": condition, "status": status, "reason": reason}
              for condition in rule["when"]
              for status, reason in [condition_result(condition, scopes)]]
    statuses = {check["status"] for check in checks}
    status = "fail" if "fail" in statuses else "unknown" if "unknown" in statuses or rule["blocking_issue_ids"] else "pass"
    return {"rule": rule["id"], "capability": rule["capability"], "role": rule["role"],
            "status": status, "checks": checks, "source_refs": rule["source_refs"],
            "blocking_issue_ids": rule["blocking_issue_ids"]}


def rank_chains(chains, weights, equal_score):
    """Normalize within one task's complete feasible comparison set (eq. 7–10)."""
    for key in ("time", "accuracy", "cost"):
        low = min(c["metrics"][key] for c in chains)
        high = max(c["metrics"][key] for c in chains)
        for chain in chains:
            raw = chain["metrics"][key]
            value = equal_score if high == low else (raw - low) / (high - low)
            if high != low and key != "accuracy":
                value = 1 - value
            chain.setdefault("normalized", {})[key] = value
    for chain in chains:
        chain["score"] = sum(weights[key] * value for key, value in chain["normalized"].items())
    chains.sort(key=lambda c: (-c["score"], c["id"]))


def bounded_product(groups, limit, label):
    count = math.prod(len(group) for group in groups)
    if count > limit:
        raise SearchLimitError(f"{label}组合数 {count} 超过显式上限 {limit}；未截断后宣称最优")
    return product(*groups)


def network_conflicts(chains, doc):
    artifacts = {a["id"]: a for a in doc["artifacts"]}
    selected = {c["task"]: c for c in chains}
    conflicts = []
    for dep in doc.get("dependencies", []):
        if selected[dep["after"]]["start_s"] < selected[dep["before"]]["end_s"] + dep["gap_s"]:
            conflicts.append({"code": "PRECEDENCE", **dep})
    # Conservative reservation: each artifact is occupied for its whole chain.
    # End events precede start events, so [start,end) intervals can touch.
    reservations = {a: [] for a in artifacts}
    uses = Counter(a for chain in chains for a in chain["artifacts"].values())
    for chain in chains:
        for a in chain["artifacts"].values():
            reservations[a].extend([(chain["start_s"], 1),
                                    (chain["end_s"] + artifacts[a]["cooldown_s"], -1)])
    def exceeds(events, capacity):
        occupied = 0
        for _, delta in sorted(events):
            occupied += delta
            if occupied > capacity:
                return True
        return False
    for a, item in artifacts.items():
        if exceeds(reservations[a], item["capacity"]):
            conflicts.append({"code": "CAPACITY", "resource": a})
        if item["consumable"] and uses[a] > item["inventory"]:
            conflicts.append({"code": "INVENTORY", "resource": a})
    for group in doc.get("resource_groups", []):
        events = [event for a in group["artifacts"] for event in reservations[a]]
        if exceeds(events, group["capacity"]):
            conflicts.append({"code": "GROUP_CAPACITY", "resource": group["id"]})
    return conflicts


def design(doc):
    validate_document(doc)
    artifacts = {a["id"]: a for a in doc["artifacts"]}
    links = {(e["from"], e["to"]): e for e in doc["links"]}
    contexts = {(c["task"], c["artifact"]): c for c in doc.get("contexts", [])}
    report = {"format_version": "1.0", "scope": doc["scope"], "status": "blocked",
              "policies": dict(doc["policies"]), "weights": dict(doc["weights"]),
              "tasks": [], "network": None, "network_search": {"evaluated": 0, "feasible": 0, "rejections": []}}
    comparison_groups = []
    unresolved = False
    for ti, task in enumerate(doc["tasks"], 1):
        inference = [evaluate(rule, {"task": task["attributes"]}) for rule in doc["task_rules"]]
        required = {role: sorted({r["capability"] for r in inference if r["role"] == role and r["status"] == "pass"}) for role in ROLES}
        result = {"task": task["id"], "target": task["target"], "inference": inference,
                  "required_capabilities": required, "selection": [], "chains": [], "rejected_chains": [], "blockers": []}
        report["tasks"].append(result)
        if task["blocking_issue_ids"]:
            result["blockers"].append({"code": "TASK_ISSUES", "issues": task["blocking_issue_ids"]})
        if any(r["status"] == "unknown" for r in inference):
            result["blockers"].append({"code": "UNKNOWN_TASK_RULE"})
        for role in ROLES:
            if not required[role]:
                result["blockers"].append({"code": "NO_CAPABILITY", "role": role})
        candidates = {role: [] for role in ROLES}
        for artifact in doc["artifacts"]:
            role = artifact["role"]
            context = contexts.get((task["id"], artifact["id"]), {})
            scopes = {"task": task["attributes"], "artifact": artifact["attributes"], "context": context.get("attributes", {})}
            checks = [evaluate(rule, scopes) for rule in doc["artifact_rules"]
                      if rule["role"] == role and rule["capability"] in required[role]]
            # Multiple rules for the same capability are alternatives; each rule
            # is a conjunction. All distinct required capabilities must hold.
            supported = {r["capability"] for r in checks if r["status"] == "pass"}
            missing = sorted(set(required[role]) - supported)
            target_status = "pass"
            if role != "coordination":
                target_type = task["attributes"].get("target_type")
                target_types = artifact["attributes"].get("target_types")
                target_status = ("unknown" if not isinstance(target_type, str) or not isinstance(target_types, list)
                                 else "pass" if target_type in target_types else "fail")
            issues = artifact["blocking_issue_ids"] + context.get("blocking_issue_ids", [])
            uncertain = bool(issues) or target_status == "unknown" or any(
                not any(r["capability"] == cap for r in checks)
                or any(r["capability"] == cap and r["status"] == "unknown" for r in checks)
                for cap in missing)
            if artifact["available"] and required[role] and target_status != "fail" and uncertain:
                result["blockers"].append({"code": "UNKNOWN_ARTIFACT", "artifact": artifact["id"]})
            accepted = bool(required[role]) and not missing and artifact["available"] and not issues and target_status == "pass"
            result["selection"].append({"artifact": artifact["id"], "accepted": bool(accepted),
                                        "missing_capabilities": missing, "rules": checks,
                                        "target_type_check": target_status,
                                        "available": artifact["available"],
                                        "blocking_issue_ids": artifact["blocking_issue_ids"] + context.get("blocking_issue_ids", [])})
            if accepted:
                candidates[role].append(artifact["id"])
        if result["blockers"]:
            unresolved = True
            comparison_groups.append([])
            continue
        combinations_iter = bounded_product([candidates[r] for r in ROLES], doc["policies"]["max_chain_combinations"], "法术链")
        for ci, ids in enumerate(combinations_iter, 1):
            reasons, evidence = [], []
            for index, (a, b) in enumerate(zip(ids, ids[1:])):
                edge = links.get((a, b))
                if edge is None:
                    reasons.append({"code": "MISSING_LINK", "from": a, "to": b})
                    continue
                evidence.append(edge)
                if edge["blocking_issue_ids"]:
                    reasons.append({"code": "LINK_ISSUES", "from": a, "to": b})
                if not edge["communicates"]:
                    reasons.append({"code": "NO_COMMUNICATION", "from": a, "to": b})
                if edge["distance"]["value"] >= edge["max_distance"]["value"]:
                    reasons.append({"code": "COMMUNICATION_RANGE", "from": a, "to": b})
                if index == 1 and not edge["controls"]:
                    reasons.append({"code": "NO_CONTROL", "from": a, "to": b})
            if reasons:
                result["rejected_chains"].append({"artifacts": dict(zip(ROLES, ids)), "reasons": reasons})
                if all(reason["code"] in {"MISSING_LINK", "LINK_ISSUES"} for reason in reasons):
                    result["blockers"].append({"code": "UNKNOWN_COOPERATION", "artifacts": list(ids)})
                    unresolved = True
                continue
            chain = {"id": f"chain-{ti}-{ci}", "task": task["id"], "target": task["target"],
                     "artifacts": dict(zip(ROLES, ids)), "closed_path": [task["target"], *ids, task["target"]],
                     "link_evidence": evidence, "metrics": None, "score": None, "start_s": task["start_s"]}
            metrics = [artifacts[a]["metrics"] for a in ids]
            if all(m is not None for m in metrics):
                chain["metrics"] = {"time": sum(m["duration_s"] for m in metrics),
                                    "accuracy": math.prod(m["accuracy"] for m in metrics),
                                    "cost": sum(m["cost"]["value"] for m in metrics),
                                    "cost_unit": doc["policies"]["cost_unit"]}
                chain["end_s"] = chain["start_s"] + chain["metrics"]["time"]
                if not all(math.isfinite(chain["metrics"][k]) for k in ("time", "accuracy", "cost")) or not math.isfinite(chain["end_s"]):
                    raise DesignError("指标计算溢出")
            result["chains"].append(chain)
        if any(c["metrics"] is None for c in result["chains"]):
            result["blockers"].append({"code": "MISSING_METRICS", "chains": [c["id"] for c in result["chains"] if c["metrics"] is None]})
        if result["blockers"]:
            unresolved = True
            comparison_groups.append([])
        else:
            if result["chains"]:
                rank_chains(result["chains"], doc["weights"], doc["policies"]["equal_metric_score"])
            comparison_groups.append(result["chains"])
    if not all(comparison_groups):
        report["status"] = "blocked" if unresolved else "no_feasible_design"
        return report
    best = None
    rejection_counts = Counter()
    for chosen in bounded_product(comparison_groups, doc["policies"]["max_network_combinations"], "奥术网"):
        report["network_search"]["evaluated"] += 1
        conflicts = network_conflicts(chosen, doc)
        if conflicts:
            rejection_counts.update((c["code"], c.get("resource", f"{c.get('before')}->{c.get('after')}")) for c in conflicts)
            continue
        report["network_search"]["feasible"] += 1
        score = sum(c["score"] for c in chosen) / len(chosen)
        key = (-score, tuple(c["id"] for c in chosen))
        if best is None or key < best[0]:
            best = key, chosen, score
    report["network_search"]["rejections"] = [{"code": code, "resource": resource, "count": count}
                                               for (code, resource), count in sorted(rejection_counts.items())]
    if best is None:
        report["status"] = "no_feasible_design"
        return report
    _, chosen, score = best
    selected = {c["task"]: c for c in chosen}
    deps = {(d["before"], d["after"]) for d in doc.get("dependencies", [])}
    relations = []
    for a, b in combinations(chosen, 2):
        if (a["task"], b["task"]) in deps or (b["task"], a["task"]) in deps:
            before, after = (a, b) if (a["task"], b["task"]) in deps else (b, a)
            relations.append({"kind": "serial", "from": before["id"], "to": after["id"]})
        elif max(a["start_s"], b["start_s"]) < min(a["end_s"], b["end_s"]):
            relations.append({"kind": "parallel", "chains": [a["id"], b["id"]]})
        shared = sorted(set(a["artifacts"].values()) & set(b["artifacts"].values()))
        if shared:
            relations.append({"kind": "resource_sharing", "chains": [a["id"], b["id"]], "artifacts": shared})
    for before in selected:
        successors = sorted(after for first, after in deps if first == before)
        if len(successors) > 1:
            relations.append({"kind": "branch", "from": selected[before]["id"], "to": [selected[s]["id"] for s in successors]})
    report["status"] = "complete"
    report["network"] = {"id": "web-1", "chains": [c["id"] for c in chosen], "score": score,
                         "objective": "mean_of_per_task_normalized_chain_scores",
                         "relations": relations, "makespan_s": max(c["end_s"] for c in chosen) - min(c["start_s"] for c in chosen),
                         "cost_per_execution": sum(c["metrics"]["cost"] for c in chosen),
                         "cost_unit": doc["policies"]["cost_unit"]}
    return report
