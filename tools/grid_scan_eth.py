#!/usr/bin/env python3
"""Run a parameter grid scan for ETH (BINANCE/ETH_USDT → GMO/ETH_JPY).

Iterates all (box, step, schedule) combinations, collects per-trade statistics,
writes results to CSV, and prints a grouped summary sorted by total_net_bps.

Usage
-----
    python tools/grid_scan_eth.py --start 2026-01-01 --end 2026-06-30
    python tools/grid_scan_eth.py --start 2026-01-01 --end 2026-06-30 --out results/my_grid.csv
    python tools/grid_scan_eth.py --start 2026-01-01 --end 2026-06-30 --top 5 --p-threshold 0.05

Columns in CSV and table
------------------------
box           box_size_rate
step          sar_step
schedule      nofilter / active / inactive
n_flips       executed flips (after all gates)
n_dd_blocked  flips blocked by rolling-DD gate
n_dd_triggers number of DD gate trigger events
avg_gross     avg signed return before costs (bps)
avg_net       avg net return after 1 bps cost (bps)
total_net_bps n_flips × avg_net  — cumulative edge proxy
hit_rate      fraction of flips with gross return > 0
std_bps       std dev of per-trade gross returns (bps)
sharpe        avg_net / std_net  — per-trade Sharpe ratio
t_stat        avg_net / (std_net / √n) — statistical strength
p_value       one-sided P(mean ≤ 0 | data)
skewness      skewness of per-trade net returns
avg_hold_s    avg holding period (seconds)
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.scan.flip_sar import scan

# ── Grid parameters ────────────────────────────────────────────────────────────
BOXES = [0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005]
STEPS = [0.01, 0.02, 0.03, 0.04, 0.05]
SCHEDULES: dict[str, dict] = {
    "nofilter": dict(block_weekday_hours=None, block_weekend_hours=None),
    "active":   dict(block_weekday_hours=None, block_weekend_hours=list(range(24))),
    "inactive": dict(block_weekday_hours=list(range(24)), block_weekend_hours=None),
}

_SCHED_ORDER = {"nofilter": 0, "active": 1, "inactive": 2}

_CSV_FIELDS = [
    "box", "step", "schedule", "n_flips",
    "n_dd_blocked", "n_dd_triggers",
    "avg_gross", "avg_net", "total_net_bps",
    "hit_rate", "std_bps", "sharpe", "t_stat", "p_value", "skewness",
    "avg_hold_s", "status",
]


def _run_grid(start: str, end: str) -> list[dict]:
    total = len(BOXES) * len(STEPS) * len(SCHEDULES)
    rows: list[dict] = []
    done = 0

    for box in BOXES:
        for step in STEPS:
            for sched_name, sched_kwargs in SCHEDULES.items():
                done += 1
                try:
                    r = scan(
                        flip_exchange="BINANCE", flip_symbol="ETH_USDT",
                        target_exchange="GMO", target_symbol="ETH_JPY",
                        start_date=start, end_date=end,
                        box_size_rate=box, sar_step=step,
                        execution_size="0.3",
                        rolling_dd_window_seconds=10800,
                        rolling_dd_threshold_bps=500,
                        rolling_dd_cooldown_seconds=21600,
                        **sched_kwargs,
                    )
                    bf = r["between_flip"]
                    rg = r["risk_gates"]
                    row = {
                        "box":           box,
                        "step":          step,
                        "schedule":      sched_name,
                        "n_flips":       r["n_flips"],
                        "n_dd_blocked":  rg["n_rolling_dd_blocked"],
                        "n_dd_triggers": len(rg.get("dd_triggers", [])),
                        "avg_gross":     round(bf["avg_signed_return_bps"], 3),
                        "avg_net":       round(bf["avg_net_return_bps"], 3),
                        "total_net_bps": round(bf["total_net_bps"], 1),
                        "hit_rate":      round(bf["hit_rate"], 4),
                        "std_bps":       round(bf["std_bps"], 3),
                        "sharpe":        round(bf["sharpe_per_trade"], 4),
                        "t_stat":        round(bf["t_stat"], 3),
                        "p_value":       round(bf["p_value_1sided"], 5),
                        "skewness":      round(bf["skewness"], 3),
                        "avg_hold_s":    round(bf["avg_holding_seconds"]),
                        "status":        "ok",
                    }
                    rows.append(row)
                    print(
                        f"[{done:3d}/{total}] box={box} step={step} sched={sched_name}"
                        f" → n={r['n_flips']} net={bf['avg_net_return_bps']:+.2f}bps"
                        f" t={bf['t_stat']:+.2f} p={bf['p_value_1sided']:.3f}",
                        flush=True,
                    )
                except Exception as e:
                    rows.append({"box": box, "step": step, "schedule": sched_name, "status": f"ERROR: {e}"})
                    print(f"[{done:3d}/{total}] box={box} step={step} sched={sched_name} → ERROR: {e}", flush=True)

    return rows


def _save_csv(rows: list[dict], path: str) -> None:
    ok = [r for r in rows if r.get("status") == "ok"]
    errors = [r for r in rows if r.get("status") != "ok"]

    ok.sort(key=lambda r: (
        _SCHED_ORDER.get(r["schedule"], 99),
        -float(r["total_net_bps"]),
        -int(r["n_flips"]),
    ))

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        w.writeheader()
        w.writerows(ok + errors)
    print(f"\nSaved {path}")


def _print_grouped(rows: list[dict], top_n: int, p_threshold: float) -> None:
    ok = [r for r in rows if r.get("status") == "ok"]

    hdr = f"  {'box':>8}  {'step':>5}  {'n':>6}  {'avg_net':>8}  {'total':>8}  {'std':>7}  {'sharpe':>7}  {'t_stat':>7}  {'p':>7}  {'skew':>6}"
    sep = "  " + "-" * (len(hdr) - 2)

    for sched in ["nofilter", "active", "inactive"]:
        group = [r for r in ok if r["schedule"] == sched]
        group.sort(key=lambda r: (-float(r["total_net_bps"]), -int(r["n_flips"])))

        print(f"\n{'=' * 90}")
        print(f"  Schedule: {sched}")
        print(f"{'=' * 90}")
        print(hdr)
        print(sep)

        for r in group:
            marker = ""
            if (
                top_n > 0
                and float(r["avg_net"]) > 0
                and not math.isnan(float(r["p_value"]))
                and float(r["p_value"]) < p_threshold
            ):
                marker = " *"
            print(
                f"  {r['box']:>8}  {r['step']:>5}  {r['n_flips']:>6}"
                f"  {float(r['avg_net']):>+8.3f}  {float(r['total_net_bps']):>+8.0f}"
                f"  {float(r['std_bps']):>7.3f}  {float(r['sharpe']):>+7.4f}"
                f"  {float(r['t_stat']):>+7.2f}  {float(r['p_value']):>7.4f}"
                f"  {float(r['skewness']):>+6.2f}{marker}"
            )

    if top_n > 0:
        print(f"\n  * = p < {p_threshold} and avg_net > 0  (statistically significant)")

    # Top-N per schedule summary
    if top_n > 0:
        print(f"\n{'=' * 90}")
        print(f"  TOP {top_n} PER SCHEDULE  (p < {p_threshold}, avg_net > 0, ranked by t_stat)")
        print(f"{'=' * 90}")
        for sched in ["nofilter", "active", "inactive"]:
            candidates = [
                r for r in ok
                if r["schedule"] == sched
                and float(r["avg_net"]) > 0
                and not math.isnan(float(r["p_value"]))
                and float(r["p_value"]) < p_threshold
            ]
            candidates.sort(key=lambda r: -float(r["t_stat"]))
            print(f"\n  {sched}:")
            if not candidates:
                print("    (none passed the filter)")
                continue
            print(hdr)
            print(sep)
            for r in candidates[:top_n]:
                print(
                    f"  {r['box']:>8}  {r['step']:>5}  {r['n_flips']:>6}"
                    f"  {float(r['avg_net']):>+8.3f}  {float(r['total_net_bps']):>+8.0f}"
                    f"  {float(r['std_bps']):>7.3f}  {float(r['sharpe']):>+7.4f}"
                    f"  {float(r['t_stat']):>+7.2f}  {float(r['p_value']):>7.4f}"
                    f"  {float(r['skewness']):>+6.2f}"
                )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    ap.add_argument("--end",   required=True, help="End date YYYY-MM-DD")
    ap.add_argument(
        "--out", default=None,
        help="CSV output path (default: results/grid_scan_ETH_<start>_<end>.csv)",
    )
    ap.add_argument("--top",         type=int,   default=5,    help="Top-N candidates per schedule (0 = skip, default 5)")
    ap.add_argument("--p-threshold", type=float, default=0.05, help="p-value threshold for significance marker (default 0.05)")
    args = ap.parse_args()

    out = args.out or f"results/grid_scan_ETH_{args.start}_{args.end}.csv"

    rows = _run_grid(args.start, args.end)
    _save_csv(rows, out)
    _print_grouped(rows, args.top, args.p_threshold)


if __name__ == "__main__":
    main()
