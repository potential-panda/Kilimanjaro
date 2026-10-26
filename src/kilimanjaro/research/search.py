"""Deterministic, bounded exploration/refinement/replication proposals."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

from .artifacts import read_json, verify
from .contracts import Campaign
from .identity import canonical, identity

LANES = ("exploration", "refinement", "exploration", "refinement", "replication")


def expression_key(expression):
    return ast.dump(ast.parse(expression, mode="eval"), include_attributes=False)


def propose(store, run_id, *, limit=5):
    if not 1 <= limit <= 20:
        raise ValueError("Proposal batch must contain 1..20 proposals")
    campaign = store.campaign(run_id)
    if campaign["spec"].get("_mode") == "confirmation":
        raise ValueError("Search cannot consume a confirmation run")
    report = store.db.execute(
        "SELECT artifact_dir,manifest FROM jobs WHERE campaign_id=? AND stage='report' AND state='succeeded'",
        (run_id,),
    ).fetchone()
    if report is None:
        return {"state": "waiting_development", "proposals": []}
    folder = Path(report["artifact_dir"])
    verify(folder, json.loads(report["manifest"]))
    evidence = read_json(folder / "report.json")["results"]["candidates"]
    spec = campaign["spec"]
    # Baseline selection is development-only. No query touches open confirmation.
    best = evidence[0] if evidence else None
    used = {expression_key(f["expr"]) for f in spec["features"]}
    base = spec["features"][0]
    if best and best["recipe"]["weights"]:
        strongest = max(
            best["recipe"]["weights"], key=lambda n: abs(best["recipe"]["weights"][n])
        )
        base = spec["features"][int(strongest[1:])]
    candidates = {"exploration": [], "refinement": [], "replication": []}
    for alias in sorted(spec["markets"]):
        for window in (5, 30, 120):
            candidates["exploration"].append(
                (
                    f"ret({alias}_mid, {window})",
                    f"Explore {alias} momentum over {window}s",
                )
            )
    for window in (5, 30, 120):
        if window <= spec["warmup_seconds"]:
            candidates["refinement"].append(
                (
                    f"ema(({base['expr']}), {window})",
                    f"Test smoothing of {base['name']} after development ranking",
                )
            )
    # Replication means a distinct, predeclared adjacent development window;
    # it does not rescan the same data with a new random seed.
    candidates["replication"].append(
        (None, "Replicate the parent recipe on the next development interval")
    )
    prior = store.db.execute(
        "SELECT count(*) FROM proposals WHERE parent_run=?", (run_id,)
    ).fetchone()[0]
    proposed = []
    for offset in range(limit):
        lane = LANES[(prior + offset) % len(LANES)]
        for expression, reason in candidates[lane]:
            child = copy.deepcopy(spec)
            if expression is not None:
                if expression_key(expression) in used or len(child["features"]) >= 32:
                    continue
                child["features"].append(
                    {"name": identity("f", expression)[0:26], "expr": expression}
                )
            else:
                from datetime import UTC, datetime

                from .contracts import instant

                train = child["windows"]["train"]
                val = child["windows"]["validation"]
                shift = instant(val["end"]) - instant(train["start"])
                for window in child["windows"].values():
                    for key in ("start", "end"):
                        window[key] = datetime.fromtimestamp(
                            instant(window[key]) + shift, UTC
                        ).isoformat()
            proposal_id = identity(
                "proposal",
                {
                    "implementation": campaign["provenance"],
                    "spec": {
                        k: v
                        for k, v in child.items()
                        if k
                        not in {
                            "name",
                            "description",
                            "hypothesis",
                            "falsification",
                            "data_root",
                            "research_root",
                        }
                    },
                },
            )
            if store.db.execute(
                "SELECT 1 FROM proposals WHERE id=?", (proposal_id,)
            ).fetchone():
                continue
            child["name"] = proposal_id
            try:
                resolved = Campaign.from_dict(child)
                from .validation import register_development

                # Validate sealed-window isolation without recording exposure.
                store.db.execute("SAVEPOINT proposal_check")
                try:
                    register_development(
                        store.db, proposal_id, resolved.spec, store.clock()
                    )
                finally:
                    store.db.execute("ROLLBACK TO proposal_check")
                    store.db.execute("RELEASE proposal_check")
            except ValueError:
                continue
            with store.transaction() as db:
                db.execute(
                    "INSERT INTO proposals VALUES (?,?,?,?,?,?,NULL)",
                    (
                        proposal_id,
                        run_id,
                        lane,
                        canonical(resolved.spec),
                        reason,
                        store.clock(),
                    ),
                )
                store._event(
                    db,
                    run_id,
                    "proposed",
                    {"proposal_id": proposal_id, "lane": lane, "reason": reason},
                )
            proposed.append(
                {
                    "id": proposal_id,
                    "lane": lane,
                    "reason": reason,
                    "campaign": resolved.spec,
                }
            )
            break
    return {
        "state": "proposed" if proposed else "search_exhausted",
        "proposals": proposed,
    }


def enqueue_proposal(store, proposal_id):
    from .planner import enqueue

    row = store.db.execute(
        "SELECT * FROM proposals WHERE id=?", (proposal_id,)
    ).fetchone()
    if row is None:
        raise KeyError(proposal_id)
    if row["run_id"]:
        return {"run_id": row["run_id"], "reused": True}
    result = enqueue(Campaign.from_dict(json.loads(row["spec"])))
    with store.transaction() as db:
        db.execute(
            "UPDATE proposals SET run_id=? WHERE id=?", (result["run_id"], proposal_id)
        )
    return {"run_id": result["run_id"], "reused": False}
