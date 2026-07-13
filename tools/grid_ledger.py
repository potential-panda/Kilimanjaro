"""Record a grid-scan run as a ledger event — the close-the-loop step.

Every run of tools/grid_scan.py or tools/grid_scan_horizon.py appends one
`scan_attached` event to ledger/events.jsonl, keyed to the hypothesis the
grid belongs to, with the scan folder as the artifact and the headline
numbers (windows, combos tested, rank-stable survivors) as details. This is
what lets a later session find that a parameter family was already swept —
and with what outcome — instead of rediscovering it.

Status transitions remain deliberate acts (human or scan_workflow), never a
side effect of running a grid.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ledger import append_event  # noqa: E402

_DEFAULT_LEDGER_PATH = Path(__file__).parent.parent / "ledger" / "events.jsonl"


def _headline(res: dict) -> str:
    stable = res["stable"]
    n = res["params"]["n_combos"]
    if not stable:
        return f"0/{n} combos rank-stable across train/holdout — no candidates"
    top = stable[0]
    param_keys = [
        k for k in top
        if not k.startswith(("train_", "holdout_"))
        and k not in ("stable", "n_combos_tested", "window")
    ]
    top_desc = " ".join(f"{k}={top[k]}" for k in sorted(param_keys))
    return (
        f"{len(stable)}/{n} combos rank-stable; best: {top_desc}"
        f" (train {top.get('train_avg_net', float('nan')):+.2f} bps"
        f" t={top.get('train_t_stat', float('nan')):+.2f},"
        f" holdout {top.get('holdout_avg_net', float('nan')):+.2f} bps"
        f" t={top.get('holdout_t_stat', float('nan')):+.2f})"
    )


def record_grid_scan(
    hypothesis_id: str,
    actor_id: str,
    scan_id: str,
    out_dir: Path,
    market: str,
    res: dict,
    ledger_path: Path = _DEFAULT_LEDGER_PATH,
) -> dict:
    """Append a scan_attached event for a completed run_grid_split run.

    res is run_grid_split's return value. Returns the written event.
    """
    stable = res["stable"]
    details = {
        "scanner": actor_id,
        "market": market,
        "scan_folder": str(out_dir),
        "windows": res["windows"],
        "selection": {
            "rank_metric": res["params"]["rank_metric"],
            "top_quantile": res["params"]["top_quantile"],
            "min_n": res["params"]["min_n"],
        },
        "n_combos_tested": res["params"]["n_combos"],
        "n_stable": len(stable),
        "gates": res["params"]["gates"],
        "headline": _headline(res),
    }
    return append_event(
        hypothesis_id=hypothesis_id,
        event_type="scan_attached",
        actor_type="agent",
        actor_id=actor_id,
        artifact_ids={"spec_id": hypothesis_id, "scan_report_ids": [scan_id]},
        details=details,
        ledger_path=ledger_path,
    )
