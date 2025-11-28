from logging import getLogger
from typing import Any, Dict, Optional, Type

import httpx

from .base import BaseTask
from .backtest_task import BacktestTask
from .market_data_task import MarketDataTask

LOG = getLogger(__name__)


class TaskRegistry:
    """Registry for managing task types"""

    def __init__(self):
        self._tasks: Dict[str, Type[BaseTask]] = {}
        self._register_default_tasks()

    def _register_default_tasks(self) -> None:
        """Register default task types"""
        self.register(BacktestTask.TASK_TYPE, BacktestTask)
        self.register(MarketDataTask.TASK_TYPE, MarketDataTask)

    def register(self, task_type: str, task_class: Type[BaseTask]) -> None:
        """Register a new task type

        Args:
            task_type: Unique identifier for the task type
            task_class: Task class that inherits from BaseTask
        """
        if task_type in self._tasks:
            LOG.warning(f"Task type '{task_type}' is already registered, overwriting")
        self._tasks[task_type] = task_class
        LOG.info(f"Registered task type: {task_type}")

    def create_task(
        self,
        task_type: str,
        job_id: str,
        job_data: Dict[str, Any],
        client: httpx.Client,
        config: Dict[str, Any],
    ) -> Optional[BaseTask]:
        """Create a task instance based on task type

        Args:
            task_type: Type of task to create
            job_id: Job identifier
            job_data: Job configuration data
            client: HTTP client for API communication
            config: Agent configuration

        Returns:
            Task instance or None if task type not found
        """
        task_class = self._tasks.get(task_type)
        if not task_class:
            LOG.error(f"Unknown task type: {task_type}")
            return None

        try:
            return task_class(job_id, job_data, client, config)
        except Exception as e:
            LOG.error(f"Error creating task of type '{task_type}': {e}")
            return None

    def get_registered_tasks(self) -> list:
        """Get list of registered task types"""
        return list(self._tasks.keys())


# Global task registry instance
_global_registry = TaskRegistry()


def get_task_registry() -> TaskRegistry:
    """Get the global task registry instance"""
    return _global_registry
