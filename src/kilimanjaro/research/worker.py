"""Supervised single-host worker with leased jobs and bounded subprocesses."""

from __future__ import annotations

import argparse
import fcntl
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

from .artifacts import manifest, read_json, verify, write_json
from .identity import implementation
from .store import Store

OWNER = "local-worker"


def resident_bytes(pid):
    """Account for provider child processes as well as the stage interpreter."""
    pending = [pid]
    total = 0
    while pending:
        current = pending.pop()
        try:
            status = Path(f"/proc/{current}/status").read_text()
            total += (
                int(
                    next(
                        line.split()[1]
                        for line in status.splitlines()
                        if line.startswith("VmRSS:")
                    )
                )
                * 1024
            )
            pending.extend(
                int(p)
                for p in Path(f"/proc/{current}/task/{current}/children")
                .read_text()
                .split()
            )
        except (FileNotFoundError, ProcessLookupError, StopIteration):
            pass
    return total


def disk_bytes(root: Path) -> int:
    seen = set()
    total = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        stat = path.stat()
        inode = (stat.st_dev, stat.st_ino)
        if inode not in seen:
            total += stat.st_size
            seen.add(inode)
    return total


def stop_process(process):
    if process.poll() is not None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def execute_child(request_file: Path) -> int:
    request = read_json(request_file)
    limits = request["spec"]["budgets"]
    # Native allocators reserve virtual arenas far beyond their resident usage.
    # RLIMIT_AS can crash them; the parent monitors RSS and reaps the job instead.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (limits["disk_mb"] * 1024**2, limits["disk_mb"] * 1024**2),
    )
    resource.setrlimit(
        resource.RLIMIT_CPU,
        (limits["job_timeout_seconds"], limits["job_timeout_seconds"] + 1),
    )
    from .stages import execute

    try:
        execute(
            request["stage"],
            request["spec"],
            request["payload"],
            [Path(p) for p in request["parents"]],
            request["inputs"],
            Path(request["output"]),
            request["run_id"],
        )
    except Exception as exc:  # noqa: BLE001 - isolate all provider failures in the child
        import traceback

        traceback.print_exc()
        write_json(
            request_file.parent / "failure.json",
            {"type": type(exc).__name__, "message": str(exc)},
        )
        return 1
    return 0


def run(
    database: Path,
    *,
    run_id=None,
    max_jobs=10,
    max_minutes=30,
    stop_requested=lambda: False,
) -> dict:
    if max_jobs <= 0 or max_minutes <= 0:
        raise ValueError("Worker requires positive job and time bounds")
    deadline = time.monotonic() + max_minutes * 60
    completed = []
    lock_path = Path(database).parent / "research-worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock, Store(database) as store:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"reason": "worker_already_running", "attempts": []}
        store.recover(OWNER)
        # Replay publication after a crash between artifact completion and the
        # confirmation-state transition. The result and its alpha are immutable.
        from .confirmation import complete

        for item in store.status(run_id)["jobs"]:
            if item["stage"] == "confirm" and item["state"] == "succeeded":
                complete(store, item["campaign_id"], item["artifact_dir"])
        while (
            len(completed) < max_jobs
            and time.monotonic() < deadline
            and not stop_requested()
        ):
            # Check the code before spending an admission, never run changed code
            # under an older immutable run identity.
            candidates = (
                [store.campaign(run_id)]
                if run_id
                else [
                    store.campaign(row[0])
                    for row in store.db.execute(
                        "SELECT DISTINCT campaign_id FROM jobs WHERE state IN ('pending','ready','retry_wait')"
                    )
                ]
            )
            current = implementation()
            for campaign in candidates:
                if campaign["provenance"] != current:
                    with store.transaction() as db:
                        db.execute(
                            "UPDATE jobs SET state='needs_tooling',error='Implementation changed; resolve a new run' WHERE campaign_id=? AND state IN ('pending','ready','retry_wait')",
                            (campaign["id"],),
                        )
                        store._event(db, campaign["id"], "implementation_changed", {})
                if (
                    disk_bytes(
                        Path(campaign["spec"]["research_root"]) / "05_experiments"
                    )
                    > campaign["spec"]["budgets"]["disk_mb"] * 1024**2
                ):
                    with store.transaction() as db:
                        db.execute(
                            "UPDATE jobs SET state='needs_tooling',error='Research disk budget exhausted' WHERE campaign_id=? AND state IN ('pending','ready','retry_wait')",
                            (campaign["id"],),
                        )
                        store._event(db, campaign["id"], "disk_budget_exhausted", {})
            job = store.claim(OWNER, run_id=run_id)
            if job is None:
                break
            campaign = store.campaign(job["campaign_id"])
            spec = campaign["spec"]
            root = Path(spec["research_root"])
            run_dir = root / "05_experiments" / job["campaign_id"]
            attempt = run_dir / "attempts" / job["id"] / job["token"]
            output = attempt / "artifacts"
            process = None
            peak_rss = 0
            started = time.monotonic()
            try:
                inputs = {}
                for info in store.status(job["campaign_id"])["jobs"]:
                    if info["state"] == "succeeded":
                        inputs[info["stage"]] = info["artifact_dir"]
                parents = []
                for parent_id in job["depends"]:
                    parent = store.job(parent_id)
                    folder = Path(parent["artifact_dir"])
                    verify(folder, parent["manifest"])
                    parents.append(str(folder))
                request = {
                    "spec": spec,
                    "stage": job["stage"],
                    "payload": job["payload"],
                    "parents": parents,
                    "inputs": inputs,
                    "output": str(output),
                    "run_id": job["campaign_id"],
                }
                write_json(attempt / "request.json", request)
                env = {
                    **os.environ,
                    "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                    "OPENBLAS_NUM_THREADS": "1",
                    "OMP_NUM_THREADS": "1",
                    "MKL_NUM_THREADS": "1",
                    "KILIMANJARO_QUERY_MEMORY_MB": str(
                        max(64, spec["budgets"]["memory_mb"] // 2)
                    ),
                }
                job_deadline = min(
                    deadline, time.monotonic() + spec["budgets"]["job_timeout_seconds"]
                )
                heartbeat = time.monotonic()
                with (attempt / "worker.log").open("w") as log:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "kilimanjaro.research.worker",
                            "--execute",
                            str(attempt / "request.json"),
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        env=env,
                        start_new_session=True,
                        pass_fds=(lock.fileno(),),
                    )
                    while process.poll() is None:
                        rss = resident_bytes(process.pid)
                        peak_rss = max(peak_rss, rss)
                        if rss > spec["budgets"]["memory_mb"] * 1024**2:
                            raise MemoryError("Worker resident memory budget exceeded")
                        if time.monotonic() >= job_deadline or stop_requested():
                            raise TimeoutError("Worker deadline or stop request")
                        if time.monotonic() - heartbeat >= 5:
                            store.heartbeat(job["id"], job["token"])
                            heartbeat = time.monotonic()
                            if (
                                disk_bytes(run_dir)
                                > spec["budgets"]["disk_mb"] * 1024**2
                            ):
                                raise OSError("Campaign disk budget exhausted")
                        time.sleep(0.1)
                if process.returncode:
                    failure = (
                        read_json(attempt / "failure.json")
                        if (attempt / "failure.json").exists()
                        else {
                            "type": "WorkerFailure",
                            "message": f"child exit {process.returncode}; see {attempt / 'worker.log'}",
                        }
                    )
                    waiting = {
                        "DataUnavailable": "waiting_data",
                        "NeedsTooling": "needs_tooling",
                    }.get(failure["type"])
                    store.fail(
                        job["id"], job["token"], failure["message"], waiting=waiting
                    )
                else:
                    if (
                        disk_bytes(root / "05_experiments")
                        > spec["budgets"]["disk_mb"] * 1024**2
                    ):
                        raise OSError("Research artifact disk budget exhausted")
                    files = manifest(output)
                    write_json(output / "manifest.json", files)
                    store.finish(job["id"], job["token"], output, files)
                    if job["stage"] == "confirm":
                        complete(store, job["campaign_id"], output)
            except Exception as exc:
                if process is not None:
                    stop_process(process)
                if store.job(job["id"])["state"] == "running":
                    store.fail(job["id"], job["token"], f"{type(exc).__name__}: {exc}")
                else:
                    raise  # Publication is replayed on restart; never fail a completed job.
            finally:
                if process is not None:
                    stop_process(process)
                with store.transaction() as db:
                    store._event(
                        db,
                        job["campaign_id"],
                        "resource_usage",
                        {
                            "job_id": job["id"],
                            "attempt": job["attempt"],
                            "peak_rss_bytes": peak_rss,
                            "elapsed_seconds": time.monotonic() - started,
                        },
                    )
                store.publish_events(root / "17_ledger" / "events.jsonl")
            completed.append(
                {"job_id": job["id"], "state": store.job(job["id"])["state"]}
            )
        return {"attempts": completed, "status": store.status(run_id)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", type=Path, required=True)
    sys.exit(execute_child(parser.parse_args().execute))
