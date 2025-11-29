import argparse
import threading
import time
from dataclasses import dataclass
from logging import getLogger
from typing import Any, Dict, Optional

import httpx
import schedule
from qate.env import sys_env
from qate.env.env import Env, EnvName

from kilimanjaro.tasks import BaseTask, get_task_registry

LOG = getLogger(__name__)
ENV_NAME = EnvName.KILIMANJARO_AGENT

# Agent configuration
JOB_ACQUISITION_INTERVAL = 12  # seconds
PROGRESS_CHECK_INTERVAL = 600  # seconds
API_BASE_URL = "http://localhost:8766"
BACKTEST_WORK_DIR = "/panda/potential-panda/Himalaya"
MARKET_DATA_SCHEDULE_TIME = "04:00"  # 4am daily


@dataclass
class Config:
    api_base_url: str = API_BASE_URL
    job_acquisition_interval: int = JOB_ACQUISITION_INTERVAL
    progress_check_interval: int = PROGRESS_CHECK_INTERVAL
    backtest_work_dir: str = BACKTEST_WORK_DIR
    enable_market_data_task: bool = True
    market_data_schedule_time: str = MARKET_DATA_SCHEDULE_TIME
    market_data_output_dir: str = "/panda/market-data"
    market_data_download_tmp_dir: str = "/panda/tmp_download"
    market_data_merge_tmp_dir: str = "/panda/tmp_merge"
    enable_metrics_data_task: bool = True
    metrics_data_interval: int = 30  # minutes
    metrics_data_download_tmp_dir: str = "/panda/metrics-data"


class KilimanjaroAgent:
    def __init__(self, config: Config):
        self.config = config
        self.client = httpx.Client(base_url=config.api_base_url, timeout=30.0)
        self.task_registry = get_task_registry()
        self.current_task: Optional[BaseTask] = None
        self.task_thread: Optional[threading.Thread] = None

    def acquire_job(self) -> Optional[Dict[str, Any]]:
        """Acquire a job from the API"""
        response = self.client.post("/job/acquire")
        if response.status_code == 200:
            job_data = response.json()
            if job_data:
                LOG.info(f"Acquired job: {job_data.get('job_id')}")
                return job_data
        else:
            LOG.error(f"Failed to acquire job: {response.status_code}")
        return None

    def _execute_task_thread(self, task: BaseTask):
        """Execute task in background thread"""
        try:
            task.execute()
        except Exception as e:
            LOG.error(f"Error executing task: {e}")
        finally:
            self.current_task = None
            self.task_thread = None

    def check_running_task_progress(self):
        """Check progress of running task"""
        if not self.current_task or not self.current_task.is_running:
            return

        # Call task-specific progress check if available
        if hasattr(self.current_task, "check_progress"):
            self.current_task.check_progress()

    def _create_scheduled_task(self, task_type: str, job_data: Dict[str, Any]) -> Optional[BaseTask]:
        """Create a scheduled task without requiring job acquisition"""
        job_id = f"scheduled_{task_type}_{int(time.time())}"

        # Build config dict for the task
        config_dict = {
            "backtest_work_dir": self.config.backtest_work_dir,
        }

        # Add task-specific config
        if task_type == "market_data":
            config_dict.update(
                {
                    "output_dir": self.config.market_data_output_dir,
                    "download_tmp_dir": self.config.market_data_download_tmp_dir,
                    "merge_tmp_dir": self.config.market_data_merge_tmp_dir,
                }
            )
        elif task_type == "metrics_data":
            config_dict.update(
                {
                    "download_tmp_dir": self.config.metrics_data_download_tmp_dir,
                }
            )

        # Create task using registry
        task = self.task_registry.create_task(task_type, job_id, job_data, self.client, config_dict)

        return task

    def run_market_data_task(self):
        """Run the market data task (scheduled)"""
        # Check if there's already a task running
        if self.current_task and self.task_thread and self.task_thread.is_alive():
            LOG.warning("Cannot start market data task: another task is already running")
            return

        LOG.info("Starting scheduled market data task")

        job_data = {
            "task_type": "market_data",
            # Uses default markets defined in MarketDataTask
        }

        task = self._create_scheduled_task("market_data", job_data)

        if not task:
            LOG.error("Failed to create market data task")
            return

        # Start task in background thread
        self.current_task = task
        self.task_thread = threading.Thread(
            target=self._execute_task_thread,
            args=(task,),
            daemon=True,
        )
        self.task_thread.start()
        LOG.info("Market data task started successfully")

    def run_metrics_data_task(self):
        """Run the metrics data task (scheduled)"""
        # Check if there's already a task running
        if self.current_task and self.task_thread and self.task_thread.is_alive():
            LOG.warning("Cannot start metrics data task: another task is already running")
            return

        LOG.info("Starting scheduled metrics data task")

        job_data = {
            "task_type": "metrics_data",
            # Uses default hosts: orion-01, orion-03
        }

        task = self._create_scheduled_task("metrics_data", job_data)

        if not task:
            LOG.error("Failed to create metrics data task")
            return

        # Start task in background thread
        self.current_task = task
        self.task_thread = threading.Thread(
            target=self._execute_task_thread,
            args=(task,),
            daemon=True,
        )
        self.task_thread.start()
        LOG.info("Metrics data task started successfully")

    def job_acquisition_task(self):
        """Acquire and start executing a new job"""
        # Check if there's already a task running
        if self.current_task or (self.task_thread and self.task_thread.is_alive()):
            return

        job_data = self.acquire_job()
        if not job_data:
            return

        # Get task type from job data (default to "backtest" for backward compatibility)
        task_type = job_data.get("task_type", "backtest")
        job_id = job_data["job_id"]

        # Create task using registry
        config_dict = {
            "backtest_work_dir": self.config.backtest_work_dir,
        }
        task = self.task_registry.create_task(task_type, job_id, job_data, self.client, config_dict)

        if not task:
            LOG.error(f"Failed to create task of type '{task_type}' for job {job_id}")
            return

        # Start task in background thread
        self.current_task = task
        self.task_thread = threading.Thread(
            target=self._execute_task_thread,
            args=(task,),
            daemon=True,
        )
        self.task_thread.start()
        LOG.info(f"Started task '{task_type}' for job {job_id}")

    def start(self):
        """Start the agent main loop"""
        LOG.info("Starting Kilimanjaro Agent")
        LOG.info(f"API URL: {self.config.api_base_url}")
        LOG.info(f"Job acquisition interval: {self.config.job_acquisition_interval} seconds")
        LOG.info(f"Progress check interval: {self.config.progress_check_interval} seconds")
        LOG.info(f"Registered task types: {self.task_registry.get_registered_tasks()}")

        # Schedule job acquisition and progress check
        schedule.every(self.config.job_acquisition_interval).seconds.do(self.job_acquisition_task)
        schedule.every(self.config.progress_check_interval).seconds.do(self.check_running_task_progress)

        # Schedule market data task at configured time (default 4am daily)
        if self.config.enable_market_data_task:
            schedule.every().day.at(self.config.market_data_schedule_time).do(self.run_market_data_task)
            LOG.info(f"Market data task scheduled daily at {self.config.market_data_schedule_time}")

        # Schedule metrics data task every N minutes (default 30 minutes)
        if self.config.enable_metrics_data_task:
            schedule.every(self.config.metrics_data_interval).minutes.do(self.run_metrics_data_task)
            LOG.info(f"Metrics data task scheduled every {self.config.metrics_data_interval} minutes")

        # Run scheduler loop
        while True:
            try:
                schedule.run_pending()
            except Exception:
                LOG.exception("Unhandled exception while running scheduled task")
            try:
                time.sleep(10)
            except KeyboardInterrupt:
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true", help="Setup agent environment")
    parser.add_argument(
        "--run-market-data",
        action="store_true",
        help="Manually run market data task once and exit",
    )
    parser.add_argument(
        "--run-metrics-data",
        action="store_true",
        help="Manually run metrics data task once and exit",
    )
    args = parser.parse_args()

    if args.setup:
        Env.setup_env(
            sys_env.ENV_DIR,
            ENV_NAME,
            {
                "config": Config(),
            },
        )
        exit(0)

    env = Env(sys_env.ENV_DIR, ENV_NAME)
    env.load()

    try:
        env.visit()
        agent = KilimanjaroAgent(env.get("config"))
        if args.run_market_data:
            LOG.info("Running market data task manually...")
            agent.run_market_data_task()
            # Wait for task to complete
            if agent.task_thread:
                agent.task_thread.join()
            LOG.info("Market data task completed")
        elif args.run_metrics_data:
            LOG.info("Running metrics data task manually...")
            agent.run_metrics_data_task()
            # Wait for task to complete
            if agent.task_thread:
                agent.task_thread.join()
            LOG.info("Metrics data task completed")
        else:
            agent.start()
    finally:
        env.leave()
