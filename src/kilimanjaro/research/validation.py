"""Data-role isolation and complete trial registration for adaptive research."""

from __future__ import annotations

import json

from .contracts import instant
from .identity import canonical, identity


def purge_intervals(label_start, label_end, evaluation_start, evaluation_end):
    """Mask labels whose information interval intersects an evaluation interval."""
    return (label_end < evaluation_start) | (label_start >= evaluation_end)


def register_development(db, run_id, spec, now):
    if spec.get("_mode") == "confirmation":
        return
    for role, window in spec["windows"].items():
        start, end = instant(window["start"]), instant(window["end"])
        if role == "train":
            start -= spec["warmup_seconds"]
        for row in db.execute(
            "SELECT spec FROM freezes WHERE state IN ('frozen','evaluating')"
        ):
            frozen = json.loads(row["spec"])
            if start < instant(frozen["end"]) and end > instant(frozen["start"]):
                raise ValueError("Development window overlaps sealed confirmation data")
        db.execute(
            "INSERT OR IGNORE INTO exposures VALUES (?,?,?,?,?)",
            (spec["program"], run_id, role, start, end),
        )
    trials = []
    for feature in spec["features"]:
        for horizon in spec["horizons"]:
            for sign in (-1, 1):
                trials.append(
                    {
                        "kind": "feature",
                        "feature": feature,
                        "horizon": horizon,
                        "sign": sign,
                    }
                )
    for horizon in spec["horizons"]:
        for method in ("single_best", "ic_weighted", "ic_decorr", "greedy"):
            trials.append(
                {
                    "kind": "blend",
                    "method": method,
                    "horizon": horizon,
                    "threshold": spec["execution"]["threshold"],
                }
            )
    for trial in trials:
        db.execute(
            "INSERT OR IGNORE INTO trials VALUES (?,?,?,?)",
            (
                identity("trial", {"run": run_id, "trial": trial}),
                run_id,
                canonical(trial),
                now,
            ),
        )
