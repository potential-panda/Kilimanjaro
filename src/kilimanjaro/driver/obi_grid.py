"""OBI-age flavour: the (depth × halflife × age × threshold × horizon) grid.

OBI-age is just an ExpressionSignal over an OBI feature-frame column, so it runs
through the same machinery as every other scan; this module only supplies the
combo builder, the (depth, halflife) column bridge, and the age-distribution
diagnostics (age_hist/ + age_stats.csv). Moved here from tools/grid_scan_horizon.py.
"""

from __future__ import annotations

from pathlib import Path

from kilimanjaro.data.feature_frame import obi_column, parse_obi_column
from kilimanjaro.framework import write_csv
from kilimanjaro.op.age import threshold_age
from kilimanjaro.return_calc.fixed_horizon import FixedHorizonReturn
from kilimanjaro.signal import ExpressionSignal

DEPTHS = [1, 3, 5, 10]
HALFLIVES = [10, 30, 60, 120, 300]
THRESHOLDS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
HORIZONS = [10, 30, 60, 120, 300, 600]

# (min_age, max_age) windows in seconds; None = unbounded on that side.
AGE_WINDOWS: list[tuple[int | None, int | None]] = [
    (None, None), (0, 60), (0, 180), (10, 120), (10, 300),
    (30, 120), (30, 180), (30, 300), (60, 180), (60, 300),
]

MARKET_CONFIGS: dict[str, dict] = {
    "eth": {"signal_exchange": "BINANCE", "signal_symbol": "ETH_USDT",
            "target_exchange": "GMO", "target_symbol": "ETH_JPY", "execution_size": "0.3"},
    "eth_bitbank": {"signal_exchange": "BITBANK", "signal_symbol": "ETH_SPOT",
                    "target_exchange": "GMO", "target_symbol": "ETH_JPY", "execution_size": "0.3"},
    "xrp": {"signal_exchange": "BINANCE", "signal_symbol": "XRP_USDT",
            "target_exchange": "GMO", "target_symbol": "XRP_JPY", "execution_size": "150"},
    "btc": {"signal_exchange": "BINANCE", "signal_symbol": "BTC_USDT",
            "target_exchange": "GMO", "target_symbol": "BTC_JPY", "execution_size": "0.01"},
}

CSV_FIELDS = [
    "depth", "halflife", "min_age", "max_age", "threshold", "horizon",
    "train_n", "train_avg_gross", "train_avg_net", "train_hit_rate", "train_ic",
    "train_t_stat", "train_p_value_bootstrap", "train_total_net_bps", "train_rank_pct",
    "holdout_n", "holdout_avg_gross", "holdout_avg_net", "holdout_hit_rate", "holdout_ic",
    "holdout_t_stat", "holdout_p_value_bootstrap", "holdout_total_net_bps", "holdout_rank_pct",
    "train_pos_month_share", "holdout_pos_month_share", "train_plateau",
    "verdict", "stable", "n_combos_tested", "train_status", "holdout_status",
]

AGE_STAT_FIELDS = [
    "depth", "halflife", "threshold", "n_episodes", "ep_per_day",
    "age_mean", "age_p25", "age_p50", "age_p75", "age_p90", "age_max",
]

_HL_LABEL = {None: "raw", 10: "10s", 30: "30s", 60: "60s", 120: "120s", 300: "300s"}


def _age_label(v: int | None) -> str:
    return "any" if v is None else f"{v}s"


def build_combos(cfg: dict) -> list[tuple]:
    """(ExpressionSignal over the OBI column, FixedHorizonReturn) over the grid.

    (depth, halflife) selects the column (obi_k{d} / obi_ema_k{d}_{hl}s); the
    label carries them back into the output rows via parse_obi_column.
    """
    sig_market = {"a": {"exchange": cfg["signal_exchange"],
                        "symbol": cfg["signal_symbol"], "execution_size": None}}
    return [
        (
            ExpressionSignal.create(
                markets=sig_market, expr=obi_column(depth, halflife, "a"),
                threshold=threshold, min_age=min_age, max_age=max_age,
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


def label_row(r: dict) -> dict:
    """row_transform for run_grid_scan: recover depth/halflife from the OBI column
    in `expr` and label the age/halflife columns; round floats."""
    r = dict(r)
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


def make_age_hist_hook(out_dir: Path, train_days: int, age_stats: list[dict]):
    """Build an on_data_loaded hook that writes age_hist/ CSVs and appends
    per-(depth, halflife) age-distribution stats to `age_stats` (train window).
    """
    hist_dir = out_dir / "age_hist"

    def hook(cache_key: tuple, data) -> None:
        _markets_key, columns = cache_key  # ExpressionSignal cache_key
        col = columns[0]
        depth, halflife = parse_obi_column(col)
        obi = data[["ts", col]].rename(columns={col: "obi"})["obi"]
        hist_dir.mkdir(parents=True, exist_ok=True)
        hl_label = _HL_LABEL[halflife]
        for thr in THRESHOLDS:
            ages = threshold_age(obi, thr)
            active = ages[ages >= 0].reset_index(drop=True)
            if len(active) == 0:
                age_stats.append({"depth": depth, "halflife": hl_label, "threshold": thr,
                                  "n_episodes": 0, "ep_per_day": 0.0, "age_mean": None,
                                  "age_p25": None, "age_p50": None, "age_p75": None,
                                  "age_p90": None, "age_max": None})
                continue
            active.to_csv(hist_dir / f"{depth}_{hl_label}_{thr}.csv",
                          index=False, header=["age"])
            age_stats.append({
                "depth": depth, "halflife": hl_label, "threshold": thr,
                "n_episodes": int((ages == 0).sum()),
                "ep_per_day": round(int((ages == 0).sum()) / train_days, 2),
                "age_mean": round(float(active.mean()), 1),
                "age_p25": round(float(active.quantile(0.25)), 1),
                "age_p50": round(float(active.quantile(0.50)), 1),
                "age_p75": round(float(active.quantile(0.75)), 1),
                "age_p90": round(float(active.quantile(0.90)), 1),
                "age_max": int(active.max()),
            })

    return hook


def write_age_stats(out_dir: Path, age_stats: list[dict]) -> None:
    if age_stats:
        write_csv(age_stats, out_dir / "age_stats.csv", AGE_STAT_FIELDS)
