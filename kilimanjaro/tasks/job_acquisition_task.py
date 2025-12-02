import time
from dataclasses import dataclass
from logging import getLogger
from typing import Any, Dict

import httpx
from qate.env.env_name import EnvName

from .backtest_runner import run_backtest
from .base import BaseTask

LOG = getLogger(__name__)
API_BASE_URL = "http://localhost:8766"
DEFAULT_ENV_NAME = EnvName.QIN_TENGFEI


@dataclass
class Config:
    api_base_url: str = API_BASE_URL


def load_config():
    # TODO read {__class__.__name__}.json from current dir
    return Config()


class JobAcquisitionTask(BaseTask):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.client = httpx.Client(base_url=self.config.api_base_url, timeout=30.0)

    def _acquire_job(self) -> Dict[str, Any]:
        response = self.client.post("/job/acquire")
        if response.status_code == 200:
            job_data = response.json()
            if job_data:
                LOG.info(f"Acquired job: {job_data.get('job_id')}")
                return job_data
        return None

    def execute(self) -> None:
        try:
            job_data = self._acquire_job()
            if not job_data:
                return

            LOG.info(job_data)
            self.job_id = job_data["job_id"]
            self.mark_started()
            run_logs = run_backtest(DEFAULT_ENV_NAME, job_data, job_data["config"], job_data["param_grid"])
            self.mark_completed({"run_logs": run_logs})
        except Exception as e:
            LOG.exception(e)
            self.mark_failed(str(e))

    def update_progress(self, update_data: Dict[str, Any]) -> None:
        update_data["ts"] = time.time()
        try:
            response = self.client.post(f"/job/update/{self.job_id}", json=update_data)
            if response.status_code != 204:
                LOG.error(f"Failed to update job {self.job_id}: {response.status_code}")
        except Exception as e:
            LOG.exception(e)

    def mark_started(self) -> None:
        LOG.info(f"Task started: job_id={self.job_id}")
        self.update_progress({"progress": "STARTED"})

    def mark_completed(self, results: Dict[str, Any]) -> None:
        LOG.info(f"Task completed: job_id={self.job_id}")
        self.update_progress({"progress": "DONE", "result": results})

    def mark_failed(self, error: str) -> None:
        LOG.error(f"Task failed: job_id={self.job_id}")
        self.update_progress({"progress": "ERROR", "result": {"error": error}})
