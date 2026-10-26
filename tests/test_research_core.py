import pytest

from kilimanjaro.research.artifacts import manifest, write_json
from kilimanjaro.research.contracts import Campaign
from kilimanjaro.research.identity import identity
from kilimanjaro.research.store import Store


def campaign_spec(tmp_path):
    return {
        "schema_version": 1,
        "name": "pilot",
        "program": "discovery",
        "data_root": str(tmp_path / "lake"),
        "research_root": str(tmp_path / "cycle"),
        "markets": {
            "a": {
                "exchange": "BINANCE",
                "symbol": "BTC_USDT",
                "execution_size": "0.002",
            },
            "b": {"exchange": "GMO", "symbol": "BTC_JPY", "execution_size": "0.002"},
        },
        "target": "b",
        "features": [{"name": "momentum", "expr": "ret(a_mid, 5)"}],
        "windows": {
            "train": {"start": "2026-01-02T00:00:00Z", "end": "2026-01-02T01:00:00Z"},
            "validation": {
                "start": "2026-01-02T01:00:00Z",
                "end": "2026-01-02T02:00:00Z",
            },
        },
        "horizons": [5, 10],
        "execution": {
            "threshold": 1.0,
            "round_trip_fee_bps": 0,
            "round_trip_slippage_bps": 1,
            "max_quote_delay_seconds": 2,
        },
        "budgets": {
            "max_jobs": 100,
            "daily_jobs": 100,
            "memory_mb": 2048,
            "disk_mb": 1024,
            "job_timeout_seconds": 60,
            "max_attempts": 3,
            "max_variants": 100,
        },
        "seed": 7,
        "warmup_seconds": 60,
        "max_staleness_seconds": 2,
        "trial_policy": {
            "program_alpha": 0.05,
            "min_trades": 20,
            "min_days": 2,
            "bootstrap_samples": 1000,
            "max_drawdown_bps": 500,
            "max_trades_per_day": 1000,
            "max_profit_day_share": 0.8,
        },
    }


def enqueue_fixture(store, spec):
    run = identity("run", spec)
    jobs = [
        {
            "id": identity("job", i),
            "stage": "fixture",
            "payload": {"i": i},
            "depends": [],
        }
        for i in range(3)
    ]
    store.enqueue(run, "campaign-test", spec, {}, jobs)
    return run, jobs


def test_identity_and_contracts(tmp_path):
    assert identity("run", {"a": 1.0, "b": 2}) == identity("run", {"b": 2, "a": 1})
    with pytest.raises(ValueError):
        identity("run", {"x": float("nan")})
    spec = campaign_spec(tmp_path)
    campaign = Campaign.from_dict(spec)
    spec["seed"] = 8
    assert campaign.logical_id != Campaign.from_dict(spec).logical_id
    spec["windows"]["train"]["end"] = "2026-02-01T00:00:00Z"
    with pytest.raises(ValueError):
        Campaign.from_dict(spec)


def test_budget_survives_restart_and_claim_is_exclusive(tmp_path):
    spec = campaign_spec(tmp_path)
    spec["budgets"]["max_jobs"] = 2
    path = tmp_path / "research.sqlite"
    with Store(path) as a, Store(path) as b:
        enqueue_fixture(a, spec)
        first = a.claim("worker")
        assert b.claim("other") is None
        a.fail(first["id"], first["token"], "timeout", retry=False)
    with Store(path) as a:
        second = a.claim("worker")
        a.fail(second["id"], second["token"], "timeout", retry=False)
        assert a.claim("worker") is None
        assert sum(r["count"] for r in a.status()["admissions"]) == 2


def test_recovery_fences_old_attempt_and_deduplicates_outbox(tmp_path):
    now = [1000.0]
    with Store(tmp_path / "state.sqlite", clock=lambda: now[0]) as store:
        enqueue_fixture(store, campaign_spec(tmp_path))
        first = store.claim("worker", lease_seconds=10)
        now[0] += 11
        assert store.recover("worker") == 1
        second = store.claim("worker")
        output = tmp_path / "artifacts"
        write_json(output / "result.json", {"verdict": "rejected"})
        with pytest.raises(ValueError):
            store.finish(first["id"], first["token"], output, manifest(output))
        store.finish(second["id"], second["token"], output, manifest(output))
        ledger = tmp_path / "events.jsonl"
        store.publish_events(ledger)
        before = ledger.read_text()
        store.db.execute("UPDATE outbox SET published=0")
        store.db.commit()
        store.publish_events(ledger)
        assert ledger.read_text() == before
        store.backup(tmp_path / "backup.sqlite")
    with Store(tmp_path / "backup.sqlite", readonly=True) as restored:
        assert restored.job(first["id"])["state"] == "succeeded"


def test_artifact_conflict_and_corruption_do_not_complete(tmp_path):
    with Store(tmp_path / "state.sqlite") as store:
        enqueue_fixture(store, campaign_spec(tmp_path))
        job = store.claim("worker")
        output = tmp_path / "out"
        write_json(output / "a.json", {"value": 1})
        files = manifest(output)
        with pytest.raises(ValueError):
            write_json(output / "a.json", {"value": 2})
        files["a.json"] = "not-the-checksum"
        with pytest.raises(ValueError):
            store.finish(job["id"], job["token"], output, files)
        assert store.job(job["id"])["state"] == "running"


def test_torn_ledger_tail_is_archived_and_outbox_recovers(tmp_path):
    from kilimanjaro.ledger.core import load_events

    with Store(tmp_path / "state.sqlite") as store:
        enqueue_fixture(store, campaign_spec(tmp_path))
        ledger = tmp_path / "events.jsonl"
        store.publish_events(ledger)
        before = ledger.read_bytes()
        with ledger.open("ab") as output:
            output.write(b'{"event_id":')
        assert len(load_events(ledger)) == 1
        store.db.execute("UPDATE outbox SET published=0")
        store.db.commit()
        store.publish_events(ledger)
        assert ledger.read_bytes() == before
        assert len(list(tmp_path.glob("events.jsonl.torn-*"))) == 1
