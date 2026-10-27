"""Load and validate explicit mission/situation/environment data links."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from rdflib import Namespace, RDF, URIRef
from rdflib.namespace import OWL, RDFS

from check_ontology import any_type_matches, instances_of
from environment.loader import ENV, load_environment, query_observations, timestamp
from validate_data import iri

KW = Namespace("http://example.org/arcaneweb#")
LINK = Namespace("urn:environment-integration:")
MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_ONTOLOGY = MODULE_DIR.parent / "arcane_ontology.ttl"


def load_project(project_paths, environment_paths=(), link_paths=(), *,
                 ontology_path=DEFAULT_ONTOLOGY, require_observations=False):
    """Return the merged graph and report. Inputs remain unchanged.

    Environmental validation runs after all files are loaded so that references
    can cross files. Explicit named subclasses are supported; no OWL inference.
    """
    paths = [ontology_path, MODULE_DIR / "environment_mapping.ttl",
             *project_paths, *environment_paths, *link_paths]
    graph, report = load_environment(paths, require_observations=require_observations,
                                     require_parameters=False)
    observation_gap = report.error if require_observations else report.warn
    for cls in (KW.ArcaneMission, KW.ElementalFieldSituation, ENV.Observation):
        if not any((cls, RDF.type, kind) in graph for kind in (OWL.Class, RDFS.Class)):
            report.error("LINK_SCHEMA", f"缺少连接所需类声明：{cls}")
    for prop, domain, range_ in (
        (KW.hasSituation, KW.ArcaneMission, KW.ElementalFieldSituation),
        (LINK.hasObservation, KW.ElementalFieldSituation, ENV.Observation),
    ):
        if (prop, RDF.type, OWL.ObjectProperty) not in graph:
            report.error("LINK_SCHEMA", f"缺少连接对象属性声明：{prop}")
        if (prop, RDF.type, OWL.DatatypeProperty) in graph:
            report.error("LINK_SCHEMA", f"连接属性不能同时声明为数据属性：{prop}")
        if set(graph.objects(prop, RDFS.domain)) != {domain}:
            report.error("LINK_SCHEMA", f"连接属性 {prop} 的 domain 须与约定一致：{domain}")
        if set(graph.objects(prop, RDFS.range)) != {range_}:
            report.error("LINK_SCHEMA", f"连接属性 {prop} 的 range 须与约定一致：{range_}")

    edges = list(graph.subject_objects(LINK.hasObservation))
    if not edges:
        observation_gap("NO_CONNECTION", "没有显式的态势—环境观测关联；不代表文章要求提供独立观测")
    for _, prop, _ in graph:
        if str(prop).startswith(str(LINK)) and prop != LINK.hasObservation:
            report.error("LINK_FIELD", f"未定义的连接字段：{prop}")

    def endpoint(node, expected):
        if not isinstance(node, URIRef):
            report.error("LINK_ID", f"关联端点需要稳定 IRI：{node}")
            return False
        if not any_type_matches(graph, node, expected):
            report.error("LINK_TYPE", f"关联对象 {node} 缺少显式类型或具名子类类型 {expected}")
            return False
        return True

    linked_situations = set()
    linked_observations = set()
    for situation, observation in edges:
        valid_situation = endpoint(situation, KW.ElementalFieldSituation)
        valid_observation = endpoint(observation, ENV.Observation)
        if valid_situation and valid_observation:
            linked_situations.add(situation)
            linked_observations.add(observation)

    reachable_situations = set()
    connected_missions = set()
    context_missions = set()
    context_situations = set()
    for mission, situation in graph.subject_objects(KW.hasSituation):
        valid_mission = endpoint(mission, KW.ArcaneMission)
        valid_situation = endpoint(situation, KW.ElementalFieldSituation)
        if valid_mission and valid_situation:
            context_missions.add(mission)
            context_situations.add(situation)
            if situation not in linked_situations:
                observation_gap("LINK_GAP", f"任务 {mission} 的态势 {situation} 只有场景背景，未关联环境观测")
            else:
                reachable_situations.add(situation)
                connected_missions.add(mission)
    if not context_missions:
        report.error("NO_CONTEXT", "没有有效的任务—态势场景背景关联")
    if require_observations and not connected_missions:
        report.error("NO_PATH", "没有完整的任务—态势—环境观测路径")
    for situation in sorted(linked_situations - context_situations, key=str):
        report.warn("UNUSED_SITUATION", f"态势 {situation} 已关联观测，但没有任务显式引用")
    for mission in sorted(instances_of(graph, KW.ArcaneMission), key=str):
        if not list(graph.objects(mission, KW.hasSituation)):
            report.warn("UNLINKED_MISSION", f"任务 {mission} 未关联态势，不会出现在联合查询结果中")
    for observation in sorted(instances_of(graph, ENV.Observation) - linked_observations, key=str):
        report.warn("UNLINKED_OBSERVATION", f"环境观测 {observation} 未关联态势")
    report.info("CONNECTION", f"完整路径涉及 {len(connected_missions)} 个任务、{len(reachable_situations)} 个态势；仅检查显式数据关联")
    report.info("CONTEXT", f"场景背景涉及 {len(context_missions)} 个任务、{len(context_situations)} 个态势；未检查环境作用")
    return graph, report


def query_project(graph, *, mission=None, situation=None, parameter=None, area=None,
                  start=None, end=None, require_observations=False):
    """Query a validated graph without assigning observations automatically."""
    for selected, expected in ((mission, KW.ArcaneMission), (situation, KW.ElementalFieldSituation)):
        if selected is not None:
            if not isinstance(selected, URIRef):
                raise ValueError("任务和态势过滤必须使用完整 IRI 的 URIRef")
            iri(str(selected))
            if not any_type_matches(graph, selected, expected):
                raise ValueError(f"过滤对象不存在或类型不符：{selected}")
    observations = {row["id"]: row for row in query_observations(
        graph, parameter=parameter, area=area, start=start, end=end)}
    rows = []
    for task, state in sorted(graph.subject_objects(KW.hasSituation), key=lambda edge: tuple(map(str, edge))):
        if mission is not None and task != mission:
            continue
        if situation is not None and state != situation:
            continue
        linked = sorted(graph.objects(state, LINK.hasObservation), key=str)
        if not linked and not require_observations and all(
            value is None for value in (parameter, area, start, end)
        ):
            rows.append({"mission": str(task), "situation": str(state),
                         "path_properties": [str(KW.hasSituation)],
                         "connection_status": "background_only", "observation": None})
        for observation in linked:
            record = observations.get(str(observation))
            if record is None:
                continue
            rows.append({"mission": str(task), "situation": str(state),
                         "path_properties": [str(KW.hasSituation), str(LINK.hasObservation)],
                         "connection_status": "observation_linked", "observation": record})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, nargs="+", required=True, help="任务和态势实例文件")
    parser.add_argument("--environment", type=Path, nargs="+", default=[], help="可选的区域、参数定义和观测文件")
    parser.add_argument("--links", type=Path, nargs="+", default=[], help="可选的显式观测关联文件")
    parser.add_argument("--require-observations", action="store_true", help="严格验收环境观测完整路径")
    parser.add_argument("--ontology", type=Path, default=DEFAULT_ONTOLOGY)
    parser.add_argument("--mission", type=iri)
    parser.add_argument("--situation", type=iri)
    parser.add_argument("--parameter", type=iri)
    parser.add_argument("--area", type=iri)
    parser.add_argument("--from-time", type=timestamp)
    parser.add_argument("--to-time", type=timestamp)
    args = parser.parse_args(argv)
    try:
        graph, report = load_project(args.project, args.environment, args.links,
                                     ontology_path=args.ontology, require_observations=args.require_observations)
        for level, code, message in report.items:
            print(f"[{level}] {code}: {message}", file=sys.stderr)
        if report.counts()["ERROR"]:
            return 1
        rows = query_project(graph, mission=args.mission, situation=args.situation,
                             parameter=args.parameter, area=args.area,
                             start=args.from_time, end=args.to_time,
                             require_observations=args.require_observations)
    except Exception as exc:
        print(f"读取或查询错误：{exc}", file=sys.stderr)
        return 2
    print(json.dumps({"mode": "observations_required" if args.require_observations else "scene_background",
                      "count": len(rows), "connections": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
