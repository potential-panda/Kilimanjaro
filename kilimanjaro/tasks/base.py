from abc import ABC, abstractmethod
from logging import getLogger
from typing import Any, Dict

import httpx

LOG = getLogger(__name__)


class BaseTask(ABC):
    """Base class for all agent tasks"""

    def __init__(self, job_id: str, job_data: Dict[str, Any], client: httpx.Client):
        self.job_id = job_id
        self.job_data = job_data
        self.client = client
        self.is_running = False
        self.is_complete = False

    @abstractmethod
    def execute(self) -> None:
        """Execute the task. This method should be implemented by subclasses."""
        pass

    @abstractmethod
    def get_task_type(self) -> str:
        """Return the task type identifier"""
        pass

    def update_progress(self, update_data: Dict[str, Any]) -> None:
        """Update job progress via API"""
        import time

        update_data["ts"] = time.time()
        try:
            response = self.client.post(f"/job/update/{self.job_id}", json=update_data)
            if response.status_code != 204:
                LOG.error(f"Failed to update job {self.job_id}: {response.status_code}")
        except Exception as e:
            LOG.error(f"Error updating job progress: {e}")

    def mark_started(self) -> None:
        """Mark task as started"""
        self.is_running = True
        self.update_progress({"progress": "started"})

    def mark_completed(self, result_data: Dict[str, Any]) -> None:
        """Mark task as completed with result data"""
        self.is_running = False
        self.is_complete = True
        self.update_progress({"progress": "DONE", "result": result_data})

    def mark_failed(self, error: str) -> None:
        """Mark task as failed with error message"""
        self.is_running = False
        self.is_complete = True
        self.update_progress({"progress": "ERROR", "result": {"error": error}})

    def cleanup(self) -> None:
        """Clean up resources after task completion. Override if needed."""
        pass
