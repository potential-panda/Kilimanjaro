"""Frozen prospective confirmation; outcomes never feed an open search cohort."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from .artifacts import read_json, verify, write_json
from .contracts import instant
from .identity import canonical, identity, implementation


def frozen(store, candidate_id):
    row = store.db.execute(
        "SELECT * FROM freezes WHERE id=?", (candidate_id,)
    ).fetchone()
    if row is None:
        raise KeyError(candidate_id)
    return {
        **dict(row),
        "spec": json.loads(row["spec"]),
        "result": json.loads(row["result"]) if row["result"] else None,
    }


def freeze(store, run_id, candidate_id, *, start, end):
    campaign = store.campaign(run_id)
    spec = campaign["spec"]
    row = store.db.execute(
        "SELECT id,artifact_dir,manifest FROM jobs WHERE campaign_id=? AND stage='returns' AND state='succeeded'",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Candidate requires completed return evaluation")
    folder = Path(row["artifact_dir"])
    verify(folder, json.loads(row["manifest"]))
    candidates = read_json(folder / "results.json")["candidates"]
    selected = next((r for r in candidates if r["id"] == candidate_id), None)
    if selected is None:
        raise ValueError("Unknown candidate in source run")
    protocol = {
        "schema_version": 1,
        "source_run": run_id,
        "candidate_id": candidate_id,
        "recipe": selected["recipe"],
        "campaign": spec,
        "implementation": campaign["provenance"],
        "start": start,
        "end": end,
        "endpoint": "mean_daily_net_bps",
        "policy": spec["trial_policy"],
    }
    freeze_id = identity("frozen", protocol)
    with store.transaction() as db:
        existing = db.execute(
            "SELECT id FROM freezes WHERE id=?", (freeze_id,)
        ).fetchone()
        if existing:
            return frozen(store, freeze_id)
        if not store.clock() <= instant(start) < instant(end):
            raise ValueError(
                "Confirmation must start at or after the freeze and have a fixed end"
            )
        duration = instant(end) - instant(start)
        if duration % 86400 or duration / 86400 < max(
            20, spec["trial_policy"]["min_days"]
        ):
            raise ValueError(
                "Confirmation requires a whole number of days, at least 20 and the policy minimum"
            )
        if db.execute(
            "SELECT 1 FROM exposures WHERE program=? AND start<? AND end>?",
            (spec["program"], instant(end), instant(start)),
        ).fetchone():
            raise ValueError("Confirmation window has already been exposed")
        for role in ("train", "validation"):
            evidence = selected[role]
            if (
                evidence["n_trades"] < spec["trial_policy"]["min_trades"]
                or (evidence["mean_net_bps"] or 0) <= 0
                or evidence.get("coverage", 0)
                < spec["trial_policy"].get("min_coverage", 0.95)
            ):
                raise ValueError(
                    "Candidate has not passed the declared development screen"
                )
        db.execute(
            "INSERT OR IGNORE INTO programs(id,alpha) VALUES (?,?)",
            (spec["program"], spec["trial_policy"]["program_alpha"]),
        )
        program = db.execute(
            "SELECT * FROM programs WHERE id=?", (spec["program"],)
        ).fetchone()
        if program["alpha"] != spec["trial_policy"]["program_alpha"]:
            raise ValueError("A new campaign cannot reset its program's error budget")
        n = program["next_test"]
        alpha = program["alpha"] / (n * (n + 1))
        db.execute(
            "UPDATE programs SET next_test=next_test+1 WHERE id=?", (spec["program"],)
        )
        db.execute(
            "INSERT INTO freezes(id,campaign_id,program,spec,alpha,created) VALUES (?,?,?,?,?,?)",
            (
                freeze_id,
                run_id,
                spec["program"],
                canonical(protocol),
                alpha,
                store.clock(),
            ),
        )
        store._event(
            db,
            run_id,
            "candidate_frozen",
            {
                "freeze_id": freeze_id,
                "alpha": alpha,
                "test_number": n,
                "start": start,
                "end": end,
            },
        )
    write_json(
        Path(spec["research_root"])
        / "04_campaigns"
        / campaign["logical_id"]
        / "freezes"
        / f"{freeze_id}.json",
        {**protocol, "alpha": alpha, "freeze_id": freeze_id},
    )
    return frozen(store, freeze_id)


def enqueue_confirmation(store, freeze_id):
    from kilimanjaro.data.planner import plan_data

    item = frozen(store, freeze_id)
    protocol = item["spec"]
    if item["result"] is not None:
        return item
    if store.clock() < instant(protocol["end"]):
        return {
            "state": "waiting_maturity",
            "freeze_id": freeze_id,
            "end": protocol["end"],
        }
    current = implementation()
    if current != protocol["implementation"]:
        raise ValueError(
            "Implementation differs from the frozen candidate; do not silently recalibrate"
        )
    spec = copy.deepcopy(protocol["campaign"])
    # Internal evaluation span: no fitting stage is scheduled, only the frozen recipe.
    spec["windows"] = {
        role: {"start": protocol["start"], "end": protocol["end"]}
        for role in ("train", "validation")
    }
    spec["_mode"] = "confirmation"
    spec["_confirmation"] = {
        "freeze_id": freeze_id,
        "protocol": protocol,
        "alpha": item["alpha"],
    }
    data = plan_data(spec)
    if data["snapshot_bytes"] > spec["budgets"]["disk_mb"] * 1024**2:
        raise ValueError("Confirmation snapshots exceed disk budget")
    if data["waiting"]:
        return {
            "state": "waiting_data",
            "freeze_id": freeze_id,
            "missing": data["waiting"],
        }
    run_id = identity(
        "run", {"confirmation": freeze_id, "data": data, "implementation": current}
    )
    ids = {
        k: identity("job", {"run": run_id, "partition": k, "node": v})
        for k, v in data["nodes"].items()
    }
    jobs = [
        {
            "id": ids[k],
            "stage": v["action"],
            "payload": v,
            "depends": [ids[p] for p in v["depends"]],
        }
        for k, v in data["nodes"].items()
    ]
    parents = list(ids.values())
    for stage in ("assemble", "features", "confirm"):
        job_id = identity("job", {"run": run_id, "stage": stage})
        jobs.append({"id": job_id, "stage": stage, "payload": {}, "depends": parents})
        parents = [job_id]
    store.enqueue(
        run_id, store.campaign(item["campaign_id"])["logical_id"], spec, current, jobs
    )
    with store.transaction() as db:
        db.execute(
            "UPDATE freezes SET state='evaluating' WHERE id=? AND result IS NULL",
            (freeze_id,),
        )
    return {"state": "enqueued", "freeze_id": freeze_id, "run_id": run_id}


def judge(trades, protocol, alpha, *, coverage=1.0):
    """One predeclared test at the end date, with zeros for non-trading days."""
    import numpy as np
    import pandas as pd

    from kilimanjaro.framework.stats import block_bootstrap_pvalue

    from .stages import clean

    policy = protocol["policy"]
    start = pd.Timestamp(protocol["start"]).tz_convert("UTC")
    end = pd.Timestamp(protocol["end"]).tz_convert("UTC")
    # Fixed 24-hour bins anchored on the protocol start; partial final bins are
    # excluded so they cannot introduce a different trading exposure.
    n_days = int((end - start).total_seconds() // 86400)
    daily = np.zeros(n_days)
    counts = np.zeros(n_days, dtype=int)
    if not trades.empty:
        indices = (
            (trades.entry_exec_ts.to_numpy() - start.timestamp()) // 86400
        ).astype(int)
        for index, net in zip(indices, trades.net_bps.to_numpy()):
            if 0 <= index < n_days and np.isfinite(net):
                daily[index] += net
                counts[index] += 1
    minimum = max(20, int(policy["min_days"]))
    result = {
        "endpoint": protocol["endpoint"],
        "alpha": alpha,
        "n_days": n_days,
        "n_trades": int(counts.sum()),
        "coverage": coverage,
        "daily_net_bps": daily.tolist(),
        "mean_daily_net_bps": float(daily.mean()) if n_days else None,
    }
    if coverage < policy.get("min_coverage", 0.95):
        return {
            **result,
            "state": "insufficient_evidence",
            "reason": "incomplete_feature_coverage",
            "p_value": None,
        }
    if n_days < minimum or counts.sum() < policy["min_trades"]:
        return {
            **result,
            "state": "insufficient_evidence",
            "reason": "too_few_days_or_trades",
            "p_value": None,
        }
    p = block_bootstrap_pvalue(
        daily,
        n_boot=int(policy["bootstrap_samples"]),
        seed=protocol["campaign"]["seed"],
    )
    rng = np.random.default_rng(protocol["campaign"]["seed"])
    block = max(1, round(n_days ** (1 / 3)))
    means = []
    for _ in range(int(policy["bootstrap_samples"])):
        starts = rng.integers(0, n_days, size=int(np.ceil(n_days / block)))
        index = ((starts[:, None] + np.arange(block)).ravel()[:n_days]) % n_days
        means.append(daily[index].mean())
    result["mean_daily_net_bps_interval"] = np.quantile(
        means, [alpha / 2, 1 - alpha / 2]
    ).tolist()
    result["interval_level"] = 1 - alpha
    equity = np.r_[0, np.cumsum(daily)]
    drawdown = float(np.max(np.maximum.accumulate(equity) - equity))
    positive = np.maximum(daily, 0)
    concentration = float(positive.max() / positive.sum()) if positive.sum() else 1.0
    checks = {
        "positive_mean": daily.mean() > 0,
        "significant": p <= alpha,
        "drawdown": drawdown <= policy["max_drawdown_bps"],
        "turnover": int(counts.max()) <= policy["max_trades_per_day"],
        "concentration": concentration <= policy["max_profit_day_share"],
    }
    if 1 / (policy["bootstrap_samples"] + 1) > alpha:
        return {
            **result,
            "state": "insufficient_evidence",
            "reason": "bootstrap_resolution_below_allocated_alpha",
            "p_value": p,
        }
    return clean(
        {
            **result,
            "state": "confirmed" if all(checks.values()) else "rejected",
            "p_value": p,
            "max_drawdown_bps": drawdown,
            "max_profit_day_share": concentration,
            "checks": checks,
            "assumptions": "circular blocks of daily net returns; stationarity and adequate dependence coverage required",
        }
    )


def complete(store, run_id, artifact_dir):
    campaign = store.campaign(run_id)
    config = campaign["spec"]["_confirmation"]
    result = read_json(Path(artifact_dir) / "confirmation.json")
    with store.transaction() as db:
        item = frozen(store, config["freeze_id"])
        if item["result"] is not None:
            if item["result"] != result:
                raise ValueError("Confirmation result is immutable")
            return
        db.execute(
            "UPDATE freezes SET state=?,result=? WHERE id=?",
            (result["state"], canonical(result), config["freeze_id"]),
        )
        protocol = item["spec"]
        db.execute(
            "INSERT OR IGNORE INTO exposures VALUES (?,?,?,?,?)",
            (
                item["program"],
                run_id,
                "confirmation",
                instant(protocol["start"]),
                instant(protocol["end"]),
            ),
        )
        store._event(
            db,
            run_id,
            "confirmation_completed",
            {"freeze_id": config["freeze_id"], "result": result},
        )
