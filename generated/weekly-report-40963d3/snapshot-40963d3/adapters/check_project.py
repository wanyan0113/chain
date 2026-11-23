"""Classified verification using the existing background and strict interfaces."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from rdflib import RDF, URIRef

from environment.loader import ENV, load_environment, query_observations, query_parameters
from integration.load_project import load_project, query_project
from source_data.cli import JsonParser, require_valid
from source_data.common import ValidationError, dumps, report, write_outputs
from source_data.load import load_source


def check_project(source_paths, project_paths, environment_paths, link_paths=()):
    _, source_report = load_source(source_paths)
    require_valid(source_report)
    environment, environment_report = load_environment(environment_paths,
        require_observations=False, require_parameters=False)
    if environment_report.counts()["ERROR"]:
        raise ValidationError(dumps(environment_report.items))
    observations = query_observations(environment)
    parameters = query_parameters(environment)
    modes = {}
    ontology = Path(__file__).resolve().parent.parent / "source_data/ontology.ttl"
    for strict in (False, True):
        graph, result = load_project([*project_paths, ontology, *source_paths], environment_paths, link_paths,
                                      require_observations=strict)
        valid = result.counts()["ERROR"] == 0
        rows = query_project(graph, require_observations=strict) if valid else []
        modes["observations_required" if strict else "scene_background"] = {
            "status": "valid" if valid else "invalid", "counts": result.counts(),
            "exit_code": 0 if valid else 1, "connections": rows,
            "distinct_linked_observations": len({row["observation"]["id"] for row in rows if row["observation"] is not None}),
            "diagnostics": [{"level": level, "code": code, "message": message} for level, code, message in result.items]}
        if not strict:
            # Supplying area alone must not fabricate an association to a mission.
            areas = sorted(set(environment.subjects(RDF.type, ENV.Area)), key=str)
            modes["scene_background"]["area_filter_results"] = {
                str(area): query_project(graph, area=URIRef(area)) for area in areas} if valid else {}
    background_ok = modes["scene_background"]["status"] == "valid"
    return {"status": "valid" if background_ok else "invalid", "counts": {
                "ERROR": 0 if background_ok else modes["scene_background"]["counts"]["ERROR"],
                "source_records": source_report["counts"]["records"],
                "source_issues": source_report["counts"]["issues"], "source_gaps": source_report["counts"]["gaps"],
                "areas": len(set(environment.subjects(RDF.type, ENV.Area))),
                "parameters": len(parameters), "observations": len(observations)},
            "scope": "来源管理及区域/环境数据接入；不包含完整业务案例、链生成或评分",
            "independent_observations": "provided" if observations else "not_provided",
            "source": source_report, "modes": modes,
            "provenance_policy": "来源入口负责追溯；区域的既有外部 seeAlso 原样保留，不推定为来源包中的材料或任务区域关系"}


def main(argv=None):
    parser = JsonParser(description=__doc__)
    for key in ("source", "project", "environment"):
        parser.add_argument("--" + key, nargs="+", type=Path, required=True)
    parser.add_argument("--links", nargs="*", type=Path, default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    try:
        args = parser.parse_args(argv)
        result = check_project(args.source, args.project, args.environment, args.links)
        write_outputs(args.output_dir, {"acceptance_report.json": json.dumps(result, ensure_ascii=False, indent=2) + "\n"},
            overwrite=args.overwrite, protected=[*args.source, *args.project, *args.environment, *args.links])
        print(dumps(result))
        return int(bool(result["counts"]["ERROR"]))
    except Exception as exc:
        print(dumps(report([str(exc)])))
        print(str(exc), file=sys.stderr)
        return 1 if isinstance(exc, ValidationError) else 2


if __name__ == "__main__":
    raise SystemExit(main())
