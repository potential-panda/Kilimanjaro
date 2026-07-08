#!/usr/bin/env python3
"""Grid scan for the fixed-horizon OBI precision scanner.

Sweeps (depth × obi_halflife × threshold × horizon) and reports IC, hit rate,
avg_gross, t_stat per combination. Analogous to grid_scan.py for the SAR scanner.

Usage
-----
    python tools/grid_scan_horizon.py --market eth --start 2026-05-01 --end 2026-06-30
    python tools/grid_scan_horizon.py --market eth --start 2026-05-01 --end 2026-06-30 --out results/my_obi.csv

Columns in CSV
--------------
depth         OBI depth level (1, 3, 5, 10)
halflife      EMA halflife in seconds (10, 30, 60, 120, 300); "raw" = no EMA
threshold     |OBI| threshold to qualify as signal
horizon       Forward return window in seconds
n_signals     Signal events per day (after schedule filters)
avg_gross     Mean signed return before costs (bps)
avg_net       avg_gross - costs_bps
hit_rate      Fraction of signals where signed return > 0
ic            Pearson correlation between OBI value and return_bps
t_stat        avg_net / (std_net / √n)
p_value       One-sided P(mean ≤ 0 | data)
total_net_bps n_signals * n_days * avg_net  — cumulative edge proxy
status        "ok" or "ERROR: ..."
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

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

_HL_LABEL  = {None: "raw", 10: "10s", 30: "30s", 60: "60s", 120: "120s", 300: "300s"}

def _age_label(v: int | None) -> str:
    return "any" if v is None else f"{v}s"


def _run_grid(market_cfg: dict, start: str, end: str) -> list[dict]:
    total = len(DEPTHS) * len(HALFLIVES) * len(AGE_WINDOWS) * len(THRESHOLDS) * len(HORIZONS)
    rows: list[dict] = []
    done = 0

    for depth in DEPTHS:
        for halflife in HALFLIVES:
            # Load OBI series once per (depth, halflife) — shared across all
            # age_window / threshold / horizon combos that follow.
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
                # Mark all combos for this (depth, halflife) as errors
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
                            row = {
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
                            }
                            rows.append(row)
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

    return rows


def _save_csv(rows: list[dict], path: str) -> None:
    ok     = [r for r in rows if r.get("status") == "ok"]
    errors = [r for r in rows if r.get("status") != "ok"]
    ok.sort(key=lambda r: -float(r["ic"]))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        w.writeheader()
        w.writerows(ok + errors)
    print(f"\nSaved {path}")


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
    if profitable:
        print(f"\n{'=' * 90}")
        print(f"  PROFITABLE COMBOS  (avg_net > 0, ranked by t_stat)")
        print(f"{'=' * 90}")
        print(hdr); print(sep)
        for r in sorted(profitable, key=lambda r: -float(r["t_stat"]))[:top_n]:
            print(_fmt(r))


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--market", required=True, choices=list(MARKET_CONFIGS))
    ap.add_argument("--start",  required=True, help="Start date YYYY-MM-DD")
    ap.add_argument("--end",    required=True, help="End date YYYY-MM-DD")
    ap.add_argument("--out",    default=None,  help="CSV output path")
    ap.add_argument("--top",    type=int, default=20, help="Top-N to print (default 20)")
    args = ap.parse_args()

    cfg = MARKET_CONFIGS[args.market]
    out = args.out or f"results/grid_scan_horizon_{args.market.upper()}_{args.start}_{args.end}.csv"

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

    rows = _run_grid(cfg, args.start, args.end)
    _save_csv(rows, out)
    _print_summary(rows, args.top)


if __name__ == "__main__":
    main()
