# Kilimanjaro Agent Tasks

This directory contains the task implementations for the Kilimanjaro agent. The agent uses a task-based architecture that makes it easy to add new types of tasks.

## Architecture

- **BaseTask**: Abstract base class that all tasks must inherit from
- **TaskRegistry**: Registry for managing and creating task instances
- **BacktestTask**: Task for running strategy backtests (job-based)
- **MarketDataTask**: Task for downloading and merging market data from S3 (scheduled daily at 4am)

## How to Add a New Task

Follow these steps to add a new task type:

### 1. Create a New Task Class

Create a new file in the `tasks/` directory (e.g., `my_new_task.py`):

```python
from logging import getLogger
from typing import Any, Dict

from .base import BaseTask

LOG = getLogger(__name__)


class MyNewTask(BaseTask):
    """Task for doing something new"""

    TASK_TYPE = "my_new_task"  # Unique identifier for this task

    def __init__(self, job_id: str, job_data: Dict[str, Any], client, config: Dict[str, Any]):
        super().__init__(job_id, job_data, client)
        self.config = config
        # Initialize any task-specific attributes here

    def get_task_type(self) -> str:
        return self.TASK_TYPE

    def execute(self) -> None:
        """Execute the task"""
        try:
            # Mark task as started
            self.mark_started()

            # Do your task work here
            # Access job data via self.job_data
            # Access configuration via self.config
            LOG.info(f"Executing {self.TASK_TYPE} for job {self.job_id}")

            # Your task logic here...
            result = self._do_work()

            # Mark task as completed with results
            self.mark_completed({"result": result})

        except Exception as e:
            LOG.error(f"Error in {self.TASK_TYPE}: {e}")
            self.mark_failed(str(e))
        finally:
            self.cleanup()

    def _do_work(self):
        """Your task-specific implementation"""
        # Implement your task logic here
        pass

    def check_progress(self) -> None:
        """Optional: Check and update progress for long-running tasks"""
        # If your task runs for a long time, you can implement
        # periodic progress updates here
        if self.is_running:
            self.update_progress({"progress": "running", "status": "processing..."})

    def cleanup(self) -> None:
        """Optional: Clean up resources after task completion"""
        # Clean up any resources here
        pass
```

### 2. Register the Task

Update `tasks/__init__.py` to export your new task:

```python
from .base import BaseTask
from .backtest_task import BacktestTask
from .my_new_task import MyNewTask  # Add this line
from .registry import TaskRegistry, get_task_registry

__all__ = ["BaseTask", "BacktestTask", "MyNewTask", "TaskRegistry", "get_task_registry"]
```

### 3. Register with the Task Registry

Update `tasks/registry.py` to register your task:

```python
from .my_new_task import MyNewTask  # Add this import

class TaskRegistry:
    def __init__(self):
        self._tasks: Dict[str, Type[BaseTask]] = {}
        self._register_default_tasks()

    def _register_default_tasks(self) -> None:
        """Register default task types"""
        self.register(BacktestTask.TASK_TYPE, BacktestTask)
        self.register(MyNewTask.TASK_TYPE, MyNewTask)  # Add this line
```

### 4. Use the Task

Your task is now ready to use! The agent will automatically pick it up based on the `task_type` field in the job data:

```python
# When creating a job via the API, specify the task_type:
job_data = {
    "job_id": "job123",
    "task_type": "my_new_task",  # This matches MyNewTask.TASK_TYPE
    # ... other job-specific data
}
```

## Example: BacktestTask

The `BacktestTask` is a complete example showing:
- Environment setup
- Running external processes
- Reading and processing results
- Progress tracking
- Error handling

See `backtest_task.py` for the full implementation.

## Example: MarketDataTask (Scheduled Task)

The `MarketDataTask` demonstrates a scheduled task that runs daily at 4am:
- Downloads market data from S3 for predefined markets
- Merges multiple data sources when needed
- Saves consolidated data to local disk
- Tracks statistics (downloaded files, merged files, errors)

**Default Markets:**
- BINANCE: BTC_USDT, ETH_USDT, XRP_USDT, SOL_USDT
- GMO: BTC_JPY, ETH_JPY, XRP_JPY, SOL_JPY

**Configuration (in agent Config):**
```python
enable_market_data_task: bool = True  # Enable/disable the task
market_data_schedule_time: str = "04:00"  # Daily run time
market_data_output_dir: str = "/panda/market-data"
market_data_download_tmp_dir: str = "/panda/tmp_download"
market_data_merge_tmp_dir: str = "/panda/tmp_merge"
```

**To customize markets:**
Pass a `markets` array in the job_data:
```python
job_data = {
    "task_type": "market_data",
    "markets": [
        {"exchange": "BINANCE", "symbol": "BTC_USDT"},
        {"exchange": "GMO", "symbol": "ETH_JPY"},
    ]
}
```

See `market_data_task.py` for the full implementation.

## BaseTask API

### Methods to Implement

- `execute()`: Main task execution logic (required)
- `get_task_type()`: Return the task type identifier (required)
- `check_progress()`: Periodic progress updates (optional)
- `cleanup()`: Resource cleanup (optional)

### Methods You Can Use

- `mark_started()`: Mark task as started
- `mark_completed(result_data)`: Mark task as completed with results
- `mark_failed(error)`: Mark task as failed with error message
- `update_progress(update_data)`: Update job progress via API

### Attributes Available

- `self.job_id`: Job identifier
- `self.job_data`: Job configuration data
- `self.client`: HTTP client for API communication
- `self.is_running`: Whether task is currently running
- `self.is_complete`: Whether task has completed

## Job Data Format

Jobs should include a `task_type` field to specify which task to run:

```json
{
  "job_id": "unique-job-id",
  "task_type": "backtest",
  "from_dt": "2024-01-01",
  "to_dt": "2024-12-31",
  "strategy_name": "orion",
  "variant": "v1",
  ...
}
```

If `task_type` is not specified, it defaults to "backtest" for backward compatibility.

## Scheduled Tasks vs Job-Based Tasks

The agent supports two types of task execution:

### Job-Based Tasks (e.g., BacktestTask)
- Acquired from the API via `/job/acquire`
- Started on-demand when a job is available
- Progress and results are reported back to the API
- Example: Backtests triggered by users

### Scheduled Tasks (e.g., MarketDataTask)
- Run automatically at scheduled times
- Configured in the agent's Config
- Don't require job acquisition from API
- Example: Daily market data downloads at 4am

### Adding a Scheduled Task

To add a new scheduled task:

1. Create the task class (same as job-based tasks)
2. Add a scheduling method in the agent:
```python
def run_my_scheduled_task(self):
    """Run my custom scheduled task"""
    if self.current_task and self.task_thread.is_alive():
        LOG.warning("Cannot start task: another task is running")
        return

    job_data = {"task_type": "my_task"}
    task = self._create_scheduled_task("my_task", job_data)

    self.current_task = task
    self.task_thread = threading.Thread(
        target=self._execute_task_thread,
        args=(task,),
        daemon=True
    )
    self.task_thread.start()
```

3. Schedule it in the agent's `start()` method:
```python
# Every day at 2pm
schedule.every().day.at("14:00").do(self.run_my_scheduled_task)

# Every hour
schedule.every().hour.do(self.run_my_scheduled_task)

# Every Monday at 9am
schedule.every().monday.at("09:00").do(self.run_my_scheduled_task)
```

See the [schedule library documentation](https://schedule.readthedocs.io/) for more scheduling options.
