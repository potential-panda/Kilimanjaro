#!/usr/bin/env python3
"""Print a comparison table for a set of flip-scan JSON reports.

Usage:
    python tools/scan_grid.py [FILE ...]

If no files are given, auto-discovers all {sym}-*.json files under
scan-reports/ for sym in {xrp, eth, btc}.

Columns
-------
sym      Market symbol (xrp/eth/btc)
box      box_size_rate
step     sar_step
sched    Schedule filter: nofilter / active / inactive
n_exec   Executed flips (after all gates)
avg_net  Between-flip avg net return in bps (after 1 bps cost)
dd_blk   Flips blocked by rolling_dd gate
total    n_exec × avg_net (cumulative edge proxy, in bps)

Sort order: sym → sched (nofilter/active/inactive) → box desc → step asc
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_SCAN_DIR = _ROOT / "scan-reports"
_SYMBOLS = ("xrp", "eth", "btc")

_SCHED_ORDER = {"nofilter": 0, "active": 1, "inactive": 2}


def _sched_label(rg: dict) -> str:
    has_hour = bool(rg.get("block_jst_hours"))
    has_sat  = bool(rg.get("block_sat_jst_hours"))
    if not has_hour and not has_sat:
        return "nofilter"
    # active = inactive hours blocked; inactive = active hours blocked
    # active hours are the minority window; they span roughly 8-9 h.
    # If more than 12 hours are blocked it's the inactive (majority) window.
    if len(rg["block_jst_hours"]) > 12:
        return "active"
    return "inactive"


def load_report(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text())
    except Exception:
        return None
    sym = None
    for s in _SYMBOLS:
        if s in path.stem.lower() or s in d.get("target_symbol", "").lower():
            sym = s
            break
    if sym is None:
        return None
    bf = d.get("between_flip", {})
    rg = d.get("risk_gates", {})
    avg_net = bf.get("avg_net_return_bps")
    n_exec  = d.get("n_flips")
    if avg_net is None or n_exec is None:
        return None
    return {
        "sym":     sym,
        "box":     d.get("box_size_rate", 0.0),
        "step":    d.get("sar_step", 0.0),
        "sched":   _sched_label(rg),
        "n_exec":  n_exec,
        "avg_net": avg_net,
        "dd_blk":  rg.get("n_rolling_dd_blocked", 0),
        "total":   n_exec * avg_net,
        "path":    str(path),
    }


def discover() -> list[Path]:
    seen: set[Path] = set()
    paths: list[Path] = []
    for sym in _SYMBOLS:
        for p in sorted(_SCAN_DIR.glob(f"{sym}-bsr*.json")) + sorted(_SCAN_DIR.glob(f"{sym}-*nofilter*.json")):
            if p not in seen:
                seen.add(p)
                paths.append(p)
    return paths


def print_table(rows: list[dict]) -> None:
    sym_order = {s: i for i, s in enumerate(_SYMBOLS)}
    rows.sort(key=lambda r: (
        sym_order.get(r["sym"], 99),
        _SCHED_ORDER.get(r["sched"], 99),
        -r["box"],
        r["step"],
    ))

    header = f"{'sym':<4} {'box':<6} {'step':<5} {'sched':<9} {'n_exec':>7} {'avg_net':>8} {'dd_blk':>7} {'total':>8}"
    sep    = "-" * len(header)
    print(header)
    print(sep)

    prev_sym = None
    for r in rows:
        if prev_sym and prev_sym != r["sym"]:
            print()
        print(
            f"{r['sym']:<4} {r['box']:<6} {r['step']:<5} {r['sched']:<9}"
            f" {r['n_exec']:>7} {r['avg_net']:>+8.2f} {r['dd_blk']:>7} {r['total']:>+8.0f}"
        )
        prev_sym = r["sym"]


def main(argv: list[str] | None = None) -> None:
    paths = [Path(p) for p in argv] if argv else discover()
    rows = [r for p in paths if (r := load_report(p)) is not None]
    if not rows:
        print("no scan reports found", file=sys.stderr)
        sys.exit(1)
    print_table(rows)


if __name__ == "__main__":
    main(sys.argv[1:] or None)
