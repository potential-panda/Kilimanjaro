#!/usr/bin/env python3
"""Run a between-flip parameter grid scan for a given market (ETH / XRP / BTC).

Built on kilimanjaro.framework: every (box, step, schedule) combo becomes a
(SARFlipSignal, BetweenSignalReturn, ScheduleFilter) triple, run through
run_grid_split with the rolling-DD gate.

Train/holdout split — always on
-------------------------------
The grid runs on a train window AND a holdout window (default split: last
third of the range held out; override with --split). Selection is by RANK
STABILITY: a combo is "stable" only if it ranks in the top decile of BOTH
windows by t_stat with positive avg_net in both. p-values (including the
autocorrelation-robust bootstrap one) are reported as evidence, not used as
the filter — with hundreds of correlated combos, p<=0.05 alone passes ~5%
by luck (measured: 110/245 ETH combos passed in-sample, 0 survived holdout).

Output folder
-------------
results/grid_scan/flip/{scan_id}/
    params.json        scan configuration (grid, risk gate, windows, ranking)
    output.csv         all combos: params + train_* + holdout_* + rank pcts
    output_stable.csv  rank-stable combos only (the candidates)

Usage
-----
    python tools/grid_scan.py --market eth --start 2025-07-01 --end 2026-06-30
    python tools/grid_scan.py --market xrp --start 2025-07-01 --end 2026-06-30 --split 2026-03-01
    python tools/grid_scan.py --market eth --start 2026-01-01 --end 2026-06-30 --out results/grid_scan/flip/my_run
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
    RollingDrawdownGate,
    ScheduleFilter,
    default_split,
    run_grid_split,
    write_csv,
)
from kilimanjaro.framework.orion_schedule import OrionScheduleFilter
from kilimanjaro.return_calc.between_signal import BetweenSignalReturn
from kilimanjaro.scan.flip_sar import DEFAULT_BLOCK_JST_DATES
from kilimanjaro.signal.sar_flip import SARFlipSignal

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

_ALL_HOURS = list(range(24))
# US-session boundary (fixed across markets): weekday JST 22–05 = US market open
_US_NIGHT_HOURS = [h for h in range(24) if not 6 <= h <= 21]
_US_DAY_HOURS = list(range(6, 22))


def _schedule_versions(xrp: bool = False) -> list[str]:
    """The orion schedule versions this market sweeps.

    Schedules are orion's (framework/orion_schedule.py = single source of truth),
    so scan and Himalaya apply the identical rule. XRP's active/inactive windows
    start one hour later, which orion encodes as xrp_active / xrp_inactive.
    """
    active = "xrp_active" if xrp else "default_active"
    inactive = "xrp_inactive" if xrp else "default_inactive"
    return ["nofilter", active, inactive, "weekday", "weekend", "us_on", "us_off"]


# Per-market config: flip source, execution target, execution size, and schedules.
# XRP's active window starts one hour later (JST 07–21 vs 06–21 for ETH/BTC).
MARKET_CONFIGS: dict[str, dict] = {
    "eth": {
        "flip_exchange": "BINANCE",
        "flip_symbol": "ETH_USDT",
        "target_exchange": "GMO",
        "target_symbol": "ETH_JPY",
        "execution_size": "0.3",
        "schedule_versions": _schedule_versions(),
    },
    "xrp": {
        "flip_exchange": "BINANCE",
        "flip_symbol": "XRP_USDT",
        "target_exchange": "GMO",
        "target_symbol": "XRP_JPY",
        "execution_size": "150",
        "schedule_versions": _schedule_versions(xrp=True),
    },
    "btc": {
        "flip_exchange": "BINANCE",
        "flip_symbol": "BTC_USDT",
        "target_exchange": "GMO",
        "target_symbol": "BTC_JPY",
        "execution_size": "0.01",
        "schedule_versions": _schedule_versions(),
    },
}

_CSV_FIELDS = [
    "box",
    "step",
    "schedule",
    "train_n_trades",
    "train_avg_net",
    "train_total_net_bps",
    "train_t_stat",
    "train_p_value_bootstrap",
    "train_n_dd_blocked",
    "train_rank_pct",
    "train_martin_jpy",
    "train_calmar_jpy",
    "train_jpy_total",
    "train_jpy_max_dd",
    "holdout_n_trades",
    "holdout_avg_net",
    "holdout_total_net_bps",
    "holdout_t_stat",
    "holdout_p_value_bootstrap",
    "holdout_n_dd_blocked",
    "holdout_rank_pct",
    "holdout_martin_jpy",
    "holdout_calmar_jpy",
    "holdout_jpy_total",
    "holdout_jpy_max_dd",
    "train_pos_month_share",
    "holdout_pos_month_share",
    "train_plateau",
    "verdict",
    "stable",
    "n_combos_tested",
    "train_status",
    "holdout_status",
]


def _build_combos(market_cfg: dict) -> list[tuple]:
    combos = []
    for box in BOXES:
        for step in STEPS:
            sig = SARFlipSignal(
                flip_exchange=market_cfg["flip_exchange"],
                flip_symbol=market_cfg["flip_symbol"],
                box_size_rate=box,
                sar_step=step,
            )
            # Schedule = orion's, applied on the entry ts — same rule Himalaya
            # runs. Weekend/maintenance handling is inherited from orion's
            # version definitions; no parallel block lists here.
            for version in market_cfg["schedule_versions"]:
                sched = OrionScheduleFilter(version, has_exit_col="exit_ts")
                combos.append((sig, BetweenSignalReturn(), sched))
    return combos


def _round(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        r = dict(r)
        for k, v in r.items():
            if isinstance(v, float):
                r[k] = round(v, 5)
        out.append(r)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--market", required=True, choices=list(MARKET_CONFIGS))
    ap.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    ap.add_argument(
        "--split",
        default=None,
        help="Holdout split date YYYY-MM-DD (default: last third of the range)",
    )
    ap.add_argument("--size", default=None, help="Override execution_size")
    ap.add_argument("--dd-window-seconds", type=float, default=DD_WINDOW_SECONDS)
    ap.add_argument("--dd-threshold-bps", type=float, default=DD_THRESHOLD_BPS)
    ap.add_argument("--dd-cooldown-seconds", type=float, default=DD_COOLDOWN_SECONDS)
    ap.add_argument("--rank-metric", default="t_stat")
    ap.add_argument("--top-quantile", type=float, default=0.10)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--out", default=None, help="Output folder")
    ap.add_argument(
        "--hypothesis-id",
        default="hyp-20260703-002",
        help="Hypothesis this grid belongs to; the run is recorded against it"
        " in ledger/events.jsonl (default: the schedule-adaptive flip-scan"
        " spec, which covers the (box, step, schedule) grid on XRP/ETH/BTC)",
    )
    ap.add_argument(
        "--no-ledger",
        action="store_true",
        help="Skip the ledger event (exploratory runs only)",
    )
    args = ap.parse_args()

    market_cfg = dict(MARKET_CONFIGS[args.market])
    if args.size is not None:
        market_cfg["execution_size"] = args.size

    split = args.split or default_split(args.start, args.end)
    target = MarketConfig(
        market_cfg["target_exchange"],
        market_cfg["target_symbol"],
        market_cfg["execution_size"],
    )
    gate = RollingDrawdownGate(
        window_seconds=args.dd_window_seconds,
        threshold_bps=args.dd_threshold_bps,
        cooldown_seconds=args.dd_cooldown_seconds,
    )
    combos = _build_combos(market_cfg)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    scan_id = f"{args.market.upper()}_{args.start}_{args.end}_{ts}"
    out_dir = Path(args.out) if args.out else Path(f"results/grid_scan/flip/{scan_id}")

    print(
        f"Flip grid scan: {market_cfg['flip_exchange']}/{market_cfg['flip_symbol']}"
        f" → {target.exchange}/{target.symbol}  size={target.execution_size}"
    )
    print(f"Split: train {args.start} .. <{split}  |  holdout {split} .. {args.end}")
    print(f"Grid: {len(combos)} combos   gate: {gate.params}")
    print(f"Output: {out_dir}/")

    done = {"train": 0, "holdout": 0}

    def on_result(row: dict) -> None:
        w = row["window"]
        done[w] += 1
        if row.get("status") == "ok":
            print(
                f"[{w:7s} {done[w]:3d}/{len(combos)}] box={row['box']} step={row['step']}"
                f" sched={row['schedule']} → n={row['n_trades']}"
                f" net={row['avg_net']:+.2f}bps t={row['t_stat']:+.2f}",
                flush=True,
            )
        else:
            print(
                f"[{w:7s} {done[w]:3d}/{len(combos)}] box={row['box']} step={row['step']}"
                f" sched={row['schedule']} → {row['status']}",
                flush=True,
            )

    res = run_grid_split(
        combos,
        target,
        args.start,
        split,
        args.end,
        gates=[gate],
        rank_metric=args.rank_metric,
        top_quantile=args.top_quantile,
        min_n=args.min_n,
        on_result=on_result,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    params = {
        "scan_id": scan_id,
        "market": args.market,
        "start": args.start,
        "end": args.end,
        "windows": res["windows"],
        "flip_exchange": market_cfg["flip_exchange"],
        "flip_symbol": market_cfg["flip_symbol"],
        "target_exchange": target.exchange,
        "target_symbol": target.symbol,
        "execution_size": target.execution_size,
        "schedule_source": "orion.schedule (framework/orion_schedule)",
        "grid": {
            "boxes": BOXES,
            "steps": STEPS,
            "schedule_versions": market_cfg["schedule_versions"],
        },
        **res["params"],
    }
    (out_dir / "params.json").write_text(json.dumps(params, indent=2))

    rows = _round(res["rows"])
    rows.sort(key=lambda r: -(r.get("holdout_rank_pct") or 0))
    stable = [r for r in rows if r["stable"]]
    write_csv(rows, out_dir / "output.csv", _CSV_FIELDS)
    write_csv(stable, out_dir / "output_stable.csv", _CSV_FIELDS)
    monthly = res["monthly_rows"]
    if monthly:
        write_csv(monthly, out_dir / "output_monthly.csv", list(monthly[0].keys()))
    print(f"\nSaved {out_dir}/output.csv  ({len(rows)} combos)")
    print(f"Saved {out_dir}/output_stable.csv  ({len(stable)} rank-stable candidates)")
    print(f"Saved {out_dir}/output_monthly.csv  ({len(monthly)} combo-months)")
    print(f"Verdicts: {res['verdict_counts']}")

    if stable:
        print(
            f"\nRank-stable candidates (top {args.top_quantile:.0%} of both windows):"
        )
        hdr = (
            f"  {'box':>7} {'step':>5} {'sched':>9}"
            f" {'tr_net':>8} {'tr_t':>6} {'ho_net':>8} {'ho_t':>6} {'ho_n':>6}"
        )
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for r in stable:
            print(
                f"  {r['box']:>7} {r['step']:>5} {r['schedule']:>9}"
                f" {r['train_avg_net']:>+8.2f} {r['train_t_stat']:>+6.2f}"
                f" {r['holdout_avg_net']:>+8.2f} {r['holdout_t_stat']:>+6.2f}"
                f" {r['holdout_n_trades']:>6}"
            )
    else:
        print("\nNo rank-stable candidates — nothing survived both windows.")

    if args.no_ledger:
        print("\n(--no-ledger: run NOT recorded in ledger/events.jsonl)")
    else:
        from grid_ledger import record_grid_scan

        event = record_grid_scan(
            hypothesis_id=args.hypothesis_id,
            actor_id="grid-scan-flip",
            scan_id=scan_id,
            out_dir=out_dir,
            market=args.market,
            res=res,
        )
        print(f"\nLedger: {event['event_id']} scan_attached → {args.hypothesis_id}")
        print(f"  {event['details']['headline']}")

    print(f"\nScan ID: {scan_id}")


if __name__ == "__main__":
    main()
