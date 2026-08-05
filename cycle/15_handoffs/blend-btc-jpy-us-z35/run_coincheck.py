#!/usr/bin/env python
"""Run the COINCHECK BTC_SPOT execution variant of the blend composite.

One command to run once COINCHECK BTC_SPOT MarketPrice is backfilled across the
train + holdout windows. It mirrors the GMO BTC_JPY run exactly — same signal
(BINANCE leader features), same US-session JST-hours filter (22,23,0,1,2), same
train/holdout split — so the two venues are directly comparable for the SAME edge.

    python cycle/15_handoffs/blend-btc-jpy-us-z35/run_coincheck.py \
        --start 2025-07-01 --end 2026-06-30

Prints holdout net per threshold for the in-scope exits: fixed_horizon (the pure
"does the signal predict the next X sec?" baseline), triple_barrier (v1), and
trailing_stop (v3). All three are anchored to the 300 s (X-sec) horizon. Episode
exit (v2) is deliberately NOT here — it exits when |z| falls back under threshold
(~2 s), measuring signal persistence rather than X-sec prediction (see aquila/v2.py).
See composite_coincheck.yaml for the data caveat.
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

import yaml

from kilimanjaro.driver.scan_runner import run_spec
from kilimanjaro.framework.schedule import ScheduleFilter

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[2]
JST_US_HOURS = [22, 23, 0, 1, 2]
THRESHOLDS = [3.5, 4.5]


def _us_hours_schedule() -> ScheduleFilter:
    blocked = [h for h in range(24) if h not in set(JST_US_HOURS)]
    return ScheduleFilter(
        block_weekday_hours=blocked,
        block_weekend_hours=blocked,
        block_gmo_maintenance=False,  # COINCHECK, not GMO
        name="jst_us",
    )


def _run(spec: dict, start: str, end: str, out: Path, label: str) -> None:
    res, out_dir, _ = run_spec(
        spec, start, end, results_root=out, split=None,
        scan_name=f"cc_{label}", schedule=_us_hours_schedule(),
    )
    print(f"\n  exit={label}   (US hours {JST_US_HOURS})")
    print(f"  {'thr':>4} {'ho_n':>6} {'ho_avg_net':>11} {'ho_hit':>7} "
          f"{'ho_total_net':>13} {'tr_avg_net':>11}")
    for r in sorted(res["rows"], key=lambda r: r.get("threshold", 0)):
        print(f"  {r.get('threshold'):>4} {r.get('holdout_n', 0):>6} "
              f"{r.get('holdout_avg_net', 0):>11.3f} {r.get('holdout_hit_rate', 0):>7.3f} "
              f"{r.get('holdout_total_net_bps', 0):>13.1f} {r.get('train_avg_net', 0):>11.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2025-07-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument(
        "--out", default=str(_ROOT / "cycle" / "13_results" / "blend" / "coincheck_us"),
    )
    args = ap.parse_args()

    base = yaml.safe_load((_HERE / "composite_coincheck.yaml").read_text())
    out = Path(args.out)

    fh = copy.deepcopy(base)
    fh["return_calc"] = "fixed_horizon"
    fh["horizons"] = [300]
    fh["thresholds"] = THRESHOLDS
    _run(fh, args.start, args.end, out, "fixed_horizon")

    tb = copy.deepcopy(base)  # v1
    tb["return_calc"] = "triple_barrier"
    tb["barriers"] = [10.0, 20.0]
    tb["time_caps"] = [300]
    tb["thresholds"] = THRESHOLDS
    _run(tb, args.start, args.end, out, "triple_barrier")

    ts = copy.deepcopy(base)  # v3
    ts["return_calc"] = "trailing_stop"
    ts["barriers"] = [10.0, 20.0]
    ts["time_caps"] = [300]
    ts["thresholds"] = THRESHOLDS
    _run(ts, args.start, args.end, out, "trailing_stop")


if __name__ == "__main__":
    main()
