import copy
from pathlib import Path

import pandas as pd
import pytest
import yaml
from test_research_core import campaign_spec, enqueue_fixture
from test_research_pipeline import synthetic_lake

from kilimanjaro.research.contracts import Campaign
from kilimanjaro.research.service import load_config, tick
from kilimanjaro.research.store import Store
from kilimanjaro.research.worker import run


def test_new_revision_does_not_replenish_program_budget(tmp_path):
    spec = campaign_spec(tmp_path)
    spec["budgets"]["max_jobs"] = 1
    with Store(tmp_path / "state.sqlite") as store:
        enqueue_fixture(store, spec)
        job = store.claim("worker")
        store.fail(job["id"], job["token"], "interrupted", retry=False)
        changed = copy.deepcopy(spec)
        changed["seed"] = 20
        changed["name"] = "challenger"
        store.enqueue(
            "new-run",
            "new-logical",
            changed,
            {},
            [{"id": "new-job", "stage": "fixture", "payload": {}}],
        )
        assert store.claim("worker", run_id="new-run") is None


def test_service_idle_restart_revision_and_backup_resume(tmp_path, monkeypatch):
    spec = campaign_spec(tmp_path)
    synthetic_lake(spec)
    campaign_path = tmp_path / "campaign.yaml"
    campaign_path.write_text(yaml.safe_dump(spec))
    config_path = tmp_path / "service.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "campaigns": ["campaign.yaml"],
                "max_jobs_per_tick": 30,
            }
        )
    )
    config = load_config(config_path)
    first = tick(config)
    assert first["attempts"] and not first["errors"]
    assert first["summary"]["jobs"] == {"succeeded": 8}
    with monkeypatch.context() as patch:
        patch.setattr(
            "kilimanjaro.research.service.plan",
            lambda *_: pytest.fail("Idle poll rehashed the lake"),
        )
        second = tick(config)
    assert second["attempts"] == []
    assert second["changed_runs"] == []
    # A corrected partition creates new identity and preserves the old result.
    file = next(Path(spec["data_root"]).rglob("data.parquet"))
    frame = pd.read_parquet(file)
    for column in ("mid", "bid", "ask"):
        frame[column] *= 1.001
    frame.to_parquet(file, index=False)
    config["max_jobs_per_tick"] = 1
    third = tick(config)
    assert third["changed_runs"] != first["changed_runs"]
    campaign = Campaign.from_dict(spec)
    backup = tmp_path / "restore" / "research.sqlite"
    backup.parent.mkdir()
    with Store(campaign.database) as store:
        store.backup(backup)
    continued = run(backup, run_id=third["changed_runs"][0], max_jobs=30, max_minutes=2)
    assert all(j["state"] == "succeeded" for j in continued["status"]["jobs"])


def test_search_replays_deterministically_and_keeps_replication(tmp_path):
    from kilimanjaro.research.artifacts import manifest, write_json
    from kilimanjaro.research.search import propose

    spec = Campaign.from_dict(campaign_spec(tmp_path)).spec
    outputs = []
    for db_name in ("one", "two"):
        with Store(tmp_path / f"{db_name}.sqlite") as store:
            run_id = "run-" + "4" * 32
            store.enqueue(
                run_id,
                "same",
                spec,
                {},
                [{"id": "report", "stage": "report", "payload": {}}],
            )
            job = store.claim("worker")
            path = tmp_path / db_name
            write_json(path / "report.json", {"results": {"candidates": []}})
            store.finish(job["id"], job["token"], path, manifest(path))
            proposals = propose(store, run_id)
            assert [p["lane"] for p in proposals["proposals"]] == [
                "exploration",
                "refinement",
                "exploration",
                "refinement",
                "replication",
            ]
            outputs.append(proposals)
            previous = {p["id"] for p in proposals["proposals"]}
            assert not previous & {p["id"] for p in propose(store, run_id)["proposals"]}
    assert outputs[0] == outputs[1]


def test_viewer_is_readonly_and_seals_confirmation(tmp_path, monkeypatch):
    from api import app

    path = tmp_path / "state.sqlite"
    with Store(path) as store:
        spec = campaign_spec(tmp_path)
        spec["_mode"] = "confirmation"
        store.enqueue("secret", "logical", spec, {}, [])
    monkeypatch.setitem(app.config, "RESEARCH_DATABASE", str(path))
    client = app.test_client()
    response = client.get("/api/research/secret")
    assert response.status_code == 200
    assert "campaign" not in response.json
    assert response.json["data_role"] == "sealed_confirmation"
    assert client.post("/api/research").status_code == 405
