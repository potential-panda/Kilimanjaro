import fcntl
import os
import threading
from abc import ABC, abstractmethod
from logging import getLogger
from pathlib import Path

LOG = getLogger(__name__)
LOCK_DIR = "./task_locks"


class Task(ABC):
    def __init__(self):
        self._lock_file = None
        self._lock_acquired = False

    def _get_lock_file_path(self) -> Path:
        """Get the lock file path for this task type"""
        task_class_name = self.__class__.__name__
        lock_dir = Path(LOCK_DIR)
        return lock_dir / f"{task_class_name}.lock"

    def try_acquire_lock(self) -> bool:
        """Try to acquire a file-based lock for this task type"""
        try:
            # Ensure lock directory exists
            lock_dir = Path(LOCK_DIR)
            lock_dir.mkdir(parents=True, exist_ok=True)

            lock_file_path = self._get_lock_file_path()

            # Open the lock file (create if doesn't exist)
            self._lock_file = open(lock_file_path, 'w')

            # Try to acquire exclusive lock without blocking
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Write PID to lock file for debugging
            self._lock_file.write(f"{os.getpid()}\n")
            self._lock_file.flush()

            self._lock_acquired = True
            return True

        except BlockingIOError:
            # Lock is held by another process
            if self._lock_file:
                self._lock_file.close()
                self._lock_file = None
            LOG.warning(
                f"Could not acquire lock for task type '{self.__class__.__name__}': another task of this type is running"
            )
            return False
        except Exception as e:
            if self._lock_file:
                self._lock_file.close()
                self._lock_file = None
            LOG.error(f"Error acquiring lock for task type '{self.__class__.__name__}': {e}")
            return False

    def release_lock(self) -> None:
        """Release the file-based lock"""
        if not self._lock_acquired or not self._lock_file:
            return

        try:
            # Release the lock
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()

            # Remove the lock file
            lock_file_path = self._get_lock_file_path()
            if lock_file_path.exists():
                lock_file_path.unlink()

        except Exception as e:
            LOG.error(f"Error releasing lock for task type '{self.__class__.__name__}': {e}")
        finally:
            self._lock_file = None
            self._lock_acquired = False

    def run_async(self) -> threading.Thread:
        if not self.try_acquire_lock():
            LOG.warning(f"Task skipped: another task of type '{self.__class__.__name__}' is running")
            return

        thread = threading.Thread(
            target=self._run_in_thread,
            daemon=True,
        )
        thread.start()
        return thread

    def _run_in_thread(self):
        try:
            self.execute()
        except Exception as e:
            LOG.error(f"Error executing task {self.__class__.__name__}: {e}")
            raise
        finally:
            self.release_lock()

    def run(self) -> None:
        if not self.try_acquire_lock():
            LOG.warning(f"Task skipped: another task of type '{self.__class__.__name__}' is running")
            return

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
