import threading
from abc import ABC, abstractmethod
from logging import getLogger

LOG = getLogger(__name__)


class BaseTask(ABC):
    def __init__(self):
        self._lock_acquired = False

    def try_acquire_lock(self) -> bool:
        from .registry import get_task_registry

        task_class = self.__class__
        registry = get_task_registry()
        lock = registry.get_lock(task_class)

        if not lock:
            LOG.error(f"No lock found for task type '{task_class.__name__}' - task type may not be registered")
            return False

        # Try to acquire lock without blocking
        acquired = lock.acquire(blocking=False)
        if acquired:
            self._lock_acquired = True
            LOG.info(f"Lock acquired for task type '{task_class.__name__}'")
        else:
            LOG.warning(
                f"Could not acquire lock for task type '{task_class.__name__}': another task of this type is running"
            )

        return acquired

    def release_lock(self) -> None:
        if not self._lock_acquired:
            return

        from .registry import get_task_registry

        task_class = self.__class__
        registry = get_task_registry()
        lock = registry.get_lock(task_class)

        if lock:
            lock.release()
            self._lock_acquired = False
            LOG.info(f"Lock released for task type '{task_class.__name__}'")

    def run(self) -> None:
        if not self.try_acquire_lock():
            LOG.warning(f"Task skipped: another task of type '{self.__class__.__name__}' is running")
            return

        thread = threading.Thread(
            target=self._run_in_thread,
            daemon=True,
        )
        thread.start()

    def _run_in_thread(self):
        try:
            self.execute()
        except Exception as e:
            LOG.error(f"Error executing task {self.__class__.__name__}: {e}")
            raise
        finally:
            self.release_lock()

    @abstractmethod
    def execute(self) -> None:
        pass

    def cleanup(self) -> None:
        pass
