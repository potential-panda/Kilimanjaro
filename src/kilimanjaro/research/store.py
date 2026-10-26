"""Transactional local research state; artifacts and the audit ledger stay separate."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .artifacts import verify
from .identity import canonical, identity

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
 id TEXT PRIMARY KEY, logical_id TEXT NOT NULL, spec TEXT NOT NULL,
 provenance TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 stage TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
 attempt INTEGER NOT NULL DEFAULT 0, available REAL NOT NULL,
 token TEXT, owner TEXT, lease_until REAL, error TEXT,
 artifact_dir TEXT, manifest TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS dependencies (
 job_id TEXT NOT NULL REFERENCES jobs(id), parent_id TEXT NOT NULL REFERENCES jobs(id),
 PRIMARY KEY(job_id,parent_id));
CREATE TABLE IF NOT EXISTS attempts (
 job_id TEXT NOT NULL REFERENCES jobs(id), number INTEGER NOT NULL, token TEXT UNIQUE NOT NULL,
 started REAL NOT NULL, ended REAL, state TEXT NOT NULL, error TEXT,
 PRIMARY KEY(job_id,number));
CREATE TABLE IF NOT EXISTS admissions (
 campaign_id TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(campaign_id,day));
CREATE TABLE IF NOT EXISTS outbox (
 id TEXT PRIMARY KEY, payload TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS exposures (
 program TEXT NOT NULL, run_id TEXT NOT NULL, role TEXT NOT NULL,
 start REAL NOT NULL, end REAL NOT NULL, PRIMARY KEY(program,run_id,role,start,end));
CREATE TABLE IF NOT EXISTS trials (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, spec TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS programs (
 id TEXT PRIMARY KEY, alpha REAL NOT NULL, next_test INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS freezes (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, program TEXT NOT NULL,
 spec TEXT NOT NULL, alpha REAL NOT NULL, created REAL NOT NULL,
 state TEXT NOT NULL DEFAULT 'frozen', result TEXT);
CREATE TABLE IF NOT EXISTS predictions (
 candidate_id TEXT NOT NULL REFERENCES freezes(id), decision REAL NOT NULL,
 recorded REAL NOT NULL, maturity REAL NOT NULL, payload TEXT NOT NULL, outcome TEXT,
 PRIMARY KEY(candidate_id,decision));
CREATE TABLE IF NOT EXISTS certificates (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, evidence TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state,available,created);
CREATE INDEX IF NOT EXISTS exposure_lookup ON exposures(program,start,end);
CREATE TABLE IF NOT EXISTS proposals (
 id TEXT PRIMARY KEY, parent_run TEXT NOT NULL REFERENCES campaigns(id),
 lane TEXT NOT NULL, spec TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL,
 run_id TEXT REFERENCES campaigns(id));
CREATE TABLE IF NOT EXISTS service_ticks (
 id INTEGER PRIMARY KEY, recorded REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS service_runs (
 id TEXT PRIMARY KEY, started REAL NOT NULL, deadline REAL NOT NULL);
"""


class Store:
    def __init__(self, path: Path, *, readonly: bool = False, clock=time.time):
        self.path, self.clock = Path(path), clock
        if readonly:
            self.db = sqlite3.connect(
                f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=10
            )
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        if not readonly:
            self.db.execute("PRAGMA journal_mode=WAL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported research database version {version}")
            self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA user_version=1")
            self.db.commit()

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def campaign(self, run_id):
        row = self.db.execute(
            "SELECT * FROM campaigns WHERE id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown campaign run {run_id}")
        return {
            **dict(row),
            "spec": json.loads(row["spec"]),
            "provenance": json.loads(row["provenance"]),
        }

    def enqueue(
        self,
        run_id: str,
        logical_id: str,
        spec: dict,
        provenance: dict,
        jobs: list[dict],
    ):
        now = self.clock()
        with self.transaction() as db:
            old = db.execute(
                "SELECT spec, provenance FROM campaigns WHERE id=?", (run_id,)
            ).fetchone()
            if old and (
                old["spec"] != canonical(spec)
                or old["provenance"] != canonical(provenance)
            ):
                raise ValueError("Run identity conflict")
            db.execute(
                "INSERT OR IGNORE INTO campaigns VALUES (?,?,?,?,?)",
                (run_id, logical_id, canonical(spec), canonical(provenance), now),
            )
            from .validation import register_development

            register_development(db, run_id, spec, now)
            for job in jobs:
                db.execute(
                    "INSERT OR IGNORE INTO jobs(id,campaign_id,stage,payload,state,available,created) VALUES (?,?,?,?,?,?,?)",
                    (
                        job["id"],
                        run_id,
                        job["stage"],
                        canonical(job["payload"]),
                        job.get("state", "pending"),
                        now,
                        now,
                    ),
                )
            for job in jobs:
                for parent in job.get("depends", []):
                    db.execute(
                        "INSERT OR IGNORE INTO dependencies VALUES (?,?)",
                        (job["id"], parent),
                    )
            self._event(db, run_id, "enqueued", {"job_ids": [j["id"] for j in jobs]})

    def _event(self, db, run_id, kind, details):
        event_id = identity("evt-r", {"run": run_id, "kind": kind, "details": details})
        payload = {
            "event_id": event_id,
            "hypothesis_id": run_id,
            "event_type": "research_event",
            "actor_type": "agent",
            "actor_id": "research-worker",
            "created_at": datetime.fromtimestamp(self.clock(), UTC).isoformat(),
            "details": {"kind": kind, **details},
        }
        db.execute(
            "INSERT OR IGNORE INTO outbox(id,payload) VALUES (?,?)",
            (event_id, canonical(payload)),
        )

    def claim(self, owner: str, *, run_id=None, lease_seconds=60) -> dict | None:
        now = self.clock()
        day = datetime.fromtimestamp(now, UTC).date().isoformat()
        with self.transaction() as db:
            # Resource admission is serialized. One running job per database in v1.
            if db.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
                return None
            rows = db.execute(
                """SELECT j.*, c.spec AS campaign_spec FROM jobs j JOIN campaigns c ON c.id=j.campaign_id
                WHERE j.state IN ('pending','ready','retry_wait') AND j.available<=?
                AND (? IS NULL OR j.campaign_id=?)
                AND NOT EXISTS (SELECT 1 FROM dependencies d JOIN jobs p ON p.id=d.parent_id WHERE d.job_id=j.id AND p.state!='succeeded')
                ORDER BY j.created,j.rowid""",
                (now, run_id, run_id),
            ).fetchall()
            for row in rows:
                limits = json.loads(row["campaign_spec"])["budgets"]
                # Replanning data, changing a seed or creating a challenger does
                # not replenish the research program's admissions.
                program = json.loads(row["campaign_spec"])["program"]
                total, daily = db.execute(
                    """SELECT COALESCE(SUM(a.count),0),
                    COALESCE(SUM(CASE WHEN a.day=? THEN a.count ELSE 0 END),0)
                    FROM admissions a JOIN campaigns c ON c.id=a.campaign_id
                    WHERE json_extract(c.spec,'$.program')=?""",
                    (day, program),
                ).fetchone()
                if total >= limits["max_jobs"] or daily >= limits["daily_jobs"]:
                    continue
                token, number = uuid.uuid4().hex, row["attempt"] + 1
                db.execute(
                    "UPDATE jobs SET state='running',attempt=?,token=?,owner=?,lease_until=?,error=NULL WHERE id=?",
                    (number, token, owner, now + lease_seconds, row["id"]),
                )
                db.execute(
                    "INSERT INTO attempts(job_id,number,token,started,state) VALUES (?,?,?,?,'running')",
                    (row["id"], number, token, now),
                )
                db.execute(
                    "INSERT INTO admissions VALUES (?,?,1) ON CONFLICT(campaign_id,day) DO UPDATE SET count=count+1",
                    (row["campaign_id"], day),
                )
                self._event(
                    db,
                    row["campaign_id"],
                    "started",
                    {"job_id": row["id"], "attempt": number},
                )
                return self.job(row["id"])
        return None

    def job(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["manifest"] = (
            json.loads(result["manifest"]) if result["manifest"] else None
        )
        result["depends"] = [
            r[0]
            for r in self.db.execute(
                "SELECT parent_id FROM dependencies WHERE job_id=? ORDER BY parent_id",
                (job_id,),
            )
        ]
        return result

    def heartbeat(self, job_id, token, *, lease_seconds=60):
        with self.transaction() as db:
            result = db.execute(
                "UPDATE jobs SET lease_until=? WHERE id=? AND state='running' AND token=? AND lease_until>?",
                (self.clock() + lease_seconds, job_id, token, self.clock()),
            )
            if result.rowcount != 1:
                raise ValueError("Lost worker lease")

    def finish(self, job_id, token, folder: Path, files: dict):
        verify(folder, files)
        with self.transaction() as db:
            job = self._leased(db, job_id, token)
            db.execute(
                "UPDATE jobs SET state='succeeded',artifact_dir=?,manifest=?,lease_until=NULL WHERE id=?",
                (str(folder), canonical(files), job_id),
            )
            db.execute(
                "UPDATE attempts SET state='succeeded',ended=? WHERE token=?",
                (self.clock(), token),
            )
            self._event(
                db,
                job["campaign_id"],
                "succeeded",
                {"job_id": job_id, "attempt": job["attempt"], "artifacts": files},
            )

    def _leased(self, db, job_id, token):
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if (
            not job
            or job["state"] != "running"
            or job["token"] != token
            or job["lease_until"] <= self.clock()
        ):
            raise ValueError("Lost worker lease")
        return job

    def fail(self, job_id, token, error, *, retry=True, waiting=None):
        with self.transaction() as db:
            job = self._leased(db, job_id, token)
            limits = self.campaign(job["campaign_id"])["spec"]["budgets"]
            state = waiting or (
                "retry_wait"
                if retry and job["attempt"] < limits["max_attempts"]
                else "failed"
            )
            if state not in {
                "retry_wait",
                "failed",
                "waiting_data",
                "waiting_approval",
                "needs_tooling",
            }:
                raise ValueError("Invalid failure state")
            db.execute(
                "UPDATE jobs SET state=?,error=?,available=?,lease_until=NULL WHERE id=?",
                (
                    state,
                    str(error),
                    self.clock() + min(3600, 5 * 2 ** min(job["attempt"], 9)),
                    job_id,
                ),
            )
            db.execute(
                "UPDATE attempts SET state=?,ended=?,error=? WHERE token=?",
                (state, self.clock(), str(error), token),
            )
            self._event(
                db,
                job["campaign_id"],
                state,
                {"job_id": job_id, "attempt": job["attempt"], "error": str(error)},
            )

    def recover(self, dead_owner: str):
        """Caller must hold the process lock proving this owner is no longer executing."""
        with self.transaction() as db:
            jobs = db.execute(
                "SELECT * FROM jobs WHERE state='running' AND owner=? AND lease_until<=?",
                (dead_owner, self.clock()),
            ).fetchall()
            for job in jobs:
                limits = self.campaign(job["campaign_id"])["spec"]["budgets"]
                state = (
                    "retry_wait"
                    if job["attempt"] < limits["max_attempts"]
                    else "failed"
                )
                db.execute(
                    "UPDATE jobs SET state=?,available=?,error='worker interrupted',lease_until=NULL WHERE id=?",
                    (state, self.clock(), job["id"]),
                )
                db.execute(
                    "UPDATE attempts SET state='interrupted',ended=? WHERE token=?",
                    (self.clock(), job["token"]),
                )
                self._event(
                    db,
                    job["campaign_id"],
                    "recovered",
                    {"job_id": job["id"], "attempt": job["attempt"]},
                )
        return len(jobs)

    def retry(self, job_id):
        with self.transaction() as db:
            job = self.job(job_id)
            if job["state"] not in {
                "failed",
                "waiting_data",
                "needs_tooling",
                "retry_wait",
            }:
                raise ValueError(
                    "Only failed/waiting jobs can be retried; approvals use their own path"
                )
            db.execute(
                "UPDATE jobs SET state='ready',available=? WHERE id=?",
                (self.clock(), job_id),
            )
            self._event(
                db,
                job["campaign_id"],
                "retry_requested",
                {"job_id": job_id, "after_attempt": job["attempt"]},
            )

    def publish_events(self, ledger_path: Path):
        from kilimanjaro.ledger.core import append_event

        for row in self.db.execute(
            "SELECT id,payload FROM outbox WHERE published=0 ORDER BY rowid"
        ).fetchall():
            append_event(**json.loads(row["payload"]), ledger_path=ledger_path)
            with self.transaction() as db:
                db.execute("UPDATE outbox SET published=1 WHERE id=?", (row["id"],))

    def status(self, run_id=None):
        jobs = [
            dict(r)
            for r in self.db.execute(
                "SELECT id,campaign_id,stage,state,attempt,error,artifact_dir FROM jobs WHERE (? IS NULL OR campaign_id=?) ORDER BY created,rowid",
                (run_id, run_id),
            )
        ]
        return {
            "jobs": jobs,
            "admissions": [
                dict(r)
                for r in self.db.execute(
                    "SELECT * FROM admissions WHERE (? IS NULL OR campaign_id=?)",
                    (run_id, run_id),
                )
            ],
            "unpublished_events": self.db.execute(
                "SELECT count(*) FROM outbox WHERE published=0"
            ).fetchone()[0],
            "trial_count": self.db.execute(
                "SELECT count(*) FROM trials WHERE (? IS NULL OR campaign_id=?)",
                (run_id, run_id),
            ).fetchone()[0],
            "candidates": [
                dict(r)
                for r in self.db.execute(
                    "SELECT id,campaign_id,state,alpha,created FROM freezes WHERE (? IS NULL OR campaign_id=?)",
                    (run_id, run_id),
                )
            ],
            "proposal_count": self.db.execute(
                "SELECT count(*) FROM proposals"
            ).fetchone()[0],
        }

    def backup(self, destination: Path):
        if Path(destination).exists():
            raise ValueError("Backup destination already exists")
        with sqlite3.connect(destination) as target:
            self.db.backup(target)
