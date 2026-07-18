#!/usr/bin/env python3
"""Grid scan for the fixed-horizon OBI precision scanner.

Sweeps (depth × obi_halflife × age_window × threshold × horizon) through
kilimanjaro.framework.run_grid_split.

Train/holdout split — always on
-------------------------------
Same discipline as tools/grid_scan.py: every combo runs on a train window
AND a holdout window (default split: last third of the range; override with
--split), and selection is by rank stability (top decile of both windows by
the rank metric, positive avg_net in both), not by in-sample p-value.

Output folder
-------------
results/grid_scan/horizon/{scan_id}/
    params.json        scan configuration (grid, windows, ranking)
    output.csv         all combos: params + train_* + holdout_* + rank pcts
    output_stable.csv  rank-stable combos only (the candidates)
    age_stats.csv      age distribution per (depth, halflife, threshold), train window
    age_hist/          raw age histogram CSVs, train window

Usage
-----
    python tools/grid_scan_horizon.py --market eth --start 2026-01-01 --end 2026-06-30
    python tools/grid_scan_horizon.py --market eth --start 2026-01-01 --end 2026-06-30 --split 2026-05-01
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.framework import (
    MarketConfig,
    ScheduleFilter,
    default_split,
    run_grid_split,
    write_csv,
)
from kilimanjaro.data.feature_frame import obi_column, parse_obi_column
from kilimanjaro.op.age import threshold_age
from kilimanjaro.return_calc.fixed_horizon import FixedHorizonReturn
from kilimanjaro.signal import ExpressionSignal

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
        "hypothesis_id":    "hyp-20260713-001",  # OBI-age ETH spec
    },
    "eth_bitbank": {
        "signal_exchange":  "BITBANK",
        "signal_symbol":    "ETH_SPOT",
        "target_exchange":  "GMO",
        "target_symbol":    "ETH_JPY",
        "execution_size":   "0.3",
        "hypothesis_id":    None,  # no spec yet — pass --hypothesis-id
    },
    "xrp": {
        "signal_exchange":  "BINANCE",
        "signal_symbol":    "XRP_USDT",
        "target_exchange":  "GMO",
        "target_symbol":    "XRP_JPY",
        "execution_size":   "150",
        "hypothesis_id":    None,  # no spec yet — pass --hypothesis-id
    },
    "btc": {
        "signal_exchange":  "BINANCE",
        "signal_symbol":    "BTC_USDT",
        "target_exchange":  "GMO",
        "target_symbol":    "BTC_JPY",
        "execution_size":   "0.01",
        "hypothesis_id":    "hyp-20260618-001",  # raw-OBI BTC spec (this grid is its variant sweep)
    },
}

_CSV_FIELDS = [
    "depth", "halflife", "min_age", "max_age", "threshold", "horizon",
    "train_n", "train_avg_gross", "train_avg_net", "train_hit_rate", "train_ic",
    "train_t_stat", "train_p_value_bootstrap", "train_total_net_bps", "train_rank_pct",
    "holdout_n", "holdout_avg_gross", "holdout_avg_net", "holdout_hit_rate", "holdout_ic",
    "holdout_t_stat", "holdout_p_value_bootstrap", "holdout_total_net_bps", "holdout_rank_pct",
    "train_pos_month_share", "holdout_pos_month_share", "train_plateau",
    "verdict", "stable", "n_combos_tested", "train_status", "holdout_status",
]

_AGE_STAT_FIELDS = [
    "depth", "halflife", "threshold",
    "n_episodes", "ep_per_day",
    "age_mean", "age_p25", "age_p50", "age_p75", "age_p90", "age_max",
]

_HL_LABEL = {None: "raw", 10: "10s", 30: "30s", 60: "60s", 120: "120s", 300: "300s"}


def _age_label(v: int | None) -> str:
    return "any" if v is None else f"{v}s"


def _write_age_hist(obi_series, depth: int, halflife: int, out_dir: Path) -> None:
    """Write one age histogram CSV per threshold for a given (depth, halflife).

    Each file: age_hist/{depth}_{halflife}_{threshold}.csv
    One column 'age', one row per second where |obi| > threshold.
    """
    hist_dir = out_dir / "age_hist"
    hist_dir.mkdir(parents=True, exist_ok=True)
    hl_label = _HL_LABEL[halflife]
    for thr in THRESHOLDS:
        ages = threshold_age(obi_series["obi"], thr)
        active = ages[ages >= 0].reset_index(drop=True)
        if len(active) == 0:
            continue
        fname = hist_dir / f"{depth}_{hl_label}_{thr}.csv"
        active.to_csv(fname, index=False, header=["age"])


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


def _label_row(r: dict) -> dict:
    r = dict(r)
    # ExpressionSignal rows carry the OBI column in `expr` (its label); recover
    # depth/halflife from it so the output keeps its (depth, halflife) columns.
    if "depth" not in r and r.get("expr"):
        try:
            r["depth"], r["halflife"] = parse_obi_column(r["expr"])
        except ValueError:
            pass
    r["halflife"] = _HL_LABEL.get(r.get("halflife"), str(r.get("halflife")))
    r["min_age"] = _age_label(r.get("min_age"))
    r["max_age"] = _age_label(r.get("max_age"))
    for k, v in r.items():
        if isinstance(v, float):
            r[k] = round(v, 5)
    return r


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--market", required=True, choices=list(MARKET_CONFIGS))
    ap.add_argument("--start",  required=True, help="Start date YYYY-MM-DD")
    ap.add_argument("--end",    required=True, help="End date YYYY-MM-DD")
    ap.add_argument(
        "--split",
        default=None,
        help="Holdout split date YYYY-MM-DD (default: last third of the range)",
    )
    ap.add_argument("--rank-metric", default="t_stat")
    ap.add_argument("--top-quantile", type=float, default=0.10)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--out", default=None, help="Output folder")
    ap.add_argument(
        "--hypothesis-id",
        default=None,
        help="Hypothesis this grid belongs to (default: per-market from"
        " MARKET_CONFIGS; markets without a spec require this or --no-ledger)",
    )
    ap.add_argument(
        "--no-ledger",
        action="store_true",
        help="Skip the ledger event (exploratory runs only)",
    )
    args = ap.parse_args()

    cfg = MARKET_CONFIGS[args.market]
    hypothesis_id = args.hypothesis_id or cfg["hypothesis_id"]
    if hypothesis_id is None and not args.no_ledger:
        ap.error(
            f"market '{args.market}' has no default hypothesis spec —"
            " pass --hypothesis-id (create a spec first) or --no-ledger"
        )
    split = args.split or default_split(args.start, args.end)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    scan_id = f"{args.market.upper()}_{args.start}_{args.end}_{ts}"
    out_dir = Path(args.out) if args.out else Path(f"results/grid_scan/horizon/{scan_id}")
    out_dir.mkdir(parents=True, exist_ok=True)

    target = MarketConfig(cfg["target_exchange"], cfg["target_symbol"], cfg["execution_size"])
    schedule = ScheduleFilter(
        block_gmo_maintenance=(cfg["target_exchange"].upper() == "GMO"),
    )

    # OBI-age is just an ExpressionSignal over the OBI column: the whole scan
    # now runs through the same expression machinery. (depth, halflife) selects
    # the column (obi_k{d} / obi_ema_k{d}_{hl}s); the label carries them back
    # into the output rows via parse_obi_column.
    sig_market = {
        "a": {
            "exchange": cfg["signal_exchange"],
            "symbol": cfg["signal_symbol"],
            "execution_size": None,
        }
    }
    combos = [
        (
            ExpressionSignal.create(
                markets=sig_market,
                expr=obi_column(depth, halflife, "a"),
                threshold=threshold,
                min_age=min_age,
                max_age=max_age,
                label=obi_column(depth, halflife),  # base name, parseable
            ),
            FixedHorizonReturn(horizon_seconds=horizon),
        )
        for depth in DEPTHS
        for halflife in HALFLIVES
        for (min_age, max_age) in AGE_WINDOWS
        for threshold in THRESHOLDS
        for horizon in HORIZONS
    ]

    train_days = max(
        (datetime.date.fromisoformat(split) - datetime.date.fromisoformat(args.start)).days, 1
    )
    age_stats: list[dict] = []

    def on_data_loaded(cache_key: tuple, data) -> None:
        # ExpressionSignal cache_key = (markets_key, columns); one OBI column here.
        _markets_key, columns = cache_key
        col = columns[0]
        depth, halflife = parse_obi_column(col)
        print(f"  Loaded OBI series: depth={depth} halflife={_HL_LABEL[halflife]}", flush=True)
        obi_df = data[["ts", col]].rename(columns={col: "obi"})
        _write_age_hist(obi_df, depth, halflife, out_dir)
        for stat in _compute_age_stats(obi_df, train_days):
            age_stats.append({"depth": depth, "halflife": _HL_LABEL[halflife], **stat})

    done = {"train": 0, "holdout": 0}

    def on_result(row: dict) -> None:
        w = row["window"]
        done[w] += 1
        if done[w] % 100 == 0 or row.get("status") not in ("ok", "no_signals"):
            print(f"[{w:7s} {done[w]:5d}/{len(combos)}] status={row.get('status')}", flush=True)

    print(
        f"Horizon grid scan: {cfg['signal_exchange']}/{cfg['signal_symbol']}"
        f" → {cfg['target_exchange']}/{cfg['target_symbol']}"
    )
    print(f"Split: train {args.start} .. <{split}  |  holdout {split} .. {args.end}")
    print(f"Grid: {len(combos)} combos")
    print(f"Output: {out_dir}/")

    res = run_grid_split(
        combos, target, args.start, split, args.end,
        schedule=schedule,
        rank_metric=args.rank_metric,
        top_quantile=args.top_quantile,
        min_n=args.min_n,
        on_data_loaded=on_data_loaded,
        on_result=on_result,
    )

    params = {
        "scan_id": scan_id,
        "market": args.market,
        "start": args.start,
        "end": args.end,
        "windows": res["windows"],
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
        **res["params"],
    }
    (out_dir / "params.json").write_text(json.dumps(params, indent=2))

    rows = [_label_row(r) for r in res["rows"]]
    rows.sort(key=lambda r: -(r.get("holdout_rank_pct") or 0))
    stable = [r for r in rows if r["stable"]]

    write_csv(rows, out_dir / "output.csv", _CSV_FIELDS)
    write_csv(stable, out_dir / "output_stable.csv", _CSV_FIELDS)
    write_csv(age_stats, out_dir / "age_stats.csv", _AGE_STAT_FIELDS)
    monthly = [_label_row(r) for r in res["monthly_rows"]]
    if monthly:
        write_csv(monthly, out_dir / "output_monthly.csv", list(monthly[0].keys()))
    print(f"\nSaved {out_dir}/output.csv  ({len(rows)} combos)")
    print(f"Saved {out_dir}/output_stable.csv  ({len(stable)} rank-stable candidates)")
    print(f"Saved {out_dir}/age_stats.csv  ({len(age_stats)} rows)")
    print(f"Saved {out_dir}/output_monthly.csv  ({len(monthly)} combo-months)")
    print(f"Verdicts: {res['verdict_counts']}")

    if stable:
        print(f"\nRank-stable candidates (top {args.top_quantile:.0%} of both windows):")
        hdr = (f"  {'d':>3} {'hl':>5} {'age':>12} {'thr':>5} {'H':>4}"
               f" {'tr_net':>8} {'tr_t':>6} {'ho_net':>8} {'ho_t':>6} {'ho_n':>6}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for r in stable[:30]:
            age = f"[{r['min_age']},{r['max_age']})"
            print(
                f"  {r['depth']:>3} {r['halflife']:>5} {age:>12} {r['threshold']:>5} {r['horizon']:>4}"
                f" {r['train_avg_net']:>+8.3f} {r['train_t_stat']:>+6.2f}"
                f" {r['holdout_avg_net']:>+8.3f} {r['holdout_t_stat']:>+6.2f}"
                f" {r['holdout_n']:>6}"
            )
    else:
        print("\nNo rank-stable candidates — nothing survived both windows.")

    if args.no_ledger:
        print("\n(--no-ledger: run NOT recorded in ledger/events.jsonl)")
    else:
        from grid_ledger import record_grid_scan
        event = record_grid_scan(
            hypothesis_id=hypothesis_id,
            actor_id="grid-scan-horizon",
            scan_id=scan_id,
            out_dir=out_dir,
            market=args.market,
            res=res,
        )
        print(f"\nLedger: {event['event_id']} scan_attached → {hypothesis_id}")
        print(f"  {event['details']['headline']}")

    print(f"\nScan ID: {scan_id}")
    print(f"Results: {out_dir}/")


if __name__ == "__main__":
    main()
