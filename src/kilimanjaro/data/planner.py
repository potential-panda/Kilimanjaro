"""Build a deterministic, coverage-aware dependency graph without writing data."""

from __future__ import annotations

from pathlib import Path

from kilimanjaro.data.catalog import feature_requests, inspect_partition, relative_path
from kilimanjaro.data.providers import dependencies


def plan_data(spec: dict) -> dict:
    nodes = {}

    def visit(request):
        key = str(relative_path(request))
        if key in nodes:
            # Same partition may be referenced by several aliases/expressions.
            merged = sorted(
                set(nodes[key]["request"]["columns"]) | set(request["columns"])
            )
            if merged != nodes[key]["request"]["columns"]:
                request = {**request, "columns": merged}
            else:
                return key
        inspection = inspect_partition(Path(spec["data_root"]), request)
        node = {"request": request, "inspection": inspection, "depends": []}
        nodes[key] = node
        if inspection["status"] == "ready":
            node["action"] = "snapshot"
        elif (
            request["dataset"] in {"Trade", "OrderBook"}
            or inspection["status"] == "incomplete_day"
        ):
            node["action"] = "waiting_data"
        else:
            node["action"] = "derive"
            node["depends"] = [visit(dep) for dep in dependencies(request)]
        return key

    required = [visit(request) for request in feature_requests(spec)]
    return {
        "nodes": nodes,
        "required": sorted(set(required)),
        "snapshot_bytes": sum(n["inspection"].get("bytes", 0) for n in nodes.values()),
        "waiting": [k for k, n in nodes.items() if n["action"] == "waiting_data"],
    }
