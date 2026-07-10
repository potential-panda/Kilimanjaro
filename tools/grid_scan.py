#!/usr/bin/env python3
"""Run a parameter grid scan for a given market (ETH / XRP / BTC).

Iterates all (box, step, schedule) combinations, collects per-trade statistics,
and writes results to a folder under results/grid_scan/flip/{scan_id}/.

Output folder
-------------
results/grid_scan/flip/{scan_id}/
    params.json          scan configuration
    output.csv           full grid results (all combos, sorted by total_net_bps)
    output_filtered.csv  filtered candidates (passing all output filters)

Usage
-----
    python tools/grid_scan.py --market eth --start 2025-07-01 --end 2026-06-30
    python tools/grid_scan.py --market xrp --start 2025-07-01 --end 2026-06-30
    python tools/grid_scan.py --market btc --start 2025-07-01 --end 2026-06-30
    python tools/grid_scan.py --market eth --start 2026-01-01 --end 2026-06-30 --out results/grid_scan/flip/my_run

Columns in CSV and table
------------------------
box           box_size_rate
step          sar_step
schedule      nofilter / active / inactive
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
import csv
import datetime
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.scan.flip_sar import scan

# ── Grid parameters ────────────────────────────────────────────────────────────
BOXES = [0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005]
STEPS = [0.01, 0.02, 0.03, 0.04, 0.05]

_SCHED_ORDER = {
    "nofilter": 0, "active": 1, "inactive": 2,
    "weekday": 3, "weekend": 4, "us_on": 5, "us_off": 6,
}
_ALL_HOURS   = list(range(24))
_ALL_WEEKEND = _ALL_HOURS  # alias

# Per-market config: flip source, execution target, execution size, and schedules.
# Schedule hours are JST. "block" = skip flips in those hours.
#
# active:   GMO peak hours (JST 06–21 weekdays only)
# inactive: GMO off-peak (JST 22–05 weekdays + all weekends)
# weekday:  all 24h on weekdays, no weekends
# weekend:  weekends only, no weekdays
# us_on:    US market open on weekdays (JST 22–05; block weekday 06–21 + weekends)
# us_off:   US market closed (weekday JST 06–21 + all weekends; block weekday 22–05)
_ETH_BTC_SCHEDULES = {
    "nofilter": dict(block_weekday_hours=None,                                                        block_weekend_hours=None),
    "active":   dict(block_weekday_hours=[22, 23, 0, 1, 2, 3, 4, 5],                                 block_weekend_hours=_ALL_HOURS),
    "inactive": dict(block_weekday_hours=[6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21], block_weekend_hours=None),
    "weekday":  dict(block_weekday_hours=None,                                                        block_weekend_hours=_ALL_HOURS),
    "weekend":  dict(block_weekday_hours=_ALL_HOURS,                                                  block_weekend_hours=None),
    "us_on":    dict(block_weekday_hours=[6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21], block_weekend_hours=_ALL_HOURS),
    "us_off":   dict(block_weekday_hours=[22, 23, 0, 1, 2, 3, 4, 5],                                 block_weekend_hours=None),
}

MARKET_CONFIGS: dict[str, dict] = {
    "eth": {
        "flip_exchange":  "BINANCE",
        "flip_symbol":    "ETH_USDT",
        "target_exchange": "GMO",
        "target_symbol":  "ETH_JPY",
        "execution_size": "0.3",
        "schedules": _ETH_BTC_SCHEDULES,
    },
    "xrp": {
        "flip_exchange":  "BINANCE",
        "flip_symbol":    "XRP_USDT",
        "target_exchange": "GMO",
        "target_symbol":  "XRP_JPY",
        "execution_size": "150",
        # XRP active JST 07–21 (one extra hour blocked vs ETH); us_on/us_off use same US boundary
        "schedules": {
            "nofilter": dict(block_weekday_hours=None,                                                              block_weekend_hours=None),
            "active":   dict(block_weekday_hours=[22, 23, 0, 1, 2, 3, 4, 5, 6],                                    block_weekend_hours=_ALL_HOURS),
            "inactive": dict(block_weekday_hours=[7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],        block_weekend_hours=None),
            "weekday":  dict(block_weekday_hours=None,                                                              block_weekend_hours=_ALL_HOURS),
            "weekend":  dict(block_weekday_hours=_ALL_HOURS,                                                        block_weekend_hours=None),
            "us_on":    dict(block_weekday_hours=[6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],     block_weekend_hours=_ALL_HOURS),
            "us_off":   dict(block_weekday_hours=[22, 23, 0, 1, 2, 3, 4, 5],                                       block_weekend_hours=None),
        },
    },
    "btc": {
        "flip_exchange":  "BINANCE",
        "flip_symbol":    "BTC_USDT",
        "target_exchange": "GMO",
        "target_symbol":  "BTC_JPY",
        "execution_size": "0.01",
        "schedules": _ETH_BTC_SCHEDULES,
    },
}

_CSV_FIELDS = [
    "box", "step", "schedule", "n_flips",
    "n_dd_blocked", "n_dd_triggers",
    "avg_gross", "avg_net", "total_net_bps",
    "hit_rate", "std_bps", "sharpe", "t_stat", "p_value", "skewness",
    "avg_hold_s", "status",
]

# Per-market output filters for output_filtered.csv.
#
# Flip scan t_stats are structurally lower than horizon scan (max ~2–3 in practice),
# so t_stat_min is much lower than the horizon scan's 10.0.
#
# ETH: moderate flip frequency, mid-range std (~60–80 bps)
# XRP: high flip frequency (small box), thin per-trade edge → lower avg_net_min
# BTC: low flip count (large box), higher per-trade returns → higher avg_net_min
FILTERS: dict[str, dict] = {
    "eth": {
        "n_flips_min":       200,
        "avg_net_min":       1.0,
        "t_stat_min":        1.5,
        "p_value_max":       0.05,
        "total_net_bps_min": 5000,
    },
    "xrp": {
        "n_flips_min":       500,   # XRP generates many more flips; require a larger sample
        "avg_net_min":       0.5,   # thinner per-trade edge due to high frequency
        "t_stat_min":        1.5,
        "p_value_max":       0.05,
        "total_net_bps_min": 3000,  # lower total hurdle to reflect smaller per-trade returns
    },
    "btc": {
        "n_flips_min":       100,   # BTC flips are infrequent; 200 would rarely be reached
        "avg_net_min":       2.0,   # higher per-trade bar to compensate for low n
        "t_stat_min":        1.5,
        "p_value_max":       0.05,
        "total_net_bps_min": 3000,
    },
}


def _run_grid(market_cfg: dict, start: str, end: str) -> list[dict]:
    schedules = market_cfg["schedules"]
    total = len(BOXES) * len(STEPS) * len(schedules)
    rows: list[dict] = []
    done = 0

    for box in BOXES:
        for step in STEPS:
            for sched_name, sched_kwargs in schedules.items():
                done += 1
                try:
                    r = scan(
                        flip_exchange=market_cfg["flip_exchange"],
                        flip_symbol=market_cfg["flip_symbol"],
                        target_exchange=market_cfg["target_exchange"],
                        target_symbol=market_cfg["target_symbol"],
                        start_date=start, end_date=end,
                        box_size_rate=box, sar_step=step,
                        execution_size=market_cfg["execution_size"],
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
                        "avg_hold_s":    round(bf["avg_holding_seconds"], 0),
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


def _passes_filters(r: dict, filters: dict) -> bool:
    try:
        return (
            int(r["n_flips"]) >= filters["n_flips_min"]
            and float(r["avg_net"]) >= filters["avg_net_min"]
            and float(r["t_stat"]) >= filters["t_stat_min"]
            and float(r["p_value"]) <= filters["p_value_max"]
            and float(r["total_net_bps"]) >= filters["total_net_bps_min"]
        )
    except (ValueError, KeyError):
        return False


def _write_csv(rows: list[dict], path: Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _write_scan_folder(
    out_dir: Path,
    params: dict,
    rows: list[dict],
    filters: dict,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "params.json").write_text(json.dumps(params, indent=2))

    ok = [r for r in rows if r.get("status") == "ok"]
    errors = [r for r in rows if r.get("status") != "ok"]
    ok.sort(key=lambda r: (
        _SCHED_ORDER.get(r["schedule"], 99),
        -float(r["total_net_bps"]),
        -int(r["n_flips"]),
    ))
    _write_csv(ok + errors, out_dir / "output.csv")
    print(f"Saved {out_dir}/output.csv  ({len(ok)} ok, {len(errors)} errors)")

    filtered = [r for r in ok if _passes_filters(r, filters)]
    filtered.sort(key=lambda r: -float(r["t_stat"]))
    _write_csv(filtered, out_dir / "output_filtered.csv")
    print(f"Saved {out_dir}/output_filtered.csv  ({len(filtered)} rows)")


def _print_grouped(rows: list[dict], top_n: int, p_threshold: float) -> None:
    ok = [r for r in rows if r.get("status") == "ok"]

    hdr = f"  {'box':>8}  {'step':>5}  {'n':>6}  {'avg_net':>8}  {'total':>8}  {'std':>7}  {'sharpe':>7}  {'t_stat':>7}  {'p':>7}  {'skew':>6}"
    sep = "  " + "-" * (len(hdr) - 2)

    for sched in ["nofilter", "active", "inactive", "weekday", "weekend", "us_on", "us_off"]:
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

    if top_n > 0:
        print(f"\n{'=' * 90}")
        print(f"  TOP {top_n} PER SCHEDULE  (p < {p_threshold}, avg_net > 0, ranked by t_stat)")
        print(f"{'=' * 90}")
        for sched in ["nofilter", "active", "inactive", "weekday", "weekend", "us_on", "us_off"]:
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
    ap.add_argument("--market", required=True, choices=list(MARKET_CONFIGS), help="Market to scan: eth / xrp / btc")
    ap.add_argument("--start",  required=True, help="Start date YYYY-MM-DD")
    ap.add_argument("--end",    required=True, help="End date YYYY-MM-DD")
    ap.add_argument("--size",   default=None,  help="Override execution_size (e.g. 0.01); default from market config")
    ap.add_argument("--out",    default=None,  help="Output folder (default: results/grid_scan/flip/{scan_id})")
    ap.add_argument("--top",         type=int,   default=5,    help="Top-N candidates per schedule (0 = skip, default 5)")
    ap.add_argument("--p-threshold", type=float, default=0.05, help="p-value threshold for significance marker (default 0.05)")
    args = ap.parse_args()

    market_cfg = dict(MARKET_CONFIGS[args.market])  # shallow copy so we can override
    if args.size is not None:
        market_cfg["execution_size"] = args.size

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    scan_id = f"{args.market.upper()}_{args.start}_{args.end}_{ts}"
    out_dir = Path(args.out) if args.out else Path(f"results/grid_scan/flip/{scan_id}")

    filters = FILTERS[args.market]

    params = {
        "scan_id": scan_id,
        "market": args.market,
        "start": args.start,
        "end": args.end,
        "flip_exchange":   market_cfg["flip_exchange"],
        "flip_symbol":     market_cfg["flip_symbol"],
        "target_exchange": market_cfg["target_exchange"],
        "target_symbol":   market_cfg["target_symbol"],
        "execution_size":  market_cfg["execution_size"],
        "grid": {
            "boxes":  BOXES,
            "steps":  STEPS,
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
    print(
        f"Grid: {len(BOXES)} boxes × {len(STEPS)} steps × {len(market_cfg['schedules'])} schedules"
        f" = {len(BOXES)*len(STEPS)*len(market_cfg['schedules'])} combos"
    )
    print(f"Filters ({args.market}): {filters}")
    print(f"Output: {out_dir}/")

    rows = _run_grid(market_cfg, args.start, args.end)
    _write_scan_folder(out_dir, params, rows, filters)
    _print_grouped(rows, args.top, args.p_threshold)
    print(f"\nScan ID: {scan_id}")
    print(f"Results: {out_dir}/")


if __name__ == "__main__":
    main()
