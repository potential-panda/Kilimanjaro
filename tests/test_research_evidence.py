import copy
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from test_research_core import campaign_spec

from kilimanjaro.research.artifacts import manifest, write_json
from kilimanjaro.research.confirmation import freeze, judge
from kilimanjaro.research.contracts import Campaign, instant
from kilimanjaro.research.identity import implementation
from kilimanjaro.research.store import Store
from kilimanjaro.research.validation import purge_intervals


def ready_candidate(store, tmp_path):
    spec = Campaign.from_dict(campaign_spec(tmp_path)).spec
    recipe = {
        "weights": {"F0": 1.0},
        "normalization": {"F0": {"mean": 0, "std": 1}},
        "signal_mean": 0,
        "signal_scale": 1,
        "horizon": 5,
    }
    evidence = {"n_trades": 50, "mean_net_bps": 1.0, "coverage": 1.0}
    store.enqueue(
        "run-" + "1" * 32,
        "campaign-fixture",
        spec,
        implementation(),
        [{"id": "returns", "stage": "returns", "payload": {}}],
    )
    job = store.claim("fixture")
    folder = tmp_path / "results"
    write_json(
        folder / "results.json",
        {
            "candidates": [
                {
                    "id": name,
                    "horizon": 5,
                    "recipe": recipe,
                    "train": evidence,
                    "validation": evidence,
                }
                for name in ("one", "two")
            ]
        },
    )
    store.finish(job["id"], job["token"], folder, manifest(folder))
    return job["campaign_id"], spec


def test_freeze_persists_alpha_and_seals_across_campaigns(tmp_path):
    now = [instant("2026-01-02T03:00:00Z")]
    with Store(tmp_path / "state.sqlite", clock=lambda: now[0]) as store:
        run, spec = ready_candidate(store, tmp_path)
        args = {"start": "2026-01-03T00:00:00Z", "end": "2026-02-03T00:00:00Z"}
        first = freeze(store, run, "one", **args)
        assert freeze(store, run, "one", **args)["id"] == first["id"]
        assert first["alpha"] == 0.025
        second = freeze(store, run, "two", **args)
        assert second["alpha"] == pytest.approx(0.05 / 6)
        spec["program"] = "renamed-program"
        spec["windows"]["validation"] = {
            "start": "2026-01-04T00:00:00Z",
            "end": "2026-01-05T00:00:00Z",
        }
        with pytest.raises(ValueError, match="sealed"):
            store.enqueue("new-run", "logical", spec, {}, [])
        assert store.db.execute("SELECT count(*) FROM campaigns").fetchone()[0] == 1
        with pytest.raises(ValueError, match="freeze"):
            freeze(
                store,
                run,
                "two",
                start="2026-01-01T00:00:00Z",
                end="2026-02-01T00:00:00Z",
            )


def test_purge_actual_label_intervals():
    start = np.array([90, 95, 100, 105, 110])
    end = np.array([94, 100, 104, 110, 115])
    assert purge_intervals(start, end, 100, 110).tolist() == [
        True,
        False,
        False,
        False,
        True,
    ]


def protocol(tmp_path, n_days=240):
    spec = Campaign.from_dict(campaign_spec(tmp_path)).spec
    start = datetime(2026, 1, 3, tzinfo=UTC)
    return {
        "start": start.isoformat(),
        "end": (start + timedelta(days=n_days)).isoformat(),
        "endpoint": "mean_daily_net_bps",
        "campaign": spec,
        "policy": {**spec["trial_policy"], "bootstrap_samples": 499},
    }


def trades_for(values, p):
    return pd.DataFrame(
        {
            "entry_exec_ts": instant(p["start"]) + np.arange(len(values)) * 86400 + 1,
            "net_bps": values,
        }
    )


def test_fixed_date_null_simulation_and_known_effect(tmp_path):
    # Declared before inspecting results: seeds 0..79, AR(1) rho=.3,
    # 240 days, alpha=.025, 499 resamples; tolerance <=9/80 rejections.
    # This is a regression diagnostic, not a universal FWER guarantee.
    p = protocol(tmp_path)
    confirmed = 0
    for seed in range(80):
        rng = np.random.default_rng(seed)
        values = rng.normal(size=240)
        for i in range(1, len(values)):
            values[i] += 0.3 * values[i - 1]
        confirmed += judge(trades_for(values, p), p, 0.025)["state"] == "confirmed"
    assert confirmed <= 9, confirmed
    assert judge(trades_for(np.ones(240), p), p, 0.025)["state"] == "confirmed"
    assert judge(trades_for(-np.ones(240), p), p, 0.025)["state"] == "rejected"
    assert (
        judge(trades_for(np.ones(240), p), p, 0.025, coverage=0.8)["state"]
        == "insufficient_evidence"
    )
    assert (
        judge(trades_for(np.ones(240), p), p, 0.0001)["reason"]
        == "bootstrap_resolution_below_allocated_alpha"
    )


def test_prospective_observations_cannot_rewrite_or_label_future(tmp_path):
    from kilimanjaro.research.observation import mature, record

    now = [instant("2026-01-02T03:00:00Z")]
    with Store(tmp_path / "state.sqlite", clock=lambda: now[0]) as store:
        run, _spec = ready_candidate(store, tmp_path)
        item = freeze(
            store, run, "one", start="2026-01-03T00:00:00Z", end="2026-02-03T00:00:00Z"
        )
        now[0] = instant("2026-01-03T00:00:00Z")
        args = {"decision": now[0], "available_at": now[0], "features": {"F0": 2.0}}
        prediction = record(store, item["id"], **args)
        assert record(store, item["id"], **args) == prediction
        with pytest.raises(ValueError, match="rewritten"):
            record(store, item["id"], **{**args, "features": {"F0": 3.0}})
        with pytest.raises(ValueError, match="Future"):
            mature(store, item["id"], args["decision"], quotes=[])
        now[0] += 6
        quotes = [
            {"ts": now[0] - 6, "bid": 99.9, "ask": 100.0},
            {"ts": now[0] - 1, "bid": 101.0, "ask": 101.1},
        ]
        outcome = mature(store, item["id"], args["decision"], quotes=quotes)
        assert outcome["net_bps"] == pytest.approx(99.0)
        assert mature(store, item["id"], args["decision"], quotes=[]) == outcome
        with pytest.raises(ValueError, match="within one second"):
            record(
                store,
                item["id"],
                decision=now[0] - 3,
                available_at=now[0] - 3,
                features={"F0": 2.0},
            )


def test_trade_calibration_catches_equal_pnl_with_wrong_timing(tmp_path):
    from kilimanjaro.research.calibration import (
        compare_artifacts,
        compatible_certificates,
        scope_for,
    )

    with Store(tmp_path / "state.sqlite") as store:
        run, _spec = ready_candidate(store, tmp_path)
        scope = scope_for(store, run, {"horizon": 5}, "enoshima-fixture-v1")
        off = pd.DataFrame(
            {
                "entry_exec_ts": [100.0, 200.0],
                "exit_exec_ts": [105.0, 205.0],
                "direction": [1, 1],
                "entry_price": [100.0, 100.0],
                "exit_price": [101.0, 101.0],
                "return_bps": [100.0, 100.0],
            }
        )
        on = off.rename(
            columns={
                "entry_exec_ts": "e_ts",
                "exit_exec_ts": "x_ts",
                "direction": "side",
                "entry_price": "e_px",
                "exit_price": "x_px",
                "return_bps": "gross_bps",
            }
        )
        off.to_parquet(tmp_path / "off.parquet")
        on.to_parquet(tmp_path / "on.parquet")
        result = compare_artifacts(
            store,
            run,
            scope,
            tmp_path / "off.parquet",
            tmp_path / "on.parquet",
            kind="trades",
        )
        assert result["state"] == "matched"
        on["x_ts"] += 1
        on.to_parquet(tmp_path / "on.parquet")
        assert (
            compare_artifacts(
                store,
                run,
                scope,
                tmp_path / "off.parquet",
                tmp_path / "on.parquet",
                kind="trades",
            )["state"]
            == "mismatch"
        )
        changed = copy.deepcopy(scope)
        changed["execution"]["round_trip_fee_bps"] += 1
        assert compatible_certificates(store, changed) == []


def test_redundancy_and_complementary_relationships():
    from kilimanjaro.research.relationships import panel_relationships

    rng = np.random.default_rng(10)
    frame = pd.DataFrame(rng.normal(size=(2000, 2)), columns=["F0", "F1"])
    frame["F2"] = frame.F0
    frame["state"] = np.abs(frame.F0)
    frame["fwd_ret"] = 2 * frame.F0 + frame.F1 + rng.normal(size=2000) * 0.1
    result = panel_relationships(
        frame.iloc[:1000], frame.iloc[1000:], ["F0", "F1", "F2"], 5
    )
    assert ["F0", "F2"] in result["clusters"]
    incremental = next(
        r
        for r in result["relationships"]
        if r.get("feature") == "F1"
        and r.get("role") == "validation"
        and r["kind"] == "feature_return"
    )
    assert incremental["incremental_ic"] > 0.3
