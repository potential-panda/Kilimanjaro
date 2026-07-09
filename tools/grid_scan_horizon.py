#!/usr/bin/env python3
"""Grid scan for the fixed-horizon OBI precision scanner.

Sweeps (depth × obi_halflife × age_window × threshold × horizon) and reports
IC, hit rate, avg_gross, t_stat per combination.

Output folder
-------------
results/grid_scan/horizon/{scan_id}/
    params.json          scan configuration
    output.csv           full grid results (all combos)
    output_filtered.csv  multi-gate filtered candidates
    age_stats.csv        age distribution per (depth, halflife, threshold)

age_stats columns
-----------------
depth, halflife, threshold, n_episodes, ep_per_day,
age_mean, age_p25, age_p50, age_p75, age_p90, age_max

output.csv columns
------------------
depth, halflife, min_age, max_age, threshold, horizon,
n_signals, avg_gross, avg_net, hit_rate, ic, t_stat, p_value, total_net_bps, status

Usage
-----
    python tools/grid_scan_horizon.py --market eth --start 2026-05-01 --end 2026-06-30
    python tools/grid_scan_horizon.py --market eth --start 2026-05-01 --end 2026-06-30 --out results/grid_scan/horizon/my_run
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.op.age import threshold_age
from kilimanjaro.scan.horizon_scan import load_obi_series, scan

# ── Grid parameters ────────────────────────────────────────────────────────────
DEPTHS      = [1, 3, 5, 10]
HALFLIVES   = [10, 30, 60, 120, 300]
THRESHOLDS  = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
HORIZONS    = [10, 30, 60, 120, 300, 600]

# (min_age, max_age) windows in seconds. None = no bound on that side.
# Informed by age histogram: episodes are exponential-decay shaped, with
# fresh crossings noisy (0–30s) and late episodes exhausted (>300s).
# Target the confirmed-but-not-exhausted middle of each episode.
AGE_WINDOWS: list[tuple[int | None, int | None]] = [
    (None, None),    # level signal baseline (no filter)
    (0,    60),      # fresh only, short window
    (0,    180),     # fresh + early middle
    (10,   120),     # skip instant crossing, short-medium
    (10,   300),     # skip instant crossing, full middle
    (30,   120),     # confirmed entry, short
    (30,   180),     # confirmed entry, medium  ← expected sweet spot
    (30,   300),     # confirmed entry, long
    (60,   180),     # established, medium
    (60,   300),     # established, long
]

# Multi-gate filter applied to produce output_filtered.csv
FILTER_GATES = {
    "n_signals_min":  10,
    "n_signals_max":  1500,
    "ic_min":         0.3,
    "t_stat_min":     10.0,
    "p_value_max":    0.01,
    "avg_gross_min":  1.0,
}

MARKET_CONFIGS: dict[str, dict] = {
    "eth": {
        "signal_exchange":  "BINANCE",
        "signal_symbol":    "ETH_USDT",
        "target_exchange":  "GMO",
        "target_symbol":    "ETH_JPY",
        "execution_size":   "0.3",
    },
    "xrp": {
        "signal_exchange":  "BINANCE",
        "signal_symbol":    "XRP_USDT",
        "target_exchange":  "GMO",
        "target_symbol":    "XRP_JPY",
        "execution_size":   "150",
    },
    "btc": {
        "signal_exchange":  "BINANCE",
        "signal_symbol":    "BTC_USDT",
        "target_exchange":  "GMO",
        "target_symbol":    "BTC_JPY",
        "execution_size":   "0.01",
    },
}

_CSV_FIELDS = [
    "depth", "halflife", "min_age", "max_age", "threshold", "horizon",
    "n_signals", "avg_gross", "avg_net", "hit_rate",
    "ic", "t_stat", "p_value", "total_net_bps", "status",
]

_AGE_STAT_FIELDS = [
    "depth", "halflife", "threshold",
    "n_episodes", "ep_per_day",
    "age_mean", "age_p25", "age_p50", "age_p75", "age_p90", "age_max",
]

_HL_LABEL = {None: "raw", 10: "10s", 30: "30s", 60: "60s", 120: "120s", 300: "300s"}


def _age_label(v: int | None) -> str:
    return "any" if v is None else f"{v}s"


def _compute_age_stats(obi_series, n_days: int) -> list[dict]:
    """Compute age distribution stats for all thresholds. Called once per (depth, halflife)."""
    stats = []
    for thr in THRESHOLDS:
        ages = threshold_age(obi_series["obi"], thr)
        active = ages[ages >= 0]
        n_ep = int((ages == 0).sum())
        if len(active) == 0:
            stats.append({
                "threshold": thr, "n_episodes": 0, "ep_per_day": 0.0,
                "age_mean": None, "age_p25": None, "age_p50": None,
                "age_p75": None, "age_p90": None, "age_max": None,
            })
        else:
            stats.append({
                "threshold":  thr,
                "n_episodes": n_ep,
                "ep_per_day": round(n_ep / n_days, 2),
                "age_mean":   round(float(active.mean()), 1),
                "age_p25":    round(float(active.quantile(0.25)), 1),
                "age_p50":    round(float(active.quantile(0.50)), 1),
                "age_p75":    round(float(active.quantile(0.75)), 1),
                "age_p90":    round(float(active.quantile(0.90)), 1),
                "age_max":    int(active.max()),
            })
    return stats


def _run_grid(
    market_cfg: dict, start: str, end: str
) -> tuple[list[dict], list[dict]]:
    """Run the full grid sweep. Returns (scan_rows, age_stat_rows)."""
    total = len(DEPTHS) * len(HALFLIVES) * len(AGE_WINDOWS) * len(THRESHOLDS) * len(HORIZONS)
    n_days = max((datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days, 1)
    rows: list[dict] = []
    age_stats: list[dict] = []
    done = 0

    for depth in DEPTHS:
        for halflife in HALFLIVES:
            print(f"  Loading OBI series: depth={depth} halflife={_HL_LABEL[halflife]} …", flush=True)
            try:
                obi_series = load_obi_series(
                    exchange=market_cfg["signal_exchange"],
                    symbol=market_cfg["signal_symbol"],
                    start_date=start,
                    end_date=end,
                    depth=depth,
                    obi_halflife=halflife,
                )
            except Exception as e:
                for (min_age, max_age) in AGE_WINDOWS:
                    for threshold in THRESHOLDS:
                        for horizon in HORIZONS:
                            done += 1
                            rows.append({
                                "depth": depth, "halflife": _HL_LABEL[halflife],
                                "min_age": _age_label(min_age), "max_age": _age_label(max_age),
                                "threshold": threshold, "horizon": horizon,
                                "status": f"ERROR: {e}",
                            })
                continue

            # Compute age stats for all thresholds while series is loaded
            for stat in _compute_age_stats(obi_series, n_days):
                age_stats.append({
                    "depth":    depth,
                    "halflife": _HL_LABEL[halflife],
                    **stat,
                })

            for (min_age, max_age) in AGE_WINDOWS:
                for threshold in THRESHOLDS:
                    for horizon in HORIZONS:
                        done += 1
                        label = (
                            f"d{depth} hl={_HL_LABEL[halflife]}"
                            f" age=[{_age_label(min_age)},{_age_label(max_age)})"
                            f" thr={threshold} H={horizon}s"
                        )
                        try:
                            r = scan(
                                signal_exchange=market_cfg["signal_exchange"],
                                signal_symbol=market_cfg["signal_symbol"],
                                target_exchange=market_cfg["target_exchange"],
                                target_symbol=market_cfg["target_symbol"],
                                start_date=start,
                                end_date=end,
                                depth=depth,
                                obi_halflife=halflife,
                                min_age_seconds=min_age,
                                max_age_seconds=max_age,
                                threshold=threshold,
                                horizon_seconds=horizon,
                                execution_size=market_cfg["execution_size"],
                                _obi_series=obi_series,
                            )
                            fh = r["fixed_horizon"]
                            rows.append({
                                "depth":         depth,
                                "halflife":      _HL_LABEL[halflife],
                                "min_age":       _age_label(min_age),
                                "max_age":       _age_label(max_age),
                                "threshold":     threshold,
                                "horizon":       horizon,
                                "n_signals":     round(r["signals_per_day"], 1),
                                "avg_gross":     round(fh["avg_signed_return_bps"], 4),
                                "avg_net":       round(fh["avg_net_return_bps"], 4),
                                "hit_rate":      round(fh["hit_rate"], 4),
                                "ic":            round(fh["ic"], 5),
                                "t_stat":        round(fh["t_stat"], 3),
                                "p_value":       round(fh["p_value_1sided"], 5),
                                "total_net_bps": round(fh["total_net_bps"], 1),
                                "status":        "ok",
                            })
                            print(
                                f"[{done:5d}/{total}] {label}"
                                f" → n/day={r['signals_per_day']:.0f}"
                                f" gross={fh['avg_signed_return_bps']:+.3f}bps"
                                f" IC={fh['ic']:+.4f}"
                                f" t={fh['t_stat']:+.2f}",
                                flush=True,
                            )
                        except Exception as e:
                            rows.append({
                                "depth": depth, "halflife": _HL_LABEL[halflife],
                                "min_age": _age_label(min_age), "max_age": _age_label(max_age),
                                "threshold": threshold, "horizon": horizon,
                                "status": f"ERROR: {e}",
                            })
                            print(f"[{done:5d}/{total}] {label} → ERROR: {e}", flush=True)

    return rows, age_stats


def _passes_gates(r: dict) -> bool:
    g = FILTER_GATES
    try:
        return (
            float(r["n_signals"]) >= g["n_signals_min"]
            and float(r["n_signals"]) < g["n_signals_max"]
            and r["ic"] not in ("nan", "")
            and float(r["ic"]) > g["ic_min"]
            and float(r["t_stat"]) > g["t_stat_min"]
            and float(r["p_value"]) < g["p_value_max"]
            and float(r["avg_gross"]) > g["avg_gross_min"]
        )
    except (ValueError, KeyError):
        return False


def _write_csv(rows: list[dict], path: Path, fields: list[str]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _print_summary(rows: list[dict], top_n: int) -> None:
    ok = [r for r in rows if r.get("status") == "ok"]

    hdr = (
        f"  {'d':>3}  {'hl':>5}  {'age_min':>7}  {'age_max':>7}  {'thr':>5}  {'H':>4}"
        f"  {'n/day':>6}  {'gross':>7}  {'net':>7}  {'hit%':>6}  {'IC':>7}  {'t':>7}"
    )
    sep = "  " + "-" * (len(hdr) - 2)

    def _fmt(r: dict) -> str:
        return (
            f"  {r['depth']:>3}  {r['halflife']:>5}  {r['min_age']:>7}  {r['max_age']:>7}"
            f"  {r['threshold']:>5}  {r['horizon']:>4}"
            f"  {float(r['n_signals']):>6.0f}  {float(r['avg_gross']):>+7.3f}  {float(r['avg_net']):>+7.3f}"
            f"  {float(r['hit_rate']):>5.1%}  {float(r['ic']):>+7.4f}  {float(r['t_stat']):>+7.2f}"
        )

    print(f"\n{'=' * 90}")
    print(f"  TOP {top_n} BY IC")
    print(f"{'=' * 90}")
    print(hdr); print(sep)
    for r in sorted(ok, key=lambda r: -float(r["ic"]))[:top_n]:
        print(_fmt(r))

    print(f"\n{'=' * 90}")
    print(f"  TOP {top_n} BY AVG_GROSS  (where avg_gross > 0)")
    print(f"{'=' * 90}")
    print(hdr); print(sep)
    for r in sorted([r for r in ok if float(r["avg_gross"]) > 0], key=lambda r: -float(r["avg_gross"]))[:top_n]:
        print(_fmt(r))

    profitable = [r for r in ok if float(r["avg_net"]) > 0]
    print(f"\n  Profitable combos (avg_net > 0): {len(profitable)} / {len(ok)}")
    filtered = [r for r in ok if _passes_gates(r)]
    print(f"  Passing all gates:               {len(filtered)} / {len(ok)}")
    if filtered:
        print(f"\n{'=' * 90}")
        print(f"  PASSING ALL GATES  (ranked by IC × t_stat)")
        print(f"{'=' * 90}")
        print(hdr); print(sep)
        for r in sorted(filtered, key=lambda r: -(float(r["ic"]) * float(r["t_stat"])))[:top_n]:
            print(_fmt(r))


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--market", required=True, choices=list(MARKET_CONFIGS))
    ap.add_argument("--start",  required=True, help="Start date YYYY-MM-DD")
    ap.add_argument("--end",    required=True, help="End date YYYY-MM-DD")
    ap.add_argument("--out",    default=None,  help="Output folder (default: results/grid_scan/horizon/{scan_id})")
    ap.add_argument("--top",    type=int, default=20, help="Top-N rows to print (default 20)")
    args = ap.parse_args()

    cfg = MARKET_CONFIGS[args.market]
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    scan_id = f"{args.market.upper()}_{args.start}_{args.end}_{ts}"
    out_dir = Path(args.out) if args.out else Path(f"results/grid_scan/horizon/{scan_id}")
    out_dir.mkdir(parents=True, exist_ok=True)

    params = {
        "scan_id": scan_id,
        "market": args.market,
        "start": args.start,
        "end": args.end,
        "signal_exchange":  cfg["signal_exchange"],
        "signal_symbol":    cfg["signal_symbol"],
        "target_exchange":  cfg["target_exchange"],
        "target_symbol":    cfg["target_symbol"],
        "execution_size":   cfg["execution_size"],
        "grid": {
            "depths":      DEPTHS,
            "halflives":   HALFLIVES,
            "age_windows": [[mn, mx] for (mn, mx) in AGE_WINDOWS],
            "thresholds":  THRESHOLDS,
            "horizons":    HORIZONS,
        },
        "filter_gates": FILTER_GATES,
    }
    (out_dir / "params.json").write_text(json.dumps(params, indent=2))

    print(
        f"Horizon grid scan: {cfg['signal_exchange']}/{cfg['signal_symbol']}"
        f" → {cfg['target_exchange']}/{cfg['target_symbol']}"
        f"  {args.start} .. {args.end}"
    )
    print(
        f"Grid: {len(DEPTHS)} depths × {len(HALFLIVES)} halflives"
        f" × {len(AGE_WINDOWS)} age_windows × {len(THRESHOLDS)} thresholds × {len(HORIZONS)} horizons"
        f" = {len(DEPTHS)*len(HALFLIVES)*len(AGE_WINDOWS)*len(THRESHOLDS)*len(HORIZONS)} combos"
    )
    print(f"Output: {out_dir}/")

    rows, age_stats = _run_grid(cfg, args.start, args.end)

    # output.csv — sorted by IC descending
    ok_rows = sorted([r for r in rows if r.get("status") == "ok"], key=lambda r: -float(r["ic"]))
    err_rows = [r for r in rows if r.get("status") != "ok"]
    _write_csv(ok_rows + err_rows, out_dir / "output.csv", _CSV_FIELDS)
    print(f"Saved {out_dir}/output.csv  ({len(ok_rows)} ok, {len(err_rows)} errors)")

    # output_filtered.csv — multi-gate filter, ranked by IC × t_stat
    filtered = sorted(
        [r for r in ok_rows if _passes_gates(r)],
        key=lambda r: -(float(r["ic"]) * float(r["t_stat"])),
    )
    _write_csv(filtered, out_dir / "output_filtered.csv", _CSV_FIELDS)
    print(f"Saved {out_dir}/output_filtered.csv  ({len(filtered)} rows)")

    # age_stats.csv
    _write_csv(age_stats, out_dir / "age_stats.csv", _AGE_STAT_FIELDS)
    print(f"Saved {out_dir}/age_stats.csv  ({len(age_stats)} rows)")

    _print_summary(rows, args.top)
    print(f"\nScan ID: {scan_id}")
    print(f"Results: {out_dir}/")


if __name__ == "__main__":
    main()
