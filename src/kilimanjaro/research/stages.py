"""Job adapters around the existing feature, correlation, blend, and return engines."""

from __future__ import annotations

import math
import shutil
from datetime import UTC, datetime
from pathlib import Path

from kilimanjaro.data.catalog import days, inspect_partition, relative_path
from kilimanjaro.data.providers import DataUnavailable, derive, link_lakes

from .artifacts import read_json, write_json
from .contracts import instant
from .identity import checksum, identity


def clean(value):
    """Reports represent unavailable statistics as null, never JSON NaN."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, "item"):
        value = value.item()
    return None if isinstance(value, float) and not math.isfinite(value) else value


def prices(spec, lake):
    from kilimanjaro.data.price_cache import load_target_prices

    target = spec["markets"][spec["target"]]
    dates = days(
        instant(spec["windows"]["train"]["start"]) - spec["warmup_seconds"],
        instant(spec["windows"]["validation"]["end"]),
    )
    return load_target_prices(
        target["exchange"],
        target["symbol"],
        target["execution_size"],
        dates[0],
        dates[-1],
        lake,
    )


def label_panel(frame, market_prices, horizon, window, max_delay):
    """Mature first-quote labels; exclude intervals crossing the role boundary."""
    import numpy as np

    ts = frame.ts.to_numpy()
    p = market_prices
    if len(p.ts) == 0:
        raise DataUnavailable("No execution quotes")
    entry = p.first_at_or_after(ts)
    exit_ = p.first_at_or_after(ts + horizon)
    valid = (entry < len(p.ts)) & (exit_ < len(p.ts))
    e = np.minimum(entry, len(p.ts) - 1)
    x = np.minimum(exit_, len(p.ts) - 1)
    valid &= (p.ts[e] - ts <= max_delay) & (p.ts[x] - ts - horizon <= max_delay)
    valid &= (ts >= instant(window["start"])) & (p.ts[x] < instant(window["end"]))
    valid &= (
        np.isfinite(p.mid[e])
        & np.isfinite(p.mid[x])
        & (p.bid[e] > 0)
        & (p.ask[e] >= p.bid[e])
        & (p.bid[x] > 0)
        & (p.ask[x] >= p.bid[x])
    )
    out = frame.loc[valid].copy()
    out["fwd_ret"] = (p.mid[x[valid]] / p.mid[e[valid]] - 1) * 1e4
    out["label_end"] = p.ts[x[valid]]
    return out


def candidate_score(frame, candidate):
    import numpy as np

    score = np.zeros(len(frame))
    available = np.ones(len(frame), dtype=bool)
    for name, weight in candidate["weights"].items():
        normalization = candidate["normalization"][name]
        values = frame[name].to_numpy(dtype=float)
        available &= np.isfinite(values)
        score += weight * np.nan_to_num(
            (values - normalization["mean"]) / normalization["std"],
            nan=0.0,
            posinf=0.0,
            neginf=0.0,
        )
    score = (score - candidate["signal_mean"]) / candidate["signal_scale"]
    return np.where(available, score, np.nan)


def evaluate_candidate(spec, frame, candidate, lake, role):
    import numpy as np
    import pandas as pd

    from kilimanjaro.framework.protocol import MarketConfig
    from kilimanjaro.op.age import threshold_episode
    from kilimanjaro.return_calc.episode import EpisodeReturn

    window = spec["windows"][role]
    score = candidate_score(frame, candidate)
    in_window = (frame.ts >= instant(window["start"])) & (
        frame.ts < instant(window["end"])
    )
    coverage = float((in_window & np.isfinite(score)).sum()) / (
        instant(window["end"]) - instant(window["start"])
    )
    series = pd.Series(np.where(in_window, score, np.nan)).fillna(0)
    active = (series.abs() >= spec["execution"]["threshold"]) & in_window
    signals = pd.DataFrame(
        {
            "ts": frame.ts,
            "direction": np.sign(series),
            "strength": series.abs(),
            "episode": threshold_episode(series, spec["execution"]["threshold"]),
        }
    )[active]
    if signals.empty:
        return pd.DataFrame(
            columns=["ts", "return_bps", "net_bps", "entry_exec_ts", "exit_exec_ts"]
        ), {"signals": 0, "censored": 0, "coverage": coverage}
    target = spec["markets"][spec["target"]]
    dates = days(instant(window["start"]), instant(window["end"]))
    trades = EpisodeReturn(max_hold_seconds=candidate["horizon"]).compute(
        signals, MarketConfig(**target), dates[0], dates[-1], lake
    )
    delay = spec["execution"]["max_quote_delay_seconds"]
    valid = (trades.entry_exec_ts - trades.ts <= delay) & (
        trades.exit_exec_ts - trades.exit_ts <= delay
    )
    valid &= (
        (trades.exit_exec_ts < instant(window["end"]))
        & np.isfinite(trades.return_bps)
        & (trades.entry_price > 0)
        & (trades.exit_price > 0)
    )
    # Signal episodes are distinct; delayed execution can still overlap fills.
    last_exit = -np.inf
    for i in trades.index:
        if valid.loc[i] and trades.loc[i, "entry_exec_ts"] >= last_exit:
            last_exit = float(trades.loc[i, "exit_exec_ts"])
        else:
            valid.loc[i] = False
    rejected = int((~valid).sum())
    trades = trades.loc[valid].copy()
    trades["net_bps"] = (
        trades.return_bps
        - spec["execution"]["round_trip_fee_bps"]
        - spec["execution"]["round_trip_slippage_bps"]
    )
    return trades, {"signals": len(signals), "censored": rejected, "coverage": coverage}


def summarize(trades):
    import numpy as np

    from kilimanjaro.framework.performance import compute_path_metrics

    if trades.empty:
        return {"n_trades": 0, "mean_net_bps": None, "total_net_bps": 0.0}
    values = trades.net_bps.to_numpy(dtype=float)
    result = compute_path_metrics(values, trades.entry_exec_ts.to_numpy(dtype=float))
    equity = np.r_[0, np.cumsum(values)]
    return clean(
        {
            **result,
            "n_trades": len(trades),
            "total_net_bps": float(values.sum()),
            "trade_max_drawdown_bps": float(
                np.max(np.maximum.accumulate(equity) - equity)
            ),
            "mean_net_bps": float(np.mean(values)),
            "positive_trade_share": float(np.mean(values > 0)),
            "extra_cost_sensitivity_mean_bps": {
                str(extra): float(values.mean() - extra) for extra in (0, 1, 2)
            },
        }
    )


def execute(
    stage: str,
    spec: dict,
    payload: dict,
    parents: list[Path],
    inputs: dict,
    output: Path,
    run_id: str,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    if stage in {"snapshot", "derive"}:
        request = payload["request"]
        lake = output / "lake"
        link_lakes(parents, lake)
        destination = lake / relative_path(request)
        if stage == "snapshot":
            source = Path(spec["data_root"]) / payload["inspection"]["relative_path"]
            expected = payload["inspection"]["checksum"]
            if not source.is_file() or checksum(source) != expected:
                raise DataUnavailable(
                    "Source revision changed: create a new resolved plan"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if checksum(destination) != expected or checksum(source) != expected:
                raise DataUnavailable("Source changed during snapshot")
        else:
            derive(request, lake)
        inspection = inspect_partition(lake, request)
        if inspection["status"] != "ready":
            raise DataUnavailable(f"Invalid snapshot: {inspection}")
        write_json(output / "partition.json", inspection)
        return
    if stage == "assemble":
        link_lakes(parents, output / "lake")
        write_json(
            output / "snapshot.json",
            {
                "run_id": run_id,
                "partitions": [read_json(p / "partition.json") for p in parents],
            },
        )
        return
    lake = Path(inputs["assemble"]) / "lake"
    if stage == "features":
        build_features(spec, lake, output)
    elif stage == "correlation":
        screen(spec, Path(inputs["features"]), lake, output)
    elif stage == "blends":
        fit_blends(spec, Path(inputs["features"]), lake, output)
    elif stage == "returns":
        return_evaluation(
            spec, Path(inputs["features"]), Path(inputs["blends"]), lake, output, run_id
        )
    elif stage == "confirm":
        import pandas as pd

        from .confirmation import judge

        config = spec["_confirmation"]
        frame = pd.read_parquet(Path(inputs["features"]) / "features.parquet")
        trades, counts = evaluate_candidate(
            spec, frame, config["protocol"]["recipe"], lake, "validation"
        )
        trades.to_parquet(output / "trades.parquet", index=False)
        write_json(
            output / "confirmation.json",
            judge(
                trades, config["protocol"], config["alpha"], coverage=counts["coverage"]
            ),
        )
    elif stage == "report":
        result = read_json(Path(inputs["returns"]) / "results.json")
        write_json(
            output / "report.json",
            {
                "run_id": run_id,
                "status": "exploratory",
                "calibration": "unverified",
                "results": result,
            },
        )
        lines = [
            "# Research evaluation",
            "",
            f"Run: `{run_id}`",
            "",
            "Evidence role: development/validation. Fresh confirmation and mechanism calibration remain separate.",
            "",
            "| Candidate | Horizon | Validation trades | Mean net bps |",
            "|---|---:|---:|---:|",
        ]
        for row in result["candidates"]:
            stats = row["validation"]
            lines.append(
                f"| {row['id']} | {row['horizon']} | {stats['n_trades']} | {stats['mean_net_bps']} |"
            )
        (output / "REVIEW_PACKET.md").write_text("\n".join(lines) + "\n")
    else:
        raise ValueError(f"Unknown research stage {stage}")


def build_features(spec, lake, output):
    import numpy as np

    from kilimanjaro.data.feature_frame import load_feature_frame
    from kilimanjaro.signal.expression import eval_expr, validate_expr

    columns = sorted(
        {c for f in spec["features"] for c in validate_expr(f["expr"])}
        | {f"{spec['target']}_{c}" for c in ("mid", "bid", "ask")}
    )
    start = instant(spec["windows"]["train"]["start"]) - spec["warmup_seconds"]
    end = instant(spec["windows"]["validation"]["end"])
    date_range = days(start, end)
    frame = load_feature_frame(
        spec["markets"],
        columns,
        date_range[0],
        date_range[-1],
        lake,
        ffill_limit_s=spec["max_staleness_seconds"],
        availability_shift_seconds=1,
    )
    # Pin the warmup start: loading a whole partition must not change the recipe.
    frame = frame[(frame.ts >= start) & (frame.ts < end)].reset_index(drop=True)
    if frame.empty:
        raise DataUnavailable(
            "No overlapping feature observations in the requested interval"
        )
    result = frame[
        ["ts", *[f"{spec['target']}_{c}" for c in ("mid", "bid", "ask")]]
    ].copy()
    for i, feature in enumerate(spec["features"]):
        values = eval_expr(feature["expr"], frame).replace([np.inf, -np.inf], np.nan)
        result[f"F{i}"] = values
    result.to_parquet(output / "features.parquet", index=False)
    write_json(
        output / "features.json",
        {
            "features": spec["features"],
            "availability": "bucket_end",
            "warmup_seconds": spec["warmup_seconds"],
            "rows": len(result),
            "missing": {c: int(result[c].isna().sum()) for c in result},
        },
    )


def screen(spec, feature_dir, lake, output):
    import pandas as pd

    from kilimanjaro.driver.signal_corr import Factor, compute

    frame = pd.read_parquet(feature_dir / "features.parquet")
    window = spec["windows"]["train"]
    frame = frame[
        (frame.ts >= instant(window["start"])) & (frame.ts < instant(window["end"]))
    ].reset_index(drop=True)
    result = compute(
        spec["markets"],
        [Factor(f["name"], f"F{i}") for i, f in enumerate(spec["features"])],
        f"{spec['target']}_mid",
        datetime.fromtimestamp(instant(window["start"]), UTC).date().isoformat(),
        datetime.fromtimestamp(instant(window["end"]), UTC).date().isoformat(),
        spec["horizons"],
        data_root=Path(spec["data_root"]),
        target_alias=spec["target"],
        frame=frame,
    )
    result.ic.assign(data_role="train", inference="descriptive").to_parquet(
        output / "relationships.parquet", index=False
    )
    result.corr.to_parquet(output / "factor_correlation.parquet")
    result.dist.to_parquet(output / "distributions.parquet", index=False)
    from .relationships import analyze

    full = pd.read_parquet(feature_dir / "features.parquet")
    write_json(output / "relationships.json", analyze(spec, full, prices(spec, lake)))


def fit_blends(spec, feature_dir, lake, output):
    import pandas as pd

    from kilimanjaro.driver.blend_corr import scan_panels
    from kilimanjaro.return_calc.fixed_horizon import _nonoverlap_indices

    frame = pd.read_parquet(feature_dir / "features.parquet")
    target = prices(spec, lake)
    names = [f"F{i}" for i in range(len(spec["features"]))]
    results = []
    for horizon in spec["horizons"]:
        panels = []
        for role in ("train", "validation"):
            panel = label_panel(
                frame,
                target,
                horizon,
                spec["windows"][role],
                spec["execution"]["max_quote_delay_seconds"],
            )
            panel = panel.iloc[_nonoverlap_indices(panel.ts.to_numpy(), horizon)]
            panels.append(panel)
        if min(map(len, panels)) < 20:
            results.append(
                {"horizon": horizon, "status": "insufficient_data", "results": []}
            )
        else:
            result = scan_panels(*panels, names, horizon)
            for candidate in result["results"]:
                candidate["normalization"] = result["normalization"]
                candidate["horizon"] = horizon
            results.append(result)
    write_json(output / "blends.json", clean(results))


def return_evaluation(spec, feature_dir, blend_dir, lake, output, run_id):
    import pandas as pd

    frame = pd.read_parquet(feature_dir / "features.parquet")
    candidates = []
    for result in read_json(blend_dir / "blends.json"):
        for candidate in result["results"]:
            if not candidate["weights"]:
                continue
            candidate_id = identity(
                "candidate", {"run_id": run_id, "recipe": candidate}
            )
            record = {
                "id": candidate_id,
                "horizon": candidate["horizon"],
                "recipe": candidate,
            }
            for role in ("train", "validation"):
                trades, counts = evaluate_candidate(spec, frame, candidate, lake, role)
                trades.to_parquet(
                    output / f"{candidate_id}-{role}.parquet", index=False
                )
                record[role] = {**summarize(trades), **counts}
            candidates.append(record)
    candidates.sort(
        key=lambda r: (
            r["validation"]["mean_net_bps"]
            if r["validation"]["mean_net_bps"] is not None
            else -math.inf
        ),
        reverse=True,
    )
    write_json(
        output / "results.json",
        clean(
            {
                "candidates": candidates,
                "evidence_role": "development",
                "trial_count": (2 * len(spec["features"]) + 4) * len(spec["horizons"]),
                "execution": spec["execution"],
            }
        ),
    )
