#!/usr/bin/env python3
"""Run a parameter grid scan for a given market (ETH / XRP / BTC).

Iterates all (box, step, schedule) combinations, collects per-trade statistics,
and writes results to a folder under results/grid_scan/flip/{scan_id}/.

Train/holdout split
-------------------
With --split, the grid is selected on the train window [start, split) and the
filtered candidates are re-scanned on the held-out window [split, end]. This
is the anti-overfitting discipline from doc/direction/evaluation-framework.md:
never report a candidate on the same window it was selected on. Without
--split, the whole window is both selection and report (legacy behavior).

Output folder
-------------
results/grid_scan/flip/{scan_id}/
    params.json          scan configuration (grid, filters, risk gate, windows)
    output.csv           full grid results on the train window
    output_filtered.csv  candidates passing all output filters (train window)
    output_holdout.csv   (only with --split) train-filtered candidates
                         re-scanned on the holdout window, with train stats
                         alongside for side-by-side comparison

Usage
-----
    python tools/grid_scan.py --market eth --start 2025-07-01 --end 2026-06-30 --split 2026-03-01
    python tools/grid_scan.py --market xrp --start 2025-07-01 --end 2026-06-30 --split 2026-03-01
    python tools/grid_scan.py --market btc --start 2025-07-01 --end 2026-06-30
    python tools/grid_scan.py --market eth --start 2026-01-01 --end 2026-06-30 --out results/grid_scan/flip/my_run

Columns in CSV and table
------------------------
box           box_size_rate
step          sar_step
schedule      nofilter / active / inactive / weekday / weekend / us_on / us_off
n_flips       executed flips (after all risk gates)
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
import datetime
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.framework import write_csv, write_scan_folder
from kilimanjaro.scan.flip_sar import scan

# ── Grid parameters ────────────────────────────────────────────────────────────
BOXES = [0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005]
STEPS = [0.01, 0.02, 0.03, 0.04, 0.05]

# ── Rolling-DD risk gate defaults (override via --dd-* CLI args) ───────────────
# NOTE: these differ from the doc/flip-scan-baseline.md calibration
# (window=21600s, threshold=1000bps); retuned in commit 8b53b6f1. Whatever is
# used is recorded in params.json so every run is reproducible from its folder.
DD_WINDOW_SECONDS = 10800
DD_THRESHOLD_BPS = 500
DD_COOLDOWN_SECONDS = 21600

_SCHED_ORDER = {
    "nofilter": 0,
    "active": 1,
    "inactive": 2,
    "weekday": 3,
    "weekend": 4,
    "us_on": 5,
    "us_off": 6,
}
_ALL_HOURS = list(range(24))
# US-session boundary (fixed across markets): weekday JST 22–05 = US market open
_US_NIGHT_HOURS = [h for h in range(24) if not 6 <= h <= 21]
_US_DAY_HOURS = list(range(6, 22))


def _make_schedules(active_start_hour: int) -> dict[str, dict]:
    """Build the 7 named schedules for a market.

    Schedule hours are JST; "block" = skip flips in those hours. Naming caveat:
    "active"/"inactive" here refer to the GMO daytime peak, the OPPOSITE of the
    older doc/flip-scan-baseline.md variants where "active" meant the US-night
    window — see that doc's naming-map section. The old meaning is available
    here as us_on/us_off.

    active:   GMO peak hours (JST {active_start_hour}–21 weekdays only)
    inactive: GMO off-peak (remaining weekday hours + all weekends)
    weekday:  all 24h on weekdays, no weekends
    weekend:  weekends only, no weekdays
    us_on:    US market open on weekdays (JST 22–05; block weekday 06–21 + weekends)
    us_off:   US market closed (weekday JST 06–21 + all weekends; block weekday 22–05)
    """
    active_hours = list(range(active_start_hour, 22))
    off_hours = [h for h in range(24) if h not in active_hours]
    return {
        "nofilter": dict(block_weekday_hours=None, block_weekend_hours=None),
        "active": dict(block_weekday_hours=off_hours, block_weekend_hours=_ALL_HOURS),
        "inactive": dict(block_weekday_hours=active_hours, block_weekend_hours=None),
        "weekday": dict(block_weekday_hours=None, block_weekend_hours=_ALL_HOURS),
        "weekend": dict(block_weekday_hours=_ALL_HOURS, block_weekend_hours=None),
        "us_on": dict(
            block_weekday_hours=_US_DAY_HOURS, block_weekend_hours=_ALL_HOURS
        ),
        "us_off": dict(block_weekday_hours=_US_NIGHT_HOURS, block_weekend_hours=None),
    }


# Per-market config: flip source, execution target, execution size, and schedules.
# XRP's active window starts one hour later (JST 07–21 vs 06–21 for ETH/BTC).
MARKET_CONFIGS: dict[str, dict] = {
    "eth": {
        "flip_exchange": "BINANCE",
        "flip_symbol": "ETH_USDT",
        "target_exchange": "GMO",
        "target_symbol": "ETH_JPY",
        "execution_size": "0.3",
        "schedules": _make_schedules(6),
    },
    "xrp": {
        "flip_exchange": "BINANCE",
        "flip_symbol": "XRP_USDT",
        "target_exchange": "GMO",
        "target_symbol": "XRP_JPY",
        "execution_size": "150",
        "schedules": _make_schedules(7),
    },
    "btc": {
        "flip_exchange": "BINANCE",
        "flip_symbol": "BTC_USDT",
        "target_exchange": "GMO",
        "target_symbol": "BTC_JPY",
        "execution_size": "0.01",
        "schedules": _make_schedules(6),
    },
}

_CSV_FIELDS = [
    "box",
    "step",
    "schedule",
    "n_flips",
    "n_dd_blocked",
    "n_dd_triggers",
    "avg_gross",
    "avg_net",
    "total_net_bps",
    "hit_rate",
    "std_bps",
    "sharpe",
    "t_stat",
    "p_value",
    "skewness",
    "avg_hold_s",
    "status",
]

# Holdout CSV: holdout stats in the normal columns, train stats alongside.
_HOLDOUT_FIELDS = _CSV_FIELDS[:-1] + [
    "holdout_pass",
    "train_n_flips",
    "train_avg_net",
    "train_total_net_bps",
    "train_t_stat",
    "train_p_value",
    "status",
]

# Per-market output filters for output_filtered.csv.
#
# Flip scan t_stats are structurally lower than horizon scan (max ~2–3 in
# practice), so t_stat_min is much lower than the horizon scan's 10.0.
# Currently identical across markets (simplified in d32eeef3); kept per-market
# so they can diverge again without touching the code paths.
FILTERS: dict[str, dict] = {
    "eth": {
        "avg_net_min": 0.0,
        "t_stat_min": 1.5,
        "p_value_max": 0.05,
    },
    "xrp": {
        "avg_net_min": 0.0,
        "t_stat_min": 1.5,
        "p_value_max": 0.05,
    },
    "btc": {
        "avg_net_min": 0.0,
        "t_stat_min": 1.5,
        "p_value_max": 0.05,
    },
}


def split_windows(start: str, split: str, end: str) -> tuple[str, str, str, str]:
    """Return (train_start, train_end, holdout_start, holdout_end).

    The split date belongs to the holdout window; the train window ends the
    day before, so the two windows never overlap (scan dates are inclusive).
    """
    d_start = datetime.date.fromisoformat(start)
    d_split = datetime.date.fromisoformat(split)
    d_end = datetime.date.fromisoformat(end)
    if not d_start < d_split <= d_end:
        raise ValueError(f"--split must satisfy start < split <= end, got {start} < {split} <= {end}")
    train_end = (d_split - datetime.timedelta(days=1)).isoformat()
    return start, train_end, split, end


def _scan_one(
    market_cfg: dict,
    box: float,
    step: float,
    sched_name: str,
    sched_kwargs: dict,
    start: str,
    end: str,
    dd: dict,
) -> dict:
    """Run one (box, step, schedule) combo; return a CSV row dict."""
    try:
        r = scan(
            flip_exchange=market_cfg["flip_exchange"],
            flip_symbol=market_cfg["flip_symbol"],
            target_exchange=market_cfg["target_exchange"],
            target_symbol=market_cfg["target_symbol"],
            start_date=start,
            end_date=end,
            box_size_rate=box,
            sar_step=step,
            execution_size=market_cfg["execution_size"],
            rolling_dd_window_seconds=dd["window_seconds"],
            rolling_dd_threshold_bps=dd["threshold_bps"],
            rolling_dd_cooldown_seconds=dd["cooldown_seconds"],
            **sched_kwargs,
        )
        bf = r["between_flip"]
        rg = r["risk_gates"]
        return {
            "box": box,
            "step": step,
            "schedule": sched_name,
            "n_flips": r["n_flips"],
            "n_dd_blocked": rg["n_rolling_dd_blocked"],
            "n_dd_triggers": len(rg.get("dd_triggers", [])),
            "avg_gross": round(bf["avg_signed_return_bps"], 3),
            "avg_net": round(bf["avg_net_return_bps"], 3),
            "total_net_bps": round(bf["total_net_bps"], 1),
            "hit_rate": round(bf["hit_rate"], 4),
            "std_bps": round(bf["std_bps"], 3),
            "sharpe": round(bf["sharpe_per_trade"], 4),
            "t_stat": round(bf["t_stat"], 3),
            "p_value": round(bf["p_value_1sided"], 5),
            "skewness": round(bf["skewness"], 3),
            "avg_hold_s": round(bf["avg_holding_seconds"], 0),
            "status": "ok",
        }
    except Exception as e:
        return {"box": box, "step": step, "schedule": sched_name, "status": f"ERROR: {e}"}


def _run_grid(market_cfg: dict, start: str, end: str, dd: dict) -> list[dict]:
    schedules = market_cfg["schedules"]
    total = len(BOXES) * len(STEPS) * len(schedules)
    rows: list[dict] = []
    done = 0

    for box in BOXES:
        for step in STEPS:
            for sched_name, sched_kwargs in schedules.items():
                done += 1
                row = _scan_one(
                    market_cfg, box, step, sched_name, sched_kwargs, start, end, dd
                )
                rows.append(row)
                if row["status"] == "ok":
                    print(
                        f"[{done:3d}/{total}] box={box} step={step} sched={sched_name}"
                        f" → n={row['n_flips']} net={row['avg_net']:+.2f}bps"
                        f" t={row['t_stat']:+.2f} p={row['p_value']:.3f}",
                        flush=True,
                    )
                else:
                    print(
                        f"[{done:3d}/{total}] box={box} step={step} sched={sched_name}"
                        f" → {row['status']}",
                        flush=True,
                    )

    return rows


def _run_holdout(
    market_cfg: dict,
    train_filtered: list[dict],
    start: str,
    end: str,
    dd: dict,
    filters: dict,
) -> list[dict]:
    """Re-scan the train-filtered candidates on the holdout window."""
    schedules = market_cfg["schedules"]
    rows: list[dict] = []
    total = len(train_filtered)

    for i, tr in enumerate(train_filtered, 1):
        row = _scan_one(
            market_cfg,
            tr["box"],
            tr["step"],
            tr["schedule"],
            schedules[tr["schedule"]],
            start,
            end,
            dd,
        )
        if row["status"] == "ok":
            row["holdout_pass"] = _passes_filters(row, filters)
            print(
                f"[holdout {i:3d}/{total}] box={tr['box']} step={tr['step']}"
                f" sched={tr['schedule']} → n={row['n_flips']}"
                f" net={row['avg_net']:+.2f}bps t={row['t_stat']:+.2f}"
                f" {'PASS' if row['holdout_pass'] else 'fail'}"
                f"  (train net={tr['avg_net']:+.2f} t={tr['t_stat']:+.2f})",
                flush=True,
            )
        else:
            row["holdout_pass"] = False
            print(
                f"[holdout {i:3d}/{total}] box={tr['box']} step={tr['step']}"
                f" sched={tr['schedule']} → {row['status']}",
                flush=True,
            )
        row["train_n_flips"] = tr["n_flips"]
        row["train_avg_net"] = tr["avg_net"]
        row["train_total_net_bps"] = tr["total_net_bps"]
        row["train_t_stat"] = tr["t_stat"]
        row["train_p_value"] = tr["p_value"]
        rows.append(row)

    return rows


def _passes_filters(r: dict, filters: dict) -> bool:
    try:
        return (
            float(r["avg_net"]) >= filters["avg_net_min"]
            and float(r["t_stat"]) >= filters["t_stat_min"]
            and float(r["p_value"]) <= filters["p_value_max"]
        )
    except (ValueError, KeyError):
        return False


def _print_grouped(rows: list[dict], top_n: int, p_threshold: float) -> None:
    ok = [r for r in rows if r.get("status") == "ok"]

    hdr = f"  {'box':>8}  {'step':>5}  {'n':>6}  {'avg_net':>8}  {'total':>8}  {'std':>7}  {'sharpe':>7}  {'t_stat':>7}  {'p':>7}  {'skew':>6}"
    sep = "  " + "-" * (len(hdr) - 2)

    def _print_row(r: dict, marker: str = "") -> None:
        print(
            f"  {r['box']:>8}  {r['step']:>5}  {r['n_flips']:>6}"
            f"  {float(r['avg_net']):>+8.3f}  {float(r['total_net_bps']):>+8.0f}"
            f"  {float(r['std_bps']):>7.3f}  {float(r['sharpe']):>+7.4f}"
            f"  {float(r['t_stat']):>+7.2f}  {float(r['p_value']):>7.4f}"
            f"  {float(r['skewness']):>+6.2f}{marker}"
        )

    for sched in _SCHED_ORDER:
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
            _print_row(r, marker)

    if top_n > 0:
        print(f"\n  * = p < {p_threshold} and avg_net > 0  (statistically significant)")

        print(f"\n{'=' * 90}")
        print(
            f"  TOP {top_n} PER SCHEDULE  (p < {p_threshold}, avg_net > 0, ranked by t_stat)"
        )
        print(f"{'=' * 90}")
        for sched in _SCHED_ORDER:
            candidates = [
                r
                for r in ok
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
                _print_row(r)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--market",
        required=True,
        choices=list(MARKET_CONFIGS),
        help="Market to scan: eth / xrp / btc",
    )
    ap.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    ap.add_argument(
        "--split",
        default=None,
        help="Holdout split date YYYY-MM-DD: select the grid on [start, split),"
        " report filtered candidates on [split, end] (output_holdout.csv)",
    )
    ap.add_argument(
        "--size",
        default=None,
        help="Override execution_size (e.g. 0.01); default from market config",
    )
    ap.add_argument(
        "--dd-window-seconds",
        type=float,
        default=DD_WINDOW_SECONDS,
        help=f"Rolling-DD gate window in seconds (default {DD_WINDOW_SECONDS})",
    )
    ap.add_argument(
        "--dd-threshold-bps",
        type=float,
        default=DD_THRESHOLD_BPS,
        help=f"Rolling-DD gate threshold in bps (default {DD_THRESHOLD_BPS})",
    )
    ap.add_argument(
        "--dd-cooldown-seconds",
        type=float,
        default=DD_COOLDOWN_SECONDS,
        help=f"Rolling-DD gate cooldown in seconds (default {DD_COOLDOWN_SECONDS})",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Output folder (default: results/grid_scan/flip/{scan_id})",
    )
    ap.add_argument(
        "--top",
        type=int,
        default=5,
        help="Top-N candidates per schedule (0 = skip, default 5)",
    )
    ap.add_argument(
        "--p-threshold",
        type=float,
        default=0.05,
        help="p-value threshold for significance marker (default 0.05)",
    )
    args = ap.parse_args()

    market_cfg = dict(MARKET_CONFIGS[args.market])  # shallow copy so we can override
    if args.size is not None:
        market_cfg["execution_size"] = args.size

    dd = {
        "window_seconds": args.dd_window_seconds,
        "threshold_bps": args.dd_threshold_bps,
        "cooldown_seconds": args.dd_cooldown_seconds,
    }

    if args.split:
        train_start, train_end, holdout_start, holdout_end = split_windows(
            args.start, args.split, args.end
        )
    else:
        train_start, train_end = args.start, args.end
        holdout_start = holdout_end = None

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    scan_id = f"{args.market.upper()}_{args.start}_{args.end}_{ts}"
    out_dir = Path(args.out) if args.out else Path(f"results/grid_scan/flip/{scan_id}")

    filters = FILTERS[args.market]

    params = {
        "scan_id": scan_id,
        "market": args.market,
        "start": args.start,
        "end": args.end,
        "train_window": [train_start, train_end],
        "holdout_window": [holdout_start, holdout_end] if args.split else None,
        "flip_exchange": market_cfg["flip_exchange"],
        "flip_symbol": market_cfg["flip_symbol"],
        "target_exchange": market_cfg["target_exchange"],
        "target_symbol": market_cfg["target_symbol"],
        "execution_size": market_cfg["execution_size"],
        "risk_gate": {
            "rolling_dd_window_seconds": dd["window_seconds"],
            "rolling_dd_threshold_bps": dd["threshold_bps"],
            "rolling_dd_cooldown_seconds": dd["cooldown_seconds"],
        },
        "grid": {
            "boxes": BOXES,
            "steps": STEPS,
            "schedules": list(market_cfg["schedules"].keys()),
        },
        "filters": filters,
    }

    print(
        f"Flip grid scan: {market_cfg['flip_exchange']}/{market_cfg['flip_symbol']}"
        f" → {market_cfg['target_exchange']}/{market_cfg['target_symbol']}"
        f"  size={market_cfg['execution_size']}"
        f"  {args.start} .. {args.end}"
    )
    if args.split:
        print(
            f"Split: train {train_start} .. {train_end}"
            f"  |  holdout {holdout_start} .. {holdout_end}"
        )
    print(
        f"Grid: {len(BOXES)} boxes × {len(STEPS)} steps × {len(market_cfg['schedules'])} schedules"
        f" = {len(BOXES) * len(STEPS) * len(market_cfg['schedules'])} combos"
    )
    print(f"Risk gate: rolling_dd {dd}")
    print(f"Filters ({args.market}): {filters}")
    print(f"Output: {out_dir}/")

    rows = _run_grid(market_cfg, train_start, train_end, dd)

    ok = [r for r in rows if r.get("status") == "ok"]
    errors = [r for r in rows if r.get("status") != "ok"]
    ok.sort(
        key=lambda r: (
            _SCHED_ORDER.get(r["schedule"], 99),
            -float(r["total_net_bps"]),
            -int(r["n_flips"]),
        )
    )
    filtered = [r for r in ok if _passes_filters(r, filters)]
    filtered.sort(key=lambda r: -float(r["t_stat"]))

    write_scan_folder(out_dir, params, ok + errors, filtered, _CSV_FIELDS)
    print(f"Saved {out_dir}/output.csv  ({len(ok)} ok, {len(errors)} errors)")
    print(f"Saved {out_dir}/output_filtered.csv  ({len(filtered)} rows)")

    if args.split:
        print(f"\nHoldout: re-scanning {len(filtered)} train candidates"
              f" on {holdout_start} .. {holdout_end}")
        holdout_rows = _run_holdout(
            market_cfg, filtered, holdout_start, holdout_end, dd, filters
        )
        holdout_rows.sort(
            key=lambda r: -float(r["t_stat"]) if r["status"] == "ok" else math.inf
        )
        write_csv(holdout_rows, out_dir / "output_holdout.csv", _HOLDOUT_FIELDS)
        n_pass = sum(1 for r in holdout_rows if r.get("holdout_pass"))
        print(
            f"Saved {out_dir}/output_holdout.csv"
            f"  ({n_pass}/{len(holdout_rows)} candidates survive holdout)"
        )

    _print_grouped(rows, args.top, args.p_threshold)
    print(f"\nScan ID: {scan_id}")
    print(f"Results: {out_dir}/")


if __name__ == "__main__":
    main()
