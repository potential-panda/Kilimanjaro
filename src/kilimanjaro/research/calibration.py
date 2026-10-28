"""Scoped mechanism certificates, independent from statistical confirmation."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from .artifacts import write_json
from .identity import canonical, checksum, identity, implementation
from .stages import clean


def scope_for(store, run_id, recipe, online_build):
    campaign = store.campaign(run_id)
    spec = campaign["spec"]
    return {
        "schema_version": 1,
        "implementation": campaign["provenance"],
        "recipe": recipe,
        "features": spec["features"],
        "markets": spec["markets"],
        "target": spec["target"],
        "execution": spec["execution"],
        "availability": "bucket_end",
        "warmup_seconds": spec["warmup_seconds"],
        "max_staleness_seconds": spec["max_staleness_seconds"],
        "return_model": "EpisodeReturn",
        "online_build": online_build,
    }


def compare_artifacts(store, run_id, scope, offline, online, *, kind, tolerance=1e-4):
    """Compare supplied replay exports; never launch an external paid replay."""
    import numpy as np
    import pandas as pd

    from kilimanjaro.calibration.compare import compare
    from kilimanjaro.calibration.trades import compare_trade_frames

    if not np.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Tolerance must be finite and nonnegative")
    if not scope.get("online_build") or scope != scope_for(
        store, run_id, scope["recipe"], scope["online_build"]
    ):
        raise ValueError("Calibration scope does not match this run")
    before = {"offline": checksum(offline), "online": checksum(online)}
    off, on = pd.read_parquet(offline), pd.read_parquet(online)
    if kind == "factor":
        if off.ts.duplicated().any() or on.ts.duplicated().any():
            raise ValueError("Duplicate factor observation times")
        result = compare(
            "frozen_signal",
            off.set_index("ts").value,
            on.set_index("ts").value,
            tol=tolerance,
        )
        passed = (
            result.n_points > 0
            and result.n_match == result.n_points
            and result.n_offline_only == result.n_online_only == 0
            and result.n_points == len(off) == len(on)
        )
    elif kind == "trades":
        off = off.sort_values("entry_exec_ts").reset_index(drop=True)
        on = on.sort_values("e_ts").reset_index(drop=True)
        if off.entry_exec_ts.duplicated().any() or on.e_ts.duplicated().any():
            raise ValueError("Duplicate trade entry times")
        result = compare_trade_frames(
            off, on, px_tol=tolerance, ts_tol=0.001, ret_tol=tolerance
        )
        passed = (
            len(off) > 0
            and len(off) == len(on) == result.n_matched
            and all(
                getattr(result, k) == len(off)
                for k in (
                    "side_match",
                    "entry_ts_exact",
                    "exit_ts_exact",
                    "entry_px_match",
                    "exit_px_match",
                    "ret_match",
                )
            )
        )
    else:
        raise ValueError("Certificate kind must be factor or trades")
    if before != {"offline": checksum(offline), "online": checksum(online)}:
        raise ValueError("Calibration inputs changed during comparison")
    evidence = clean(
        {
            "kind": kind,
            "state": "matched" if passed else "mismatch",
            "result": dataclasses.asdict(result),
            "inputs": before,
            "tolerance": tolerance,
            "scope": scope,
            "statistical_evidence": "none",
        }
    )
    certificate_id = identity("certificate", evidence)
    root = Path(store.campaign(run_id)["spec"]["research_root"])
    write_json(
        root / "05_experiments" / run_id / "certificates" / f"{certificate_id}.json",
        evidence,
    )
    with store.transaction() as db:
        db.execute(
            "INSERT OR IGNORE INTO certificates VALUES (?,?,?,?)",
            (certificate_id, canonical(scope), canonical(evidence), store.clock()),
        )
        store._event(
            db,
            run_id,
            "calibration_compared",
            {
                "certificate_id": certificate_id,
                "kind": kind,
                "state": evidence["state"],
            },
        )
    return {"id": certificate_id, **evidence}


def compatible_certificates(store, scope):
    if scope["implementation"] != implementation():
        return []
    return [
        json.loads(r[0])
        for r in store.db.execute(
            "SELECT evidence FROM certificates WHERE scope=?", (canonical(scope),)
        )
    ]


def replay_readiness(store, run_id, scope, ledger_path, hypothesis_id):
    from kilimanjaro.ledger.core import load_events

    scope_id = identity("calibration", scope)
    approvals = [
        event
        for event in load_events(ledger_path)
        if event["hypothesis_id"] == hypothesis_id
        and event["event_type"] == "human_approval"
        and event["actor_type"] == "human"
        and event.get("to_status") == "backtest-approved"
        and event.get("details", {}).get("calibration_scope") == scope_id
    ]
    state = "needs_tooling" if approvals else "waiting_approval"
    task = {
        "state": state,
        "scope_id": scope_id,
        "scope": scope,
        "approval_event": approvals[-1]["event_id"] if approvals else None,
        "implementation_task": "Register an Enoshima adapter for the frozen EpisodeReturn recipe, bucket-end availability and exact venue/size/cost scope; export factors and trade fills for comparison.",
    }
    with store.transaction() as db:
        store._event(db, run_id, "replay_readiness", task)
    return task
