import json
import os
import time
from logging import getLogger
from pathlib import Path

from qate.env import sys_env
from qate.env.env import Env
from qate.env.env_name import EnvName

from .. import job_data
from ..backtest_runner import run_backtest
from ..task import Task

LOG = getLogger(__name__)

# Folder where best result files are stored
BEST_RESULT_DIR = "best_result"
DEFAULT_ENV_NAME = EnvName.BACKTEST_DAYS_BEST


class BestDaysTask(Task):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.start_time = None

    def execute(self) -> None:
        """Execute backtest and log best parameters from result file"""
        try:
            # Record start time before executing
            self.start_time = time.time()
            LOG.info(f"Task started at: {self.start_time}")

            # Generate job_data using preset
            backtest_job_data = job_data.create(DEFAULT_ENV_NAME.name, "TWO_DAYS_AGO", "xrp")

            LOG.info("Starting backtest with job_data")
            LOG.info(f"Job data: {json.dumps(backtest_job_data, indent=2)}")

            # Run the backtest
            run_logs = run_backtest(
                DEFAULT_ENV_NAME,
                backtest_job_data,
                backtest_job_data["config"],
                backtest_job_data["param_grid"],
            )

            LOG.info(f"Backtest completed. Got {len(run_logs)} run logs")

            # Read and log best parameters from result folder
            self._log_best_parameters()

        except Exception as e:
            LOG.error(f"Error executing BestDaysTask: {e}")
            raise

    def _log_best_parameters(self) -> None:
        """Read and log best parameters from the best result directory"""
        try:
            env = Env(sys_env.ENV_DIR, DEFAULT_ENV_NAME)
            best_result_dir = Path(env.work_dir) / BEST_RESULT_DIR
            if not best_result_dir.exists():
                LOG.warning(f"Best result directory does not exist: {best_result_dir}")
                return

            # Find files created after start_time
            result_files = []
            for filename in os.listdir(best_result_dir):
                file_path = best_result_dir / filename
                if file_path.is_file():
                    file_ctime = os.path.getctime(file_path)
                    # Only include files created after task started
                    if self.start_time and file_ctime > self.start_time:
                        result_files.append((file_path, file_ctime))
                        LOG.info(f"Found file created after start: {filename} (ctime: {file_ctime})")

            if not result_files:
                LOG.warning(f"No result files created after start_time ({self.start_time}) found in {best_result_dir}")
                return

            # Sort by creation time (newest first)
            result_files.sort(key=lambda x: x[1], reverse=True)
            newest_file, ctime = result_files[0]

            LOG.info(f"Reading best parameters from: {newest_file} (created at: {ctime})")

            # Read and log the content
            with open(newest_file, "r") as f:
                content = f.read()
                LOG.info("=" * 80)
                LOG.info("BEST PARAMETERS:")
                LOG.info("=" * 80)
                LOG.info(content)
                LOG.info("=" * 80)

        except Exception as e:
            LOG.error(f"Error reading best parameters: {e}")
