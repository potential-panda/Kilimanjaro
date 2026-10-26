"""Immutable prospective decision ingestion and separately matured shadow labels.

An event-time feed must supply causal feature values. Daily parquet backfills are
historical evaluation and cannot be relabeled as prospective observations here.
"""

from __future__ import annotations

import json

from .confirmation import frozen
from .contracts import instant
from .identity import canonical, implementation


def record(store, freeze_id, *, decision, available_at, features):
    import math

    import pandas as pd

    from .stages import candidate_score

    item = frozen(store, freeze_id)
    protocol = item["spec"]
    now = store.clock()
    request = {"decision": decision, "available_at": available_at, "features": features}
    old = store.db.execute(
        "SELECT payload FROM predictions WHERE candidate_id=? AND decision=?",
        (freeze_id, decision),
    ).fetchone()
    if old:
        payload = json.loads(old[0])
        if payload["request"] != request:
            raise ValueError("Prediction cannot be rewritten")
        return payload
    if protocol["implementation"] != implementation():
        raise ValueError("Frozen feature implementation changed")
    if (
        not all(math.isfinite(v) for v in (decision, available_at, now))
        or not available_at <= decision <= now
        or now - decision > 1
    ):
        raise ValueError(
            "Prospective observations require causal features recorded within one second of the decision"
        )
    if not instant(protocol["start"]) <= decision < instant(protocol["end"]):
        raise ValueError("Decision outside the frozen observation window")
    recipe = protocol["recipe"]
    if set(features) != set(recipe["weights"]) or not all(
        math.isfinite(v) for v in features.values()
    ):
        raise ValueError("Supply exactly the frozen recipe's finite feature values")
    score = float(candidate_score(pd.DataFrame([features]), recipe)[0])
    threshold = protocol["campaign"]["execution"]["threshold"]
    payload = {
        "request": request,
        "score": score,
        "direction": (1 if score > 0 else -1) if abs(score) >= threshold else 0,
        "recorded": now,
        "maturity": now + recipe["horizon"],
        "evidence_role": "prospective_shadow",
        "endpoint": "fixed_horizon_quote_diagnostic",
        "confirmation_use": False,
    }
    with store.transaction() as db:
        db.execute(
            "INSERT INTO predictions VALUES (?,?,?,?,?,NULL)",
            (freeze_id, decision, now, payload["maturity"], canonical(payload)),
        )
        store._event(
            db,
            item["campaign_id"],
            "prediction_recorded",
            {"freeze_id": freeze_id, "decision": decision},
        )
    return payload


def mature(store, freeze_id, decision, *, quotes):
    """Join first eligible quotes only after the label's actual maturity."""
    import math

    item = frozen(store, freeze_id)
    row = store.db.execute(
        "SELECT * FROM predictions WHERE candidate_id=? AND decision=?",
        (freeze_id, decision),
    ).fetchone()
    if row is None:
        raise KeyError("Unknown prediction")
    if row["outcome"]:
        return json.loads(row["outcome"])
    now = store.clock()
    if now < row["maturity"]:
        raise ValueError("Future labels are not yet available")
    if not quotes or any(
        set(q) != {"ts", "bid", "ask"}
        or not all(math.isfinite(v) for v in q.values())
        or q["ts"] > now
        or not 0 < q["bid"] <= q["ask"]
        for q in quotes
    ):
        raise ValueError("Quotes must be observed, finite, positive and noncrossed")
    quotes = sorted(quotes, key=lambda q: q["ts"])
    entry = next((q for q in quotes if q["ts"] >= row["recorded"]), None)
    exit_ = next((q for q in quotes if q["ts"] >= row["maturity"]), None)
    policy = item["spec"]["campaign"]["execution"]
    if entry is None or exit_ is None:
        return {"state": "waiting_quotes"}
    if (
        entry["ts"] - row["recorded"] > policy["max_quote_delay_seconds"]
        or exit_["ts"] - row["maturity"] > policy["max_quote_delay_seconds"]
    ):
        result = {"state": "censored", "reason": "stale_quote"}
    else:
        direction = json.loads(row["payload"])["direction"]
        gross = (
            (exit_["bid"] / entry["ask"] - 1) * 1e4
            if direction > 0
            else (1 - exit_["ask"] / entry["bid"]) * 1e4
        )
        net = (
            gross - policy["round_trip_fee_bps"] - policy["round_trip_slippage_bps"]
            if direction
            else 0.0
        )
        result = {
            "state": "observed",
            "net_bps": net,
            "direction": direction,
            "entry": entry,
            "exit": exit_,
            "labeled_at": now,
        }
    with store.transaction() as db:
        db.execute(
            "UPDATE predictions SET outcome=? WHERE candidate_id=? AND decision=? AND outcome IS NULL",
            (canonical(result), freeze_id, decision),
        )
        store._event(
            db,
            item["campaign_id"],
            "prediction_matured",
            {"freeze_id": freeze_id, "decision": decision},
        )
    return result
