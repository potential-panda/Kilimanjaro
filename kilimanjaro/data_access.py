import json
import threading
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class JobStatus(Enum):
    PENDING = "pending"
    ACQUIRED = "acquired"
    DONE = "DONE"
    ERROR = "ERROR"

    @property
    def file_extension(self):
        if self == JobStatus.PENDING:
            return ".json"
        elif self == JobStatus.ACQUIRED:
            return ".json.acquired"
        elif self == JobStatus.DONE:
            return ".json.done"
        elif self == JobStatus.ERROR:
            return ".json.error"
        return "NA"


class JobDataAccess:
    def __init__(self, job_dir: str, results_dir: str = None):
        self.job_dir = Path(job_dir)
        self.job_dir.mkdir(parents=True, exist_ok=True)

        # Use provided results_dir or derive from job_dir (for backward compatibility)
        if results_dir:
            self.results_dir = Path(results_dir)
        else:
            self.results_dir = self.job_dir.parent / "results"
        self.results_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()

    def create_job(self, job_data: Dict[str, Any]) -> str:
        with self._lock:
            job_id = str(uuid.uuid4())
            job_data["job_id"] = job_id
            job_data["updates"] = []
            job_file = self.job_dir / f"{job_id}{JobStatus.PENDING.file_extension}"
            with open(job_file, "w") as f:
                json.dump(job_data, f, indent=2)

            return job_id

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            target_file = self._get_job_file(job_id)
            if not target_file:
                return False
            target_file.unlink()
            return True

    def acquire_job(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            available_jobs = list(self.job_dir.glob("*.json"))
            if not available_jobs:
                return None

            oldest_job = min(available_jobs, key=lambda f: f.stat().st_mtime)
            job_id = oldest_job.stem
            with open(oldest_job, "r") as f:
                job_data = json.load(f)
            acquired_file = self.job_dir / f"{job_id}{JobStatus.ACQUIRED.file_extension}"
            oldest_job.rename(acquired_file)
            return job_data

    def _get_status(self, job_data) -> str:
        updates = job_data.get("updates")
        if not updates:
            return JobStatus.PENDING.value

        last_update = job_data["updates"][-1]
        progress = last_update.get("progress")
        if progress == JobStatus.DONE.value:
            return JobStatus.DONE.value.lower()
        elif progress == JobStatus.ERROR.value:
            return JobStatus.ERROR.value.lower()
        else:
            return progress

    def list_jobs(self) -> List[Dict[str, Any]]:
        """List all job files with basic info"""
        with self._lock:
            jobs = []
            job_files = list(self.job_dir.glob("*.json*"))

            for job_file in job_files:
                with open(job_file, "r") as f:
                    job_data = json.load(f)
                status = self._get_status(job_data)
                jobs.append(
                    {
                        "job_id": job_data.get("job_id"),
                        "status": status,
                        "created_time": job_file.stat().st_ctime,
                        "file_name": job_file.name,
                    }
                )

            return sorted(jobs, key=lambda j: j["created_time"])

    def update_job(self, job_id: str, update_data: Dict[str, Any]) -> bool:
        """Update job with progress/status information"""
        with self._lock:
            target_file = self.job_dir / f"{job_id}{JobStatus.ACQUIRED.file_extension}"
            if not target_file.exists():
                return False

            with open(target_file, "r") as f:
                job_data = json.load(f)
            job_data["updates"].append(update_data)
            with open(target_file, "w") as f:
                json.dump(job_data, f, indent=2)

            progress = update_data.get("progress")
            if progress in [JobStatus.ERROR.value, JobStatus.DONE.value]:
                # If job is done and has run_logs, save them as individual files
                if progress == JobStatus.DONE.value and update_data.get("result"):
                    self._save_run_logs_from_result(update_data["result"])

                status = JobStatus[progress]
                done_file = self.job_dir / f"{job_id}{status.file_extension}"
                target_file.rename(done_file)
            return True

    def _save_run_logs_from_result(self, result_data: Dict[str, Any]):
        """Extract run_logs from result and save as individual files"""
        run_logs = result_data.get("run_logs", [])
        if not run_logs:
            return

        for run_log in run_logs:
            bucket_name = run_log.get("bucket_name")
            if bucket_name:
                try:
                    result_file_path = self.results_dir / f"{bucket_name}.json"
                    with open(result_file_path, "w") as f:
                        json.dump(run_log, f, indent=2)
                except Exception as e:
                    print(f"Error saving run log {bucket_name}: {e}")

    def _get_job_file(self, job_id: str) -> Optional[Path]:
        for file_path in [self.job_dir / f"{job_id}{s.file_extension}" for s in list(JobStatus)]:
            if file_path.exists():
                return file_path
        return None

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            target_file = self._get_job_file(job_id)
            if not target_file:
                return None
            try:
                with open(target_file, "r") as f:
                    return json.load(f)
            except Exception:
                return None

    def get_all_run_logs(self) -> List[Dict[str, Any]]:
        """Read all run_logs from individual result JSON files"""
        with self._lock:
            run_logs = []

            if not self.results_dir.exists():
                return run_logs

            # Get all JSON files in results directory
            result_files = list(self.results_dir.glob("*.json"))

            for result_file in result_files:
                try:
                    with open(result_file, "r") as f:
                        run_log_data = json.load(f)
                    run_logs.append(run_log_data)
                except Exception as e:
                    # Skip files that can't be read or parsed
                    print(f"Error reading result file {result_file}: {e}")
                    continue

            # Sort by start_ts if available, newest first
            run_logs.sort(key=lambda x: x.get("start_ts", 0), reverse=True)
            return run_logs

    def delete_result(self, bucket_name: str) -> bool:
        """Delete individual result file by bucket name"""
        with self._lock:
            result_file_path = self.results_dir / f"{bucket_name}.json"
            if result_file_path.exists():
                try:
                    result_file_path.unlink()
                    return True
                except Exception as e:
                    print(f"Error deleting result file {bucket_name}: {e}")
                    return False
            return False
