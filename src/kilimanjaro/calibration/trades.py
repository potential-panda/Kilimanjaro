"""Trade-level calibration — the layer above the factor calibration.

Once the signal (composite_z) matches (see factors/), the next question is whether
the SCAN, fed that signal, fires the SAME orders and books the SAME return as
Himalaya. This module answers it with the same [match, fix] loop the factors use:

  offline (Kilimanjaro): run the promoted spec through the scan exactly as a grid run
                         would — ExpressionSignal -> TripleBarrierReturn(fill_delay=1)
                         -> US-session schedule -> rolling-drawdown gate. One row/trade
                         [entry_exec_ts, entry_price, exit_exec_ts, exit_price, return_bps].
  online  (Himalaya):    the ORDER stream the aquila backtest wrote to InfluxDB
                         (OPEN/CLOSE fills, EXEC_PRICE) paired into round-trips, plus
                         the PNL.return per close. One row per Himalaya round-trip.

The scan config MUST mirror the aquila run that produced the bucket (aquila config.py:
threshold 3.5, barrier 10, cap 300, jst_hours {22,23,0,1,2}, dd 200bps/6h/1h cooldown),
or the orders diverge for config reasons, not fidelity reasons. Those defaults live in
`ScanConfig` below.

Trades are sequential (one position at a time), so a single divergence desyncs the whole
tail — align on entry ts with a small window, then report the funnel:
  (1) counts (scan vs Himalaya)   (2) matched pairs   (3) side match
  (4) entry-ts / exit-ts exact    (5) entry/exit price match (within tol)
  (6) per-trade + total return (gross price move; scan vs Himalaya EXEC_PRICE)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")

# Aquila run config (config.py:default_params) — the scan must replicate it to match.
US_JST_HOURS = (22, 23, 0, 1, 2)


@dataclass(frozen=True)
class ScanConfig:
    """Everything the scan needs to reproduce the aquila backtest that filled `bucket`."""
    threshold: float = 3.5
    barrier_bps: float = 10.0
    time_cap_s: float = 300.0
    fill_delay_seconds: float = 1.0        # next-available-price fill (the certified model)
    jst_hours: tuple[int, ...] = US_JST_HOURS
    dd_window_s: float = 21600.0           # aquila dd_window_s (6h)
    dd_threshold_bps: float = 200.0        # aquila dd_threshold_bps
    dd_cooldown_s: float = 3600.0          # aquila dd_cooldown_s (1h)
    use_dd_gate: bool = True

    @classmethod
    def from_bucket(cls, bucket: str, **over) -> "ScanConfig":
        """Parse threshold_barrier_cap from a `..._<thr>_<bar>_<cap>` bucket name."""
        tail = bucket.rsplit("__", 1)[-1]
        parts = tail.split("_")
        kw: dict = {}
        try:
            kw = {"threshold": float(parts[0]), "barrier_bps": float(parts[1]),
                  "time_cap_s": float(parts[2])}
        except (IndexError, ValueError):
            pass
        kw.update(over)
        return cls(**kw)


@dataclass
class TradeCalibResult:
    n_scan: int
    n_hima: int
    n_matched: int
    side_match: int
    entry_ts_exact: int
    exit_ts_exact: int
    entry_px_match: int
    exit_px_match: int
    scan_total_bps: float
    hima_total_bps: float
    scan_mean_bps: float
    hima_mean_bps: float
    ret_corr: float
    ret_match: int
    px_tol: float
    ts_tol: float
    ret_tol: float
    divergences: list = field(default_factory=list)

    def _pct(self, n: int) -> float:
        return 100.0 * n / self.n_matched if self.n_matched else 0.0

    def summary(self) -> str:
        m = self.n_matched
        return (
            f"trades: scan {self.n_scan}  Himalaya {self.n_hima}  matched {m}\n"
            f"  (1) side match      {self.side_match}/{m} ({self._pct(self.side_match):.1f}%)\n"
            f"  (2) entry ts exact  {self.entry_ts_exact}/{m} ({self._pct(self.entry_ts_exact):.1f}%)   "
            f"exit ts exact {self.exit_ts_exact}/{m} ({self._pct(self.exit_ts_exact):.1f}%)\n"
            f"  (3) entry px match  {self.entry_px_match}/{m} ({self._pct(self.entry_px_match):.1f}%)   "
            f"exit px match {self.exit_px_match}/{m} ({self._pct(self.exit_px_match):.1f}%)  [tol {self.px_tol}]\n"
            f"  (4) return: per-trade match {self.ret_match}/{m} ({self._pct(self.ret_match):.1f}%)  "
            f"corr {self.ret_corr:.4f}  [tol {self.ret_tol} bps]\n"
            f"      total bps  scan {self.scan_total_bps:+.1f}   Himalaya {self.hima_total_bps:+.1f}   "
            f"diff {self.scan_total_bps - self.hima_total_bps:+.1f}\n"
            f"      per-trade  scan {self.scan_mean_bps:+.2f}   Himalaya {self.hima_mean_bps:+.2f}"
        )


class TradeCalib:
    """Runs the scan, pulls the Himalaya orders, aligns them, reports the funnel."""

    def __init__(self, spec: dict, markets: dict, cfg: ScanConfig):
        self.spec = spec
        self.markets = markets
        self.cfg = cfg

    # ---- offline: the scan's trades -------------------------------------------
    def offline_trades(self, start: str, end: str,
                       data_root: Path = _DEFAULT_DATA_ROOT) -> pd.DataFrame:
        from kilimanjaro.driver.scan_runner import gates_for_spec
        from kilimanjaro.framework.protocol import MarketConfig
        from kilimanjaro.framework.schedule import ScheduleFilter
        from kilimanjaro.calibration.base import _shift_date, _WARMUP_DAYS
        from kilimanjaro.return_calc.triple_barrier import TripleBarrierReturn
        from kilimanjaro.signal.expression import ExpressionSignal

        e = self.spec["expressions"][0]
        sig = ExpressionSignal.create(
            markets=self.markets, expr=e["expr"], threshold=self.cfg.threshold,
            min_age=None, max_age=None,
            invert=e.get("invert", [False])[0], label=e.get("label"))
        # Load from start-warmup so the composite's rolling windows converge to the same
        # state aquila has at the window start (else the first ~hour under-warms); the
        # extra early trades are dropped by the Himalaya-window restriction in calibrate().
        load_start = _shift_date(start, _WARMUP_DAYS)
        data = sig.load_data(load_start, end, data_root)
        t = self.spec["target"]
        tgt = MarketConfig(t["exchange"], t["symbol"], t["execution_size"])
        trades = TripleBarrierReturn(
            self.cfg.barrier_bps, self.cfg.time_cap_s,
            fill_delay_seconds=self.cfg.fill_delay_seconds,
        ).compute(sig.generate(data), tgt, load_start, end, data_root)

        # US-session schedule: allow entries only in jst_hours (any day), no maint block.
        blocked = [h for h in range(24) if h not in self.cfg.jst_hours]
        sched = ScheduleFilter(block_weekday_hours=blocked, block_weekend_hours=blocked,
                               block_gmo_maintenance=False, name="us")
        trades, _ = sched.apply(trades)
        if self.cfg.use_dd_gate:
            from kilimanjaro.framework.gate import RollingDrawdownGate
            g = RollingDrawdownGate(window_seconds=self.cfg.dd_window_s,
                                    threshold_bps=self.cfg.dd_threshold_bps,
                                    cooldown_seconds=self.cfg.dd_cooldown_s)
            trades, _ = g.apply(trades)
        trades = trades.dropna(subset=["return_bps"]).sort_values("entry_exec_ts")
        return trades.reset_index(drop=True)

    # ---- online: Himalaya's orders --------------------------------------------
    def online_trades(self, bucket: str, start: str, end: str) -> pd.DataFrame:
        import warnings

        from influxdb_client.client.warnings import MissingPivotFunction
        warnings.simplefilter("ignore", MissingPivotFunction)
        from kilimanjaro.framework.verify import (
            influx_client, influx_range_jst, query_df,
        )
        rfc_start, rfc_stop = influx_range_jst(start, end)

        def _pull(meas: str) -> pd.DataFrame:
            flux = (f'from(bucket:"{bucket}")|>range(start:{rfc_start},stop:{rfc_stop})'
                    f'|>filter(fn:(r)=>r._measurement=="{meas}")'
                    f'|>pivot(rowKey:["_time"],columnKey:["_field"],valueColumn:"_value")'
                    f'|>sort(columns:["_time"])')
            with influx_client() as c:
                return query_df(c.query_api(), flux)

        od = _pull("ORDER")
        cols = ["e_ts", "x_ts", "side", "e_px", "x_px", "gross_bps"]
        if od.empty:
            return pd.DataFrame(columns=cols)
        od["ts"] = od["_time"].apply(lambda t: t.timestamp())
        od = od.dropna(subset=["EXEC_PRICE"]).sort_values("ts").reset_index(drop=True)

        # Pair OPEN->CLOSE by walking the time-sorted stream as a one-position-at-a-time
        # state machine. A naive index-zip of the split OPEN/CLOSE lists desyncs the whole
        # tail on any retry/duplicate order (handle_order_error re-emits a CLOSE); the
        # state machine skips a duplicate OPEN while in-position and a CLOSE while flat.
        rows = []
        pos = None  # (e_ts, side, e_px)
        for _, o in od.iterrows():
            st = o["SETTLE_TYPE"]
            if st == "OPEN":
                if pos is None:
                    pos = (o["ts"], 1 if o["SIDE"] == "BUY" else -1, float(o["EXEC_PRICE"]))
            elif st == "CLOSE" and pos is not None:
                e_ts, side, e_px = pos
                x_ts, x_px = o["ts"], float(o["EXEC_PRICE"])
                rows.append((e_ts, x_ts, side, e_px, x_px,
                             side * (x_px - e_px) / e_px * 1e4))
                pos = None
        ht = pd.DataFrame(rows, columns=cols)
        return ht

    # ---- align + report --------------------------------------------------------
    def calibrate(self, bucket: str, start: str, end: str,
                  data_root: Path = _DEFAULT_DATA_ROOT,
                  px_tol: float = 1.0, ts_tol: float = 0.001, ret_tol: float = 0.5,
                  max_div: int = 15) -> TradeCalibResult:
        sc = self.offline_trades(start, end, data_root)
        ht = self.online_trades(bucket, start, end)
        # restrict scan to Himalaya's covered window (first open .. last close)
        if not ht.empty:
            sc = sc[(sc.entry_exec_ts >= ht.e_ts.min() - 5)
                    & (sc.entry_exec_ts <= ht.e_ts.max() + 5)].reset_index(drop=True)

        s_e = sc["entry_exec_ts"].to_numpy() if len(sc) else np.array([])
        idx = (np.clip(np.searchsorted(s_e, ht["e_ts"].to_numpy()), 0, len(s_e) - 1)
               if len(s_e) else np.array([], dtype=int))

        side_m = ets_m = xts_m = epx_m = xpx_m = ret_m = matched = 0
        scan_g: list[float] = []
        hima_g: list[float] = []
        divs: list = []
        for k in range(len(ht)):
            if not len(s_e):
                break
            cands = [c for c in (idx[k] - 1, idx[k]) if 0 <= c < len(s_e)]
            j = min(cands, key=lambda c: abs(s_e[c] - ht["e_ts"][k])) if cands else None
            if j is None or abs(s_e[j] - ht["e_ts"][k]) > 3:
                if len(divs) < max_div:
                    divs.append(f"no scan trade near hima open {ht['e_ts'][k]:.0f} "
                                f"side {ht['side'][k]:+d}")
                continue
            matched += 1
            sr = sc.iloc[j]
            es = abs(sr["entry_exec_ts"] - ht["e_ts"][k]) <= ts_tol
            xs = abs(sr["exit_exec_ts"] - ht["x_ts"][k]) <= ts_tol
            sd = int(sr["direction"]) == int(ht["side"][k])
            ep = abs(sr["entry_price"] - ht["e_px"][k]) <= px_tol
            xp = abs(sr["exit_price"] - ht["x_px"][k]) <= px_tol
            rm = abs(sr["return_bps"] - ht["gross_bps"][k]) <= ret_tol
            side_m += sd; ets_m += es; xts_m += xs; epx_m += ep; xpx_m += xp; ret_m += rm
            scan_g.append(sr["return_bps"]); hima_g.append(ht["gross_bps"][k])
            if (not (es and xs and sd and ep and xp and rm)) and len(divs) < max_div:
                divs.append(
                    f"e {sr['entry_exec_ts']:.0f}/{ht['e_ts'][k]:.0f} "
                    f"x {sr['exit_exec_ts']:.0f}/{ht['x_ts'][k]:.0f} "
                    f"sd {int(sr['direction'])}/{int(ht['side'][k])} "
                    f"epx {sr['entry_price']:.0f}/{ht['e_px'][k]:.0f} "
                    f"g {sr['return_bps']:+.1f}/{ht['gross_bps'][k]:+.1f}")
        sg = np.array(scan_g)
        hg = np.array(hima_g)
        corr = float(np.corrcoef(sg, hg)[0, 1]) if len(sg) > 1 else float("nan")
        return TradeCalibResult(
            n_scan=len(sc), n_hima=len(ht), n_matched=matched,
            side_match=side_m, entry_ts_exact=ets_m, exit_ts_exact=xts_m,
            entry_px_match=epx_m, exit_px_match=xpx_m,
            scan_total_bps=float(sg.sum()), hima_total_bps=float(hg.sum()),
            scan_mean_bps=float(sg.mean()) if len(sg) else 0.0,
            hima_mean_bps=float(hg.mean()) if len(hg) else 0.0,
            ret_corr=corr, ret_match=ret_m,
            px_tol=px_tol, ts_tol=ts_tol, ret_tol=ret_tol, divergences=divs)
