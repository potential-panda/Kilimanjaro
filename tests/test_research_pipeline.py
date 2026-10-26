from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from test_research_core import campaign_spec

from kilimanjaro.data.catalog import inspect_partition, relative_path
from kilimanjaro.data.providers import derive
from kilimanjaro.research.artifacts import read_json
from kilimanjaro.research.contracts import Campaign, instant
from kilimanjaro.research.planner import enqueue, plan
from kilimanjaro.research.worker import run


def synthetic_lake(spec):
    start = instant(spec["windows"]["train"]["start"]) - 120
    n = 7500
    rng = np.random.default_rng(42)
    lead_returns = rng.normal(0, 0.00015, n)
    lead = 100 * np.exp(np.cumsum(lead_returns))
    lag_returns = np.roll(lead_returns, 5) + rng.normal(0, 0.00001, n)
    lag = 100 * np.exp(np.cumsum(lag_returns))
    for alias, mid in (("a", lead), ("b", lag)):
        market = spec["markets"][alias]
        request = {
            **market,
            "dataset": "MarketPrice",
            "date": "2026-01-02",
            "params": {"execution_size": float(market["execution_size"])},
            "columns": ["ts", "bid", "ask", "mid"],
        }
        path = Path(spec["data_root"]) / relative_path(request)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {
                "ts": start + np.arange(n) + 0.5,
                "mid": mid,
                "bid": mid * (1 - 0.00001),
                "ask": mid * (1 + 0.00001),
                "spread_bps": 0.2,
            }
        ).to_parquet(path, index=False)


def test_full_pipeline_is_reproducible_and_resumable(tmp_path):
    spec = campaign_spec(tmp_path)
    synthetic_lake(spec)
    campaign = Campaign.from_dict(spec)
    before = plan(campaign)
    assert not before["data"]["waiting"]
    resolved = enqueue(campaign, before)
    result = run(
        campaign.database, run_id=resolved["run_id"], max_jobs=30, max_minutes=2
    )
    assert all(j["state"] == "succeeded" for j in result["status"]["jobs"]), result
    report_job = next(j for j in result["status"]["jobs"] if j["stage"] == "report")
    report = read_json(Path(report_job["artifact_dir"]) / "report.json")
    assert report["status"] == "exploratory"
    assert report["results"]["candidates"]
    single = next(
        c
        for c in report["results"]["candidates"]
        if c["recipe"]["method"] == "single_best" and c["horizon"] == 5
    )
    assert single["recipe"]["weights"]["F0"] > 0
    assert single["validation"]["mean_net_bps"] > 0
    # The same fills lose under sufficiently high costs, charged exactly once.
    from kilimanjaro.research.stages import evaluate_candidate

    directories = {
        j["stage"]: Path(j["artifact_dir"]) for j in result["status"]["jobs"]
    }
    frame = pd.read_parquet(directories["features"] / "features.parquet")
    expensive = Campaign.from_dict(spec).spec
    expensive["execution"]["round_trip_fee_bps"] = 100
    costly, _ = evaluate_candidate(
        expensive,
        frame,
        single["recipe"],
        directories["assemble"] / "lake",
        "validation",
    )
    assert costly.net_bps.mean() == pytest.approx(
        single["validation"]["mean_net_bps"] - 100
    )
    assert (
        run(campaign.database, run_id=resolved["run_id"], max_jobs=3, max_minutes=1)[
            "attempts"
        ]
        == []
    )
    assert plan(campaign)["run_id"] == resolved["run_id"]


def test_missing_derived_partition_routes_to_raw_and_parameter_identity(tmp_path):
    spec = campaign_spec(tmp_path)
    resolved = plan(Campaign.from_dict(spec))
    assert any(n["action"] == "derive" for n in resolved["data"]["nodes"].values())
    assert resolved["data"]["waiting"]
    original = resolved["run_id"]
    spec["markets"]["b"]["execution_size"] = "0.01"
    assert plan(Campaign.from_dict(spec))["run_id"] != original


def test_real_hase_provider_on_isolated_bronze(tmp_path):
    request = {
        "exchange": "GMO",
        "symbol": "BTC_JPY",
        "dataset": "MarketPrice",
        "date": "2026-01-02",
        "params": {"execution_size": 0.002},
        "columns": ["ts", "bid", "ask", "mid"],
    }
    raw = {**request, "dataset": "OrderBook", "params": {}}
    path = tmp_path / relative_path(raw)
    path.parent.mkdir(parents=True)
    columns = {"ts": np.arange(10) + instant("2026-01-02T00:00:00Z")}
    for i in range(20):
        columns[f"bid{i}_price"] = np.full(10, 100 - i * 0.01)
        columns[f"ask{i}_price"] = np.full(10, 100.01 + i * 0.01)
        columns[f"bid{i}_qty"] = columns[f"ask{i}_qty"] = np.ones(10)
    pd.DataFrame(columns).to_parquet(path, index=False)
    derive(request, tmp_path)
    result = inspect_partition(tmp_path, request)
    assert result["status"] == "ready"
    assert result["rows"] == 10


def test_real_makalu_bookstate_provider_on_isolated_bronze(tmp_path):
    request = {
        "exchange": "GMO",
        "symbol": "BTC_JPY",
        "dataset": "BookState",
        "date": "2026-01-02",
        "params": {},
        "columns": ["ts", "vol_imb_k1"],
    }
    for date in ("2026-01-01", "2026-01-02"):
        raw = {**request, "dataset": "OrderBook", "date": date}
        path = tmp_path / relative_path(raw)
        path.parent.mkdir(parents=True)
        columns = {"ts": np.arange(10) + instant(f"{date}T00:00:00Z")}
        for i in range(20):
            columns[f"bid{i}_price"] = np.full(10, 100 - i * 0.01)
            columns[f"ask{i}_price"] = np.full(10, 100.01 + i * 0.01)
            columns[f"bid{i}_qty"] = columns[f"ask{i}_qty"] = np.ones(10)
        pd.DataFrame(columns).to_parquet(path, index=False)
    derive(request, tmp_path)
    assert inspect_partition(tmp_path, request)["status"] == "ready"


def test_future_observations_do_not_change_prior_features(tmp_path):
    from kilimanjaro.research.stages import build_features

    spec = Campaign.from_dict(campaign_spec(tmp_path)).spec
    synthetic_lake(spec)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    build_features(spec, Path(spec["data_root"]), first)
    cutoff = instant(spec["windows"]["validation"]["start"])
    for file in Path(spec["data_root"]).rglob("data.parquet"):
        frame = pd.read_parquet(file)
        for col in ("mid", "bid", "ask"):
            frame.loc[frame.ts >= cutoff, col] *= 2
        frame.to_parquet(file, index=False)
    build_features(spec, Path(spec["data_root"]), second)
    a, b = (pd.read_parquet(p / "features.parquet") for p in (first, second))
    pd.testing.assert_frame_equal(a[a.ts <= cutoff], b[b.ts <= cutoff])
