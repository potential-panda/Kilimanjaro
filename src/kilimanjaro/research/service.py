"""Opt-in polling service. Fixed budgets, cheap idle polls and restartable state."""

from __future__ import annotations

import copy
import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .artifacts import write_json
from .contracts import Campaign, instant
from .identity import canonical, identity, implementation
from .planner import enqueue, plan
from .store import Store
from .worker import run


def load_config(path):
    config = yaml.safe_load(Path(path).read_text())
    allowed = {
        "schema_version",
        "campaigns",
        "poll_seconds",
        "max_hours",
        "max_jobs_per_tick",
        "max_minutes_per_tick",
        "adaptive",
        "max_proposals",
        "rolling",
        "audit_every_polls",
    }
    if (
        not isinstance(config, dict)
        or set(config) - allowed
        or config.get("schema_version") != 1
        or not config.get("campaigns")
    ):
        raise ValueError("Invalid service config")
    defaults = {
        "poll_seconds": 60,
        "max_hours": 72,
        "max_jobs_per_tick": 10,
        "max_minutes_per_tick": 10,
        "adaptive": False,
        "max_proposals": 20,
        "rolling": False,
        "audit_every_polls": 60,
    }
    for key, value in defaults.items():
        config.setdefault(key, value)
    for key in (
        "poll_seconds",
        "max_hours",
        "max_jobs_per_tick",
        "max_minutes_per_tick",
        "max_proposals",
        "audit_every_polls",
    ):
        if (
            isinstance(config[key], bool)
            or not isinstance(config[key], (int, float))
            or config[key] <= 0
        ):
            raise ValueError(f"Service {key} must be positive")
    for key in ("max_jobs_per_tick", "max_proposals", "audit_every_polls"):
        if type(config[key]) is not int:
            raise ValueError(f"Service {key} must be an integer")
    for key in ("adaptive", "rolling"):
        if type(config[key]) is not bool:
            raise ValueError(f"Service {key} must be a boolean")
    if config["poll_seconds"] < 1:
        raise ValueError("Poll interval must be at least one second")
    config["campaigns"] = [
        str((Path(path).parent / str(p)).resolve()) for p in config["campaigns"]
    ]
    return config


def rolling_campaign(campaign, now):
    """Advance whole development windows to the most recent closed JST day."""
    from kilimanjaro.data.catalog import JST

    spec = copy.deepcopy(campaign.spec)
    end = datetime.fromtimestamp(now, JST).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    shift = end.timestamp() - instant(spec["windows"]["validation"]["end"])
    for window in spec["windows"].values():
        for key in ("start", "end"):
            window[key] = datetime.fromtimestamp(
                instant(window[key]) + shift, UTC
            ).isoformat()
    return Campaign.from_dict(spec)


def fingerprint(root, paths):
    entries = {}
    for relative in sorted(paths):
        try:
            stat = (Path(root) / relative).stat()
            entries[relative] = [stat.st_size, stat.st_mtime_ns, stat.st_ino]
        except FileNotFoundError:
            entries[relative] = None
    return identity("inventory", entries)


def summary(store):
    status = store.status()
    states = Counter(j["state"] for j in status["jobs"])
    attempts = store.db.execute(
        "SELECT count(*),COALESCE(SUM(ended-started),0) FROM attempts"
    ).fetchone()
    return {
        "recorded": store.clock(),
        "jobs": dict(states),
        "attempts": attempts[0],
        "completed_attempt_seconds": attempts[1],
        "trial_count": status["trial_count"],
        "admissions": status["admissions"],
        "candidates": status["candidates"],
        "failures": [j for j in status["jobs"] if j["error"]],
        "proposal_count": status["proposal_count"],
        "unpublished_events": status["unpublished_events"],
        "calibration": [
            {
                "id": r["id"],
                "state": json.loads(r["evidence"])["state"],
                "kind": json.loads(r["evidence"])["kind"],
            }
            for r in store.db.execute("SELECT * FROM certificates")
        ],
        "predictions": store.db.execute("SELECT count(*) FROM predictions").fetchone()[
            0
        ],
    }


def tick(config, *, stop_requested=lambda: False):
    campaigns = [Campaign.load(Path(p)) for p in config["campaigns"]]
    if len({str(c.database) for c in campaigns}) != 1:
        raise ValueError("One service must use one research database")
    database = campaigns[0].database
    changed = []
    with Store(database) as store:
        last = store.db.execute(
            "SELECT payload FROM service_ticks ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prior = json.loads(last[0]) if last else {}
        inventory = {}
        errors = []
        number = prior.get("number", 0) + 1
        code = implementation()["source_hash"]
        for base in campaigns:
            campaign = (
                rolling_campaign(base, store.clock()) if config["rolling"] else base
            )
            previous = prior.get("inventory", {}).get(base.spec["name"], {})
            stamp = fingerprint(campaign.data_root, previous.get("paths", []))
            if (
                previous.get("logical_id") == campaign.logical_id
                and previous.get("code") == code
                and previous.get("stamp") == stamp
                and number % config["audit_every_polls"]
            ):
                inventory[base.spec["name"]] = previous
                continue
            try:
                resolved = plan(campaign)
                enqueue(campaign, resolved)
                paths = list(resolved["data"]["nodes"])
                inventory[base.spec["name"]] = {
                    "logical_id": campaign.logical_id,
                    "code": code,
                    "stamp": fingerprint(campaign.data_root, paths),
                    "paths": paths,
                    "run_id": resolved["run_id"],
                    "waiting": resolved["data"]["waiting"],
                }
                changed.append(resolved["run_id"])
            except (ValueError, OSError) as exc:
                errors.append({"campaign": base.spec["name"], "error": str(exc)})
        from .confirmation import enqueue_confirmation

        for item in store.db.execute(
            "SELECT id FROM freezes WHERE state='frozen'"
        ).fetchall():
            try:
                enqueue_confirmation(store, item[0])
            except (ValueError, OSError) as exc:
                errors.append({"freeze_id": item[0], "error": str(exc)})
        if config["adaptive"]:
            from .search import enqueue_proposal, propose

            count = store.db.execute("SELECT count(*) FROM proposals").fetchone()[0]
            for row in store.db.execute(
                "SELECT campaign_id FROM jobs WHERE stage='report' AND state='succeeded' ORDER BY created"
            ).fetchall():
                if store.campaign(row[0])["provenance"].get("source_hash") != code:
                    continue
                remaining = config["max_proposals"] - count
                if remaining <= 0:
                    break
                response = propose(store, row[0], limit=min(5, remaining))
                count += len(response["proposals"])
            # Retry publication after interruption; each proposal has one run id.
            for row in store.db.execute(
                "SELECT id FROM proposals WHERE run_id IS NULL"
            ).fetchall():
                try:
                    enqueue_proposal(store, row[0])
                except (ValueError, OSError) as exc:
                    errors.append({"proposal_id": row[0], "error": str(exc)})
    work = run(
        database,
        max_jobs=config["max_jobs_per_tick"],
        max_minutes=config["max_minutes_per_tick"],
        stop_requested=stop_requested,
    )
    with Store(database) as store:
        store.publish_events(campaigns[0].root / "17_ledger" / "events.jsonl")
        record = {
            "number": number,
            "inventory": inventory,
            "changed_runs": changed,
            "errors": errors,
            "attempts": work["attempts"],
            "summary": summary(store),
        }
        with store.transaction() as db:
            db.execute(
                "INSERT INTO service_ticks(recorded,payload) VALUES (?,?)",
                (store.clock(), canonical(record)),
            )
        stamp = datetime.fromtimestamp(store.clock(), UTC).strftime("%Y%m%dT%H%M%S")
        write_json(
            campaigns[0].root / "05_experiments" / "service" / f"{stamp}-{number}.json",
            record,
        )
        return record


def serve(config, *, stop_requested=lambda: False, once=False):
    import fcntl

    campaign = Campaign.load(Path(config["campaigns"][0]))
    campaign.database.parent.mkdir(parents=True, exist_ok=True)
    with (campaign.database.parent / "research-service.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"state": "service_already_running", "ticks": 0}
        return _serve(config, stop_requested=stop_requested, once=once)


def _serve(config, *, stop_requested, once):
    remaining = config["max_hours"] * 3600
    if not once:
        campaign = Campaign.load(Path(config["campaigns"][0]))
        session_id = identity("service", config)
        with Store(campaign.database) as store, store.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO service_runs VALUES (?,?,?)",
                (session_id, store.clock(), store.clock() + remaining),
            )
            remaining = max(
                0,
                db.execute(
                    "SELECT deadline FROM service_runs WHERE id=?", (session_id,)
                ).fetchone()[0]
                - store.clock(),
            )
    deadline = time.monotonic() + remaining
    ticks = 0
    last = None
    while time.monotonic() < deadline and not stop_requested():
        last = tick(
            config,
            stop_requested=lambda: stop_requested() or time.monotonic() >= deadline,
        )
        ticks += 1
        if once:
            break
        # Short sleeps keep shutdown responsive and consume negligible idle CPU.
        next_poll = min(deadline, time.monotonic() + config["poll_seconds"])
        while time.monotonic() < next_poll and not stop_requested():
            time.sleep(min(1, max(0, next_poll - time.monotonic())))
    return {"ticks": ticks, "last": last, "state": "stopped"}
