"""Fixed-horizon OBI precision scanner — thin wrapper over the framework.

Public API (unchanged):
    load_obi_series(...)  → pd.DataFrame with [ts, obi]
    scan(...)             → report dict
    print_summary(report)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from kilimanjaro.data.obi_loader import load_obi_series  # re-export
from kilimanjaro.framework.protocol import MarketConfig
from kilimanjaro.framework.runner import run_grid
from kilimanjaro.framework.schedule import ScheduleFilter
from kilimanjaro.return_calc.fixed_horizon import FixedHorizonReturn
from kilimanjaro.signal.obi_age import OBIAgeSignal

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_DEFAULT_COSTS_BPS = 1.0

__all__ = ["load_obi_series", "scan", "print_summary"]


def scan(
    signal_exchange: str,
    signal_symbol: str,
    target_exchange: str,
    target_symbol: str,
    start_date: str,
    end_date: str,
    depth: int = 5,
    threshold: float = 0.3,
    horizon_seconds: int = 60,
    obi_halflife: int | None = 60,
    min_age_seconds: int | None = None,
    max_age_seconds: int | None = None,
    execution_size: str = "0.3",
    costs_bps: float = _DEFAULT_COSTS_BPS,
    block_weekday_hours: list[int] | None = None,
    block_weekend_hours: list[int] | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
    _obi_series: pd.DataFrame | None = None,
) -> dict:
    """Run a fixed-horizon OBI scan.

    Parameters mirror the original API. Pass _obi_series to skip parquet reads
    when calling from a grid sweep that already loaded the series.
    """
    sig = OBIAgeSignal(
        signal_exchange=signal_exchange,
        signal_symbol=signal_symbol,
        depth=depth,
        halflife=obi_halflife,
        threshold=threshold,
        min_age=min_age_seconds,
        max_age=max_age_seconds,
    )
    rc = FixedHorizonReturn(horizon_seconds=horizon_seconds)
    target = MarketConfig(target_exchange, target_symbol, execution_size)

    schedule = ScheduleFilter(
        block_weekday_hours=block_weekday_hours or [],
        block_weekend_hours=block_weekend_hours or [],
        block_gmo_maintenance=(target_exchange.upper() == "GMO"),
    )

    # Use pre-loaded series if provided (grid scan optimisation)
    if _obi_series is not None:
        signals = sig.generate(_obi_series)
    else:
        data = sig.load_data(start_date, end_date, data_root)
        signals = sig.generate(data)

    if signals.empty:
        raise ValueError(
            f"no OBIFeature data for {signal_exchange}/{signal_symbol}"
            f" in {start_date}..{end_date} with depth={depth}"
            f" halflife={obi_halflife} threshold={threshold}"
        )

    trades = rc.compute(signals, target, start_date, end_date, data_root)
    trades, schedule_counts = schedule.apply(trades)

    n_days = max((pd.to_datetime(end_date) - pd.to_datetime(start_date)).days, 1)
    n_signals = len(trades)
    valid = trades.dropna(subset=["return_bps"])

    from kilimanjaro.framework.stats import compute_stats
    fh = compute_stats(valid["return_bps"], costs_bps, signal_strength=valid["strength"])

    # Rename framework keys to legacy output names for backward compat
    fh_out = {
        "n": fh["n"],
        "avg_signed_return_bps": fh["avg_gross"],
        "avg_net_return_bps": fh["avg_net"],
        "hit_rate": fh["hit_rate"],
        "hit_rate_net": fh["hit_rate_net"],
        "std_bps": fh["std_bps"],
        "sharpe_per_trade": fh["sharpe_per_trade"],
        "t_stat": fh["t_stat"],
        "p_value_1sided": fh["p_value_1sided"],
        "skewness": fh["skewness"],
        "ic": fh["ic"],
        "total_net_bps": fh["total_net_bps"],
    }

    valid = valid.copy()
    valid["month"] = pd.to_datetime(valid["ts"], unit="s", utc=True).dt.strftime("%Y-%m")
    by_month = []
    for month, g in valid.groupby("month"):
        s = compute_stats(g["return_bps"], costs_bps, signal_strength=g["strength"])
        by_month.append({"month": month, "n": s["n"],
                         "avg_gross": s["avg_gross"], "avg_net": s["avg_net"],
                         "hit_rate": s["hit_rate"], "ic": s["ic"]})

    by_hour = []
    for hour, g in valid.groupby("jst_hour"):
        s = compute_stats(g["return_bps"], costs_bps, signal_strength=g["strength"])
        by_hour.append({"jst_hour": int(hour), "n": s["n"],
                        "avg_gross": s["avg_gross"], "avg_net": s["avg_net"],
                        "hit_rate": s["hit_rate"], "ic": s["ic"]})

    return {
        "signal_style": "precision",
        "signal_exchange": signal_exchange,
        "signal_symbol": signal_symbol,
        "target_exchange": target_exchange,
        "target_symbol": target_symbol,
        "start_date": start_date,
        "end_date": end_date,
        "depth": depth,
        "signal_col": sig.generate.__self__.__class__.__name__ if False else (
            f"vol_imb_k{depth}" if obi_halflife is None else f"ema_k{depth}_{obi_halflife}s"
        ),
        "obi_halflife": obi_halflife,
        "min_age_seconds": min_age_seconds,
        "max_age_seconds": max_age_seconds,
        "threshold": threshold,
        "horizon_seconds": horizon_seconds,
        "execution_size": execution_size,
        "costs_bps": costs_bps,
        "n_signals": n_signals,
        "signals_per_day": round(n_signals / n_days, 1),
        "risk_gates": {
            "block_weekday_hours": block_weekday_hours or [],
            "block_weekend_hours": block_weekend_hours or [],
            **schedule_counts,
        },
        "fixed_horizon": fh_out,
        "by_month": by_month,
        "by_jst_hour": by_hour,
    }


def print_summary(report: dict) -> None:
    costs = report["costs_bps"]
    fh = report["fixed_horizon"]
    rg = report["risk_gates"]
    print(f"\n{'=' * 60}")
    print(
        f"HORIZON SCAN: {report['signal_exchange']}/{report['signal_symbol']}"
        f" → {report['target_exchange']}/{report['target_symbol']}"
    )
    print(
        f"Window: {report['start_date']} .. {report['end_date']}"
        f"  signal={report['signal_col']}  threshold={report['threshold']}"
        f"  H={report['horizon_seconds']}s"
    )
    if rg.get("block_weekday_hours") or rg.get("block_weekend_hours"):
        print(f"Gates: block_weekday={rg['block_weekday_hours']}  block_weekend={rg['block_weekend_hours']}")
    print(f"N signals: {report['n_signals']}  ({report['signals_per_day']:.1f}/day)")
    print(f"Round-trip cost: {costs} bps")

    print("\n--- Fixed-horizon returns ---")
    print(f"  N with valid exit:  {fh['n']}")
    print(f"  Avg gross:          {fh['avg_signed_return_bps']:+.3f} bps")
    print(f"  Avg net:            {fh['avg_net_return_bps']:+.3f} bps  (after {costs} bps)")
    print(f"  Hit rate:           {fh['hit_rate']:.1%} gross  /  {fh['hit_rate_net']:.1%} net")
    print(f"  Std:                {fh['std_bps']:.3f} bps")
    print(f"  Sharpe/trade:       {fh['sharpe_per_trade']:+.4f}")
    print(f"  t-stat / p-value:   {fh['t_stat']:+.2f}  /  {fh['p_value_1sided']:.5f}")
    print(f"  IC (OBI↔return):    {fh['ic']:+.4f}")
    print(f"  Skewness:           {fh['skewness']:+.3f}")
    print(f"  Total net:          {fh['total_net_bps']:+.0f} bps")

    print(f"\n--- Monthly (net of {costs} bps) ---")
    print(f"  {'Month':>8}  {'N':>7}  {'Avg gross':>10}  {'Avg net':>10}  {'Hit%':>7}  {'IC':>7}")
    for m in report["by_month"]:
        print(
            f"  {m['month']:>8}  {m['n']:>7}"
            f"  {m['avg_gross']:>+9.3f}  {m['avg_net']:>+9.3f}"
            f"  {m['hit_rate']:>6.1%}  {m['ic']:>+6.4f}"
        )
    print()
