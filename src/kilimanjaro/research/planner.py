"""Resolve an immutable run and connect data prerequisites to research stages."""

from __future__ import annotations

from .artifacts import write_json
from .contracts import Campaign
from .identity import identity, implementation
from .store import Store


def plan(campaign: Campaign) -> dict:
    from kilimanjaro.data.planner import plan_data

    data = plan_data(campaign.spec)
    provenance = implementation()
    run_id = identity(
        "run",
        {"campaign": campaign.logical_id, "data": data, "implementation": provenance},
    )
    ids = {
        key: identity("job", {"run": run_id, "partition": key, "node": node})
        for key, node in data["nodes"].items()
    }
    jobs = []
    for key, node in data["nodes"].items():
        jobs.append(
            {
                "id": ids[key],
                "stage": node["action"],
                "state": "waiting_data"
                if node["action"] == "waiting_data"
                else "pending",
                "payload": node,
                "depends": [ids[k] for k in node["depends"]],
            }
        )
    parent = sorted(ids.values())
    for stage in ("assemble", "features", "correlation", "blends", "returns", "report"):
        job_id = identity("job", {"run": run_id, "stage": stage})
        jobs.append({"id": job_id, "stage": stage, "payload": {}, "depends": parent})
        parent = [job_id]
    if data["snapshot_bytes"] > campaign.spec["budgets"]["disk_mb"] * 1024**2:
        raise ValueError("Required input snapshots exceed the campaign disk budget")
    return {
        "schema_version": 1,
        "run_id": run_id,
        "logical_id": campaign.logical_id,
        "campaign": campaign.spec,
        "provenance": provenance,
        "data": data,
        "jobs": jobs,
    }


def enqueue(campaign: Campaign, resolved: dict | None = None) -> dict:
    resolved = resolved or plan(campaign)
    folder = campaign.root / "05_experiments" / resolved["run_id"]
    write_json(folder / "plan.json", resolved)
    write_json(
        campaign.root / "04_campaigns" / campaign.logical_id / "campaign.json",
        campaign.spec,
    )
    with Store(campaign.database) as store:
        store.enqueue(
            resolved["run_id"],
            campaign.logical_id,
            campaign.spec,
            resolved["provenance"],
            resolved["jobs"],
        )
        store.publish_events(campaign.root / "17_ledger" / "events.jsonl")
    return resolved
