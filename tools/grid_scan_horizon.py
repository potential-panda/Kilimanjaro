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
HALFLIVES   = [10, 30, 60, 120, 300]          # EMA halflives; raw OBI excluded (level signal, not crossing)
THRESHOLDS  = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
HORIZONS    = [10, 30, 60, 120, 300, 600]
MAX_AGES    = [1, 5, 10, 30, None]            # None = no age filter (level signal)

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
    "depth", "halflife", "max_age", "threshold", "horizon",
    "n_signals", "avg_gross", "avg_net", "hit_rate",
    "ic", "t_stat", "p_value", "total_net_bps", "status",
]

_HL_LABEL  = {None: "raw", 10: "10s", 30: "30s", 60: "60s", 120: "120s", 300: "300s"}
_AGE_LABEL = {None: "any", 1: "1s", 5: "5s", 10: "10s", 30: "30s"}


def _run_grid(market_cfg: dict, start: str, end: str) -> list[dict]:
    total = len(DEPTHS) * len(HALFLIVES) * len(MAX_AGES) * len(THRESHOLDS) * len(HORIZONS)
    rows: list[dict] = []
    done = 0

    for depth in DEPTHS:
        for halflife in HALFLIVES:
            # Load OBI series once per (depth, halflife) — shared across all
            # threshold / max_age / horizon combos that follow.
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
                for max_age in MAX_AGES:
                    for threshold in THRESHOLDS:
                        for horizon in HORIZONS:
                            done += 1
                            rows.append({
                                "depth": depth, "halflife": _HL_LABEL[halflife],
                                "max_age": _AGE_LABEL[max_age],
                                "threshold": threshold, "horizon": horizon,
                                "status": f"ERROR: {e}",
                            })
                continue

            for max_age in MAX_AGES:
                for threshold in THRESHOLDS:
                    for horizon in HORIZONS:
                        done += 1
                        label = (
                            f"d{depth} hl={_HL_LABEL[halflife]}"
                            f" age<{_AGE_LABEL[max_age]} thr={threshold} H={horizon}s"
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
                                "max_age":       _AGE_LABEL[max_age],
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
                                "max_age": _AGE_LABEL[max_age],
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
        f"  {'d':>3}  {'hl':>5}  {'age<':>5}  {'thr':>5}  {'H':>4}"
        f"  {'n/day':>6}  {'gross':>7}  {'net':>7}  {'hit%':>6}  {'IC':>7}  {'t':>7}"
    )
    sep = "  " + "-" * (len(hdr) - 2)

    # Group by horizon for readability
    print(f"\n{'=' * 80}")
    print(f"  TOP {top_n} BY IC  (all horizon × halflife combinations)")
    print(f"{'=' * 80}")
    print(hdr)
    print(sep)
    candidates = sorted(ok, key=lambda r: -float(r["ic"]))
    for r in candidates[:top_n]:
        print(
            f"  {r['depth']:>3}  {r['halflife']:>5}  {r['max_age']:>5}  {r['threshold']:>5}  {r['horizon']:>4}"
            f"  {r['n_signals']:>6.0f}  {float(r['avg_gross']):>+7.3f}  {float(r['avg_net']):>+7.3f}"
            f"  {float(r['hit_rate']):>5.1%}  {float(r['ic']):>+7.4f}  {float(r['t_stat']):>+7.2f}"
        )

    print(f"\n{'=' * 80}")
    print(f"  TOP {top_n} BY AVG_GROSS  (where avg_gross > 0)")
    print(f"{'=' * 80}")
    print(hdr)
    print(sep)
    by_gross = sorted(
        [r for r in ok if float(r["avg_gross"]) > 0],
        key=lambda r: -float(r["avg_gross"]),
    )
    for r in by_gross[:top_n]:
        print(
            f"  {r['depth']:>3}  {r['halflife']:>5}  {r['max_age']:>5}  {r['threshold']:>5}  {r['horizon']:>4}"
            f"  {r['n_signals']:>6.0f}  {float(r['avg_gross']):>+7.3f}  {float(r['avg_net']):>+7.3f}"
            f"  {float(r['hit_rate']):>5.1%}  {float(r['ic']):>+7.4f}  {float(r['t_stat']):>+7.2f}"
        )

    # Profitable (avg_net > 0) summary
    profitable = [r for r in ok if float(r["avg_net"]) > 0]
    print(f"\n  Profitable combos (avg_net > 0): {len(profitable)} / {len(ok)}")
    if profitable:
        print(f"\n{'=' * 80}")
        print(f"  PROFITABLE COMBOS  (avg_net > 0, ranked by t_stat)")
        print(f"{'=' * 80}")
        print(hdr)
        print(sep)
        for r in sorted(profitable, key=lambda r: -float(r["t_stat"]))[:top_n]:
            print(
                f"  {r['depth']:>3}  {r['halflife']:>5}  {r['threshold']:>5}  {r['horizon']:>4}"
                f"  {r['n_signals']:>6.0f}  {float(r['avg_gross']):>+7.3f}  {float(r['avg_net']):>+7.3f}"
                f"  {float(r['hit_rate']):>5.1%}  {float(r['ic']):>+7.4f}  {float(r['t_stat']):>+7.2f}"
            )


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
        f" × {len(MAX_AGES)} max_ages × {len(THRESHOLDS)} thresholds × {len(HORIZONS)} horizons"
        f" = {len(DEPTHS)*len(HALFLIVES)*len(MAX_AGES)*len(THRESHOLDS)*len(HORIZONS)} combos"
    )

    rows = _run_grid(cfg, args.start, args.end)
    _save_csv(rows, out)
    _print_summary(rows, args.top)


if __name__ == "__main__":
    main()
