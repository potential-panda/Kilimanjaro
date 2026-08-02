"""`cli.py learn` — learn a descriptive metric about a signal (not a scan).

A scan (`grid`) measures a signal's edge; `learn` measures a property of the
signal itself — over a window, with no return calc, target, split, or cost. The
metric is chosen with `--op`; today the only op is `age` (episode-length
distribution), the diagnostic that used to be bundled into `grid obi`.

    python src/cli.py learn --op age --spec my_grid.yaml --start ... --end ...

Adding an op later means a new function in driver/learn_metrics.py and a branch
here — `age` becomes one value of `--op`, not a special case.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from kilimanjaro.framework import write_csv

_RESULTS = Path("cycle/13_results/learn")

_OPS = ("age", "return_dist", "exit_mode")


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("learn", help="Learn a descriptive metric about a signal (not a scan)")
    p.add_argument("--op", default="age", choices=_OPS,
                   help="Which metric to learn: age (episode length), return_dist "
                        "(time distribution of directional returns), or exit_mode "
                        "(triple-barrier: barrier vs time-cap exit split)")
    p.add_argument("--spec", required=True, help="YAML spec (expressions + thresholds)")
    p.add_argument("--start", required=True, help="Window start YYYY-MM-DD")
    p.add_argument("--end", required=True, help="Window end YYYY-MM-DD")
    p.add_argument("--thresholds", default=None,
                   help="return_dist/exit_mode: comma z-thresholds (default: the spec's)")
    p.add_argument("--horizon", type=int, default=300,
                   help="return_dist: forward horizon seconds (default 300)")
    p.add_argument("--barriers", default=None,
                   help="exit_mode: comma barrier bps to sweep (default: the spec's)")
    p.add_argument("--time-caps", default=None,
                   help="exit_mode: comma time caps in seconds (default: the spec's)")
    p.add_argument("--jst-hours", default=None,
                   help="exit_mode: restrict to these JST hours (e.g. 22,23,0,1,2)")
    p.add_argument("--out", default=None, help="Output directory (default under "
                   "cycle/13_results/learn/<op>/)")
    p.set_defaults(func=_run_learn)


def _run_learn(args: argparse.Namespace) -> int:
    from kilimanjaro.driver import learn_metrics as lm

    spec = yaml.safe_load(Path(args.spec).read_text())
    stem = Path(args.spec).stem

    if args.op == "age":
        rows = lm.learn_age(spec, args.start, args.end)
        out_dir = Path(args.out) if args.out else (
            _RESULTS / "age" / f"{stem}_{args.start}_{args.end}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "age_stats.csv"
        write_csv(rows, out_path, lm.AGE_STAT_FIELDS)
        n_ep = sum(r["n_episodes"] for r in rows)
        print(f"learn age: {stem} — {len(rows)} (expr × threshold) rows, "
              f"{n_ep} episodes total")
        print(f"  -> {out_path}")
        return 0

    if args.op == "return_dist":
        thresholds = (
            [float(x) for x in args.thresholds.split(",")] if args.thresholds else None
        )
        rows = lm.learn_return_dist(
            spec, args.start, args.end, thresholds=thresholds, horizon=args.horizon
        )
        out_dir = Path(args.out) if args.out else (
            _RESULTS / "return_dist" / f"{stem}_{args.start}_{args.end}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "return_dist.csv"
        write_csv(rows, out_path, lm.RETURN_DIST_FIELDS)
        print(f"learn return_dist: {stem} — {len(rows)} (expr × threshold) rows "
              f"(mid-to-mid, no cost)")
        print(f"  {'expr':<18} {'thr':>4} {'n':>7} {'mean':>7} {'pos_day':>8} "
              f"{'top10%':>7} {'pos_mo':>7}")
        for r in rows:
            print(f"  {str(r['expr'])[:18]:<18} {r['threshold']:>4} {r['n_trades']:>7} "
                  f"{_fmt(r['mean_bps']):>7} {_fmt(r['pos_day_share']):>8} "
                  f"{_fmt(r['top10_day_share_pct']):>7} {_fmt(r['pos_month_share']):>7}")
        print(f"  -> {out_path}")
        return 0

    if args.op == "exit_mode":
        thresholds = (
            [float(x) for x in args.thresholds.split(",")] if args.thresholds else None
        )
        barriers = (
            [float(x) for x in args.barriers.split(",")] if args.barriers else None
        )
        time_caps = (
            [int(x) for x in args.time_caps.split(",")] if args.time_caps else None
        )
        jst_hours = (
            {int(h) for h in args.jst_hours.split(",")} if args.jst_hours else None
        )
        rows = lm.learn_exit_mode(
            spec, args.start, args.end, thresholds=thresholds,
            barriers=barriers, time_caps=time_caps, jst_hours=jst_hours,
        )
        out_dir = Path(args.out) if args.out else (
            _RESULTS / "exit_mode" / f"{stem}_{args.start}_{args.end}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "exit_mode.csv"
        write_csv(rows, out_path, lm.EXIT_MODE_FIELDS)
        hrs = f" | JST {sorted(jst_hours)}" if jst_hours else ""
        print(f"learn exit_mode: {stem}{hrs} — barrier vs time-cap exit split "
              f"+ risk-adjusted (martin/sharpe/DD on the per-trade net curve)")
        print(f"  {'thr':>4} {'barr':>5} {'n':>6} {'%barr':>6} {'%tp':>5} "
              f"{'hold_p50':>8} {'net_all':>7} {'total':>8} {'sharpe':>7} "
              f"{'martin':>7} {'calmar':>7} {'max_dd':>8}")
        for r in rows:
            print(f"  {r['threshold']:>4} {r['barrier_bps']:>5} {r['n_trades']:>6} "
                  f"{_fmt(r['pct_barrier']):>6} {_fmt(r['pct_tp']):>5} "
                  f"{_fmt(r['hold_p50_s']):>8} {_fmt(r['avg_net_all']):>7} "
                  f"{_fmt(r['total_net_bps']):>8} {_fmt(r['sharpe_daily']):>7} "
                  f"{_fmt(r['martin']):>7} {_fmt(r['calmar']):>7} "
                  f"{_fmt(r['max_dd_bps']):>8}")
        print(f"  -> {out_path}")
        return 0

    print(f"unknown op {args.op!r}")  # unreachable: argparse choices guards it
    return 2


def _fmt(v) -> str:
    return f"{v:.3f}" if isinstance(v, float) else ("—" if v is None else str(v))
