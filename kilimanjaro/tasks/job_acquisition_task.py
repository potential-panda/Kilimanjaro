import threading
import time
from dataclasses import dataclass
from logging import getLogger
from typing import Any, Dict

import httpx

from .base import BaseTask
from .registry import get_task_registry

LOG = getLogger(__name__)
API_BASE_URL = "http://localhost:8766"


@dataclass
class Config:
    api_base_url: str = API_BASE_URL


def load_config():
    # TODO read {__class__.__name__}.json from current dir
    return Config()


class JobAcquisitionTask(BaseTask):
    """
    Task for acquiring and executing jobs from the API.

    This task:
    1. Acquires a job from the API
    2. Creates the appropriate task based on job type
    3. Runs the task in a thread
    4. Monitors progress in another thread
    """

    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.client = httpx.Client(base_url=self.config.api_base_url, timeout=30.0)

        self.work_task = None
        self.work_thread = None
        self.progress_thread = None
        self.stop_progress = threading.Event()
        self.is_complete = False
        self.is_running = False

    def _acquire_job(self) -> Dict[str, Any]:
        """Acquire a job from the API"""
        try:
            response = self.client.post("/job/acquire")
            if response.status_code == 200:
                job_data = response.json()
                if job_data:
                    LOG.info(f"Acquired job: {job_data.get('job_id')}")
                    return job_data
        except Exception as e:
            LOG.error(f"Error acquiring job: {e}")
        return None

    def _run_work_task(self, task: BaseTask):
        """Run the work task (with its own locking)"""
        try:
            task.run()  # task.run() handles locking internally
        except Exception as e:
            LOG.error(f"Error running work task: {e}")

    def _check_progress(self, task: BaseTask):
        """Periodically check task progress"""
        while not self.stop_progress.is_set():
            if task.is_running and hasattr(task, "check_progress"):
                try:
                    task.check_progress()
                except Exception as e:
                    LOG.error(f"Error checking progress: {e}")

            # Wait 60 seconds or until stop signal
            self.stop_progress.wait(timeout=60)

    def execute(self) -> None:
        """Execute the job acquisition task"""
        try:
            # Acquire a job
            job_data = self._acquire_job()
            if not job_data:
                LOG.info("No jobs available")
                self.mark_completed({"status": "no_jobs"})
                return

            self.work_job_id = job_data["job_id"]
            self.mark_started()

            task_type_str = job_data.get("task_type", "backtest")
            registry = get_task_registry()
            self.work_task = registry.create_task_from_string(
                task_type_str,
                self.work_job_id,
                job_data,
            )

            if not self.work_task:
                self.mark_failed("Failed to create task")
                return

            LOG.info(f"Starting job {self.work_job_id}")

            # Start work task in a thread
            self.work_thread = threading.Thread(
                target=self._run_work_task,
                args=(self.work_task,),
                daemon=True,
            )
            self.work_thread.start()

            # Start progress checking thread
            self.progress_thread = threading.Thread(
                target=self._check_progress,
                args=(self.work_task,),
                daemon=True,
            )
            self.progress_thread.start()

            # Wait for work task to complete
            self.work_thread.join()

            # Stop progress checking
            self.stop_progress.set()
            if self.progress_thread:
                self.progress_thread.join(timeout=5)

            LOG.info(f"Task completed {self.work_job_id}")
            self.mark_completed(
                {
                    "job_id": self.work_job_id,
                    "status": "completed",
                }
            )

        except Exception as e:
            LOG.error(f"Error in job acquisition task: {e}")
            self.mark_failed(str(e))

    def update_progress(self, update_data: Dict[str, Any]) -> None:
        if not self.client:
            return

        update_data["ts"] = time.time()
        try:
            response = self.client.post(f"/job/update/{self.work_job_id}", json=update_data)
            if response.status_code != 204:
                LOG.error(f"Failed to update job {self.work_job_id}: {response.status_code}")
        except Exception as e:
            LOG.error(f"Error updating job progress: {e}")

    def mark_started(self) -> None:
        LOG.error(f"Task started: job_id={self.work_job_id}")
        self.is_running = True
        self.update_progress({"progress": "started"})

    def mark_completed(self, result_data: Dict[str, Any]) -> None:
        LOG.error(f"Task completed: job_id={self.work_job_id}")
        self.is_running = False
        self.is_complete = True
        self.update_progress({"progress": "DONE", "result": result_data})

    def mark_failed(self, error: str) -> None:
        LOG.error(f"Task failed: job_id={self.work_job_id} error={error}")
        self.is_running = False
        self.is_complete = True
        self.update_progress({"progress": "ERROR", "result": {"error": error}})
