from logging import getLogger
from threading import Lock
from typing import Any, Dict, Optional, Type

from .base import BaseTask
from .backtest_task import BacktestTask
from .job_acquisition_task import JobAcquisitionTask
from .market_data_task import MarketDataTask
from .metrics_data_task import MetricsDataTask

LOG = getLogger(__name__)


class TaskRegistry:
    """Registry for managing task types and their locks"""

    def __init__(self):
        # Map string names to task classes for API compatibility
        self._string_to_class: Dict[str, Type[BaseTask]] = {}
        # Lock per task class
        self._locks: Dict[Type[BaseTask], Lock] = {}
        self._register_default_tasks()

    def _register_default_tasks(self) -> None:
        """Register default task types"""
        self.register("backtest", BacktestTask)
        self.register("job_acquisition", JobAcquisitionTask)
        self.register("market_data", MarketDataTask)
        self.register("metrics_data", MetricsDataTask)

    def register(self, task_name: str, task_class: Type[BaseTask]) -> None:
        """Register a new task type

        Args:
            task_name: String identifier for the task type (for API compatibility)
            task_class: Task class that inherits from BaseTask
        """
        if task_name in self._string_to_class:
            LOG.warning(f"Task name '{task_name}' is already registered, overwriting")
        self._string_to_class[task_name] = task_class
        # Create a lock for this task class if it doesn't exist
        if task_class not in self._locks:
            self._locks[task_class] = Lock()
        LOG.info(f"Registered task: {task_name} -> {task_class.__name__}")

    def create_task(
        self,
        task_class: Type[BaseTask],
        job_id: str,
        job_data: Dict[str, Any],
    ) -> Optional[BaseTask]:
        """Create a task instance based on task class

        Args:
            task_class: Task class to instantiate
            job_id: Job identifier
            job_data: Job configuration data

        Returns:
            Task instance or None if creation fails
        """
        try:
            # Create task instance and set job data
            task = task_class()
            task.job_id = job_id
            task.job_data = job_data
            return task
        except Exception as e:
            LOG.error(f"Error creating task of class '{task_class.__name__}': {e}")
            return None

    def create_task_from_string(
        self,
        task_name: str,
        job_id: str,
        job_data: Dict[str, Any],
    ) -> Optional[BaseTask]:
        """Create a task instance based on string name (for API compatibility)

        Args:
            task_name: String identifier for the task type
            job_id: Job identifier
            job_data: Job configuration data

        Returns:
            Task instance or None if task name not found
        """
        task_class = self._string_to_class.get(task_name)
        if not task_class:
            LOG.error(f"Unknown task name: {task_name}")
            return None

        return self.create_task(task_class, job_id, job_data)

    def get_registered_tasks(self) -> list:
        """Get list of registered task names"""
        return list(self._string_to_class.keys())

    def get_lock(self, task_class: Type[BaseTask]) -> Optional[Lock]:
        """Get the lock for a specific task class

        Args:
            task_class: Task class

        Returns:
            Lock for the task class, or None if not registered
        """
        return self._locks.get(task_class)


# Global task registry instance
_global_registry = TaskRegistry()


def get_task_registry() -> TaskRegistry:
    """Get the global task registry instance"""
    return _global_registry
