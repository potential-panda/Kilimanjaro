import time
from dataclasses import dataclass
from logging import getLogger
from typing import Any, Dict

import httpx
from qate.env.env_name import EnvName

from ..backtest_runner import run_backtest
from ..data_access import JobDataAccess
from ..task import Task

LOG = getLogger(__name__)
API_BASE_URL = "http://localhost:8766"
DEFAULT_ENV_NAME = EnvName.BENJAMIN_TATUM


@dataclass
class Config:
    api_base_url: str = API_BASE_URL


def load_config():
    # TODO read {__class__.__name__}.json from current dir
    return Config()


class JobRunner:
    def __init__(self, job_data):
        self.job_data = job_data

    def _get_env_name(self) -> EnvName:
        try:
            return EnvName[self.job_data["env_name"]]
        except Exception:
            return DEFAULT_ENV_NAME

    def run(self) -> None:
        try:
            LOG.info(self.job_data)
            self.job_id = self.job_data["job_id"]
            env_name = self._get_env_name()

            self.mark_started()
            run_logs = run_backtest(
                env_name,
                self.job_data,
                self.job_data["config"],
                self.job_data["param_grid"],
            )
            self.mark_completed({"run_logs": run_logs})
        except Exception as e:
            LOG.exception(e)
            self.mark_failed(str(e))

    def mark_started(self) -> None:
        LOG.info(f"Task started: job_id={self.job_id}")
        self.update_progress({"progress": "STARTED"})

    def mark_completed(self, results: Dict[str, Any]) -> None:
        LOG.info(f"Task completed: job_id={self.job_id}")
        self.update_progress({"progress": "DONE", "result": results})

    def mark_failed(self, error: str) -> None:
        LOG.error(f"Task failed: job_id={self.job_id}")
        self.update_progress({"progress": "ERROR", "result": {"error": error}})

    def update_progress(self, update_data: Dict[str, Any]) -> None:
        pass


class LocalJobRunner(JobRunner):
    def __init__(self, job_data, job_data_access):
        super().__init__(job_data)
        self.job_data_access = job_data_access

    def update_progress(self, update_data):
        self.job_data_access.update_job(self.job_id, update_data)


class ApiJobRunner(JobRunner):
    def __init__(self, job_data, client):
        super().__init__(job_data)
        self.client = client

    def update_progress(self, update_data: Dict[str, Any]) -> None:
        update_data["ts"] = time.time()
        try:
            response = self.client.post(f"/job/update/{self.job_id}", json=update_data)
            if response.status_code != 204:
                LOG.error(f"Failed to update job {self.job_id}: {response.status_code}")
        except Exception as e:
            LOG.exception(e)


class JobAcquisitionTask(Task):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.client = httpx.Client(base_url=self.config.api_base_url, timeout=30.0)
        self.job_data_access = JobDataAccess("jobs", "results")

    def _acquire_api_job(self) -> JobRunner:
        """Try to acquire a job from the API"""
        try:
            response = self.client.post("/job/acquire")
            if response.status_code == 200:
                job_data = response.json()
                if job_data:
                    LOG.info(f"Acquired API job: {job_data.get('job_id')}")
                    return ApiJobRunner(job_data, self.client)
        except Exception as e:
            LOG.warning(f"Failed to acquire API job: {e}")
        return None

    def _acquire_local_job(self) -> JobRunner:
        """Try to acquire a job from local files"""
        job_data = self.job_data_access.acquire_job()
        if job_data:
            LOG.info(f"Acquired local job: {job_data.get('job_id')}")
            return LocalJobRunner(job_data, self.job_data_access)
        return None

    def execute(self) -> None:
        # Try API first
        job_runner = self._acquire_api_job()

        # Fall back to local jobs if API has no jobs
        if not job_runner:
            job_runner = self._acquire_local_job()

        # Execute the job if one was acquired
        if not job_runner:
            return
        job_runner.run()
