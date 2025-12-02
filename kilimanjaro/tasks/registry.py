from logging import getLogger
from threading import Lock
from typing import Dict, Optional, Type

from .base import BaseTask
from .job_acquisition_task import JobAcquisitionTask
from .market_data_task import MarketDataTask
from .metrics_data_task import MetricsDataTask

LOG = getLogger(__name__)


class TaskRegistry:
    """Registry for managing task types and their locks"""

    def __init__(self):
        self._locks: Dict[Type[BaseTask], Lock] = {}
        self._register_default_tasks()

    def _register_default_tasks(self) -> None:
        self.register(JobAcquisitionTask)
        self.register(MarketDataTask)
        self.register(MetricsDataTask)

    def register(self, task_class: Type[BaseTask]) -> None:
        if task_class not in self._locks:
            self._locks[task_class] = Lock()

    def get_lock(self, task_class: Type[BaseTask]) -> Optional[Lock]:
        return self._locks.get(task_class)


_global_registry = TaskRegistry()


def get_task_registry() -> TaskRegistry:
    return _global_registry
