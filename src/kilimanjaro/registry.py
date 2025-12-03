from logging import getLogger
from threading import Lock
from typing import Dict, Optional, Type

from .task import Task
from .tasks import AddBacktestTask, JobAcquisitionTask, MarketDataTask, MetricsDataTask

LOG = getLogger(__name__)


class TaskRegistry:
    """Registry for managing task types and their locks"""

    def __init__(self):
        self._locks: Dict[Type[Task], Lock] = {}
        self._register_default_tasks()

    def _register_default_tasks(self) -> None:
        self.register(JobAcquisitionTask)
        self.register(MarketDataTask)
        self.register(MetricsDataTask)
        self.register(AddBacktestTask)

    def register(self, task_class: Type[Task]) -> None:
        if task_class not in self._locks:
            self._locks[task_class] = Lock()

    def get_lock(self, task_class: Type[Task]) -> Optional[Lock]:
        return self._locks.get(task_class)


_global_registry = TaskRegistry()


def get_task_registry() -> TaskRegistry:
    return _global_registry
