"""cli.py return-eval — stage 15: does the candidate PAY?

Runs a spec's formula through the calibrated trading mechanism (its return_calc:
v3 trailing_stop / v1 triple_barrier, fill_delay=1, US session, rolling-DD gate = the
aquila config) over a threshold sweep, and prints standardized per-row trade economics:
n_trades, total_bps, avg_bps (GROSS, spread paid) + net_total/net_avg (minus the 1bp
latency cost — the verdict) + sharpe + avg_hold. Writes cycle/15_return_eval/<spec>/eval.csv.

    python src/cli.py return-eval --spec cycle/14_blend_corr/BTC_COINCHECK_BTC_SPOT_0.002/top_blend.yaml \
        --start 2025-08-01 --end 2026-06-30 --thresholds 2,2.5,3,3.5,4,4.5,5

See kilimanjaro.driver.return_eval.evaluate_spec and doc/research-pipeline.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from kilimanjaro.driver.return_eval import evaluate_spec

_RESULTS = Path("cycle/15_return_eval")


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("return-eval",
                       help="Stage 15: run a spec as the calibrated strategy; net trade economics")
    p.add_argument("--spec", required=True, help="candidate spec YAML (e.g. a stage-14 top_blend.yaml)")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--thresholds", default=None,
                   help="comma list to sweep (default: the spec's own thresholds)")
    p.add_argument("--cost-bps", type=float, default=1.0,
                   help="round-trip latency cost in bps (default 1.0, the calibrated value)")
    p.add_argument("--all-hours", action="store_true",
                   help="trade every hour (default: US session, matching the calibrated strategy)")
    p.add_argument("--no-dd-gate", action="store_true", help="drop the rolling-drawdown gate")
    p.add_argument("--out", default=None, help="output dir (default cycle/15_return_eval/<spec>)")
    p.set_defaults(func=_run)


def _run(args) -> int:
    spec = yaml.safe_load(Path(args.spec).read_text())
    thresholds = [float(x) for x in args.thresholds.split(",")] if args.thresholds else None
    jst = tuple(range(24)) if args.all_hours else (22, 23, 0, 1, 2)
    dd = float("inf") if args.no_dd_gate else 200.0

    t = spec["target"]
    rc = spec.get("return_calc", "trailing_stop")
    print(f"return-eval: {Path(args.spec).stem} | {t['exchange']}:{t['symbol']} | {rc} | "
          f"{'all-hours' if args.all_hours else 'US-session'}"
          f"{'' if not args.no_dd_gate else ' | no-dd-gate'} | cost {args.cost_bps}bp | "
          f"{args.start}..{args.end}")
    df = evaluate_spec(spec, args.start, args.end, thresholds,
                       cost_bps=args.cost_bps, jst_hours=jst, dd_threshold_bps=dd)

    print(f"\n  {'thr':>4} {'nTrades':>8} {'totalBps':>9} {'avgBps':>7} | "
          f"{'netTotal':>9} {'netAvg':>7} {'sharpe':>7} {'holdS':>7}")
    for _, r in df.iterrows():
        print(f"  {r.threshold:>4} {int(r.n_trades):>8} {r.total_bps:>+9.1f} {r.avg_bps:>+7.3f} | "
              f"{r.net_total_bps:>+9.1f} {r.net_avg_bps:>+7.3f} {r.sharpe:>+7.2f} {int(r.avg_hold_s):>7}")
    pos = df[df.net_total_bps > 0]
    print(f"\n  net-positive thresholds: "
          + (", ".join(f"{r.threshold}({int(r.n_trades)} tr, {r.net_avg_bps:+.2f}/t)"
                       for _, r in pos.iterrows()) if len(pos) else "NONE"))

    out = Path(args.out) if args.out else _RESULTS / Path(args.spec).stem
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "eval.csv", index=False)
    print(f"  wrote {out}/eval.csv")
    return 0
