"""Flip-scan flavour: the (box × step × schedule) grid over range-bar SAR flips.

Combo builder + config for the flip grid; the run/write/ledger pipeline is the
shared `scan_runner.run_grid_scan`. Moved here from tools/grid_scan.py so the
whole scan lives under kilimanjaro/ and is driven from cli.py (`grid flip`).
"""

from __future__ import annotations

from kilimanjaro.framework.orion_schedule import OrionScheduleFilter
from kilimanjaro.return_calc.between_signal import BetweenSignalReturn
from kilimanjaro.signal.sar_flip import SARFlipSignal

# ── Grid parameters ──────────────────────────────────────────────────────────
BOXES = [0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005]
STEPS = [0.01, 0.02, 0.03, 0.04, 0.05]

# ── Rolling-DD risk gate defaults (override via CLI) ─────────────────────────
# Canonical values live in framework/gate.py — the same default every scan uses.

MARKET_CONFIGS: dict[str, dict] = {
    "eth": {
        "flip_exchange": "BINANCE",
        "flip_symbol": "ETH_USDT",
        "target_exchange": "GMO",
        "target_symbol": "ETH_JPY",
        "execution_size": "0.3",
    },
    "xrp": {
        "flip_exchange": "BINANCE",
        "flip_symbol": "XRP_USDT",
        "target_exchange": "GMO",
        "target_symbol": "XRP_JPY",
        "execution_size": "150",
    },
    "btc": {
        "flip_exchange": "BINANCE",
        "flip_symbol": "BTC_USDT",
        "target_exchange": "GMO",
        "target_symbol": "BTC_JPY",
        "execution_size": "0.01",
    },
}

CSV_FIELDS = [
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


def schedule_versions(xrp: bool = False) -> list[str]:
    """The orion schedule versions this market sweeps (framework/orion_schedule
    = single source of truth). XRP's active/inactive windows start one hour
    later, which orion encodes as xrp_active / xrp_inactive.
    """
    active = "xrp_active" if xrp else "default_active"
    inactive = "xrp_inactive" if xrp else "default_inactive"
    return ["nofilter", active, inactive, "weekday", "weekend", "us_on", "us_off"]


def build_combos(market_cfg: dict) -> list[tuple]:
    """(SARFlipSignal, BetweenSignalReturn, OrionScheduleFilter) over box × step ×
    schedule. Schedule is orion's, applied on the entry ts — same rule Himalaya
    runs; weekend/maintenance handling is inherited from the version definitions.
    """
    xrp = market_cfg["target_symbol"].startswith("XRP")
    combos = []
    for box in BOXES:
        for step in STEPS:
            sig = SARFlipSignal(
                flip_exchange=market_cfg["flip_exchange"],
                flip_symbol=market_cfg["flip_symbol"],
                box_size_rate=box,
                sar_step=step,
            )
            for version in schedule_versions(xrp):
                sched = OrionScheduleFilter(version, has_exit_col="exit_ts")
                combos.append((sig, BetweenSignalReturn(), sched))
    return combos
