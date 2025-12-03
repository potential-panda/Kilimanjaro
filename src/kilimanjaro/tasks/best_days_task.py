import json
import os
from logging import getLogger
from pathlib import Path

from qate.env.env_name import EnvName

from ..backtest_runner import run_backtest
from ..task import Task

LOG = getLogger(__name__)

# Folder where best result files are stored
BEST_RESULT_DIR = "best_results"
DEFAULT_ENV_NAME = EnvName.QIN_TENGFEI


class BestDaysTask(Task):
    def __init__(self, config):
        super().__init__()
        self.config = config

    def execute(self) -> None:
        """Execute backtest and log best parameters from result file"""
        try:
            # Generate job_data in memory
            job_data = {
                "job_id": "best_days_task",
                "strategy_name": "orion",
                "variant": "v601",
                "from_dt": "ONE_WEEK_AGO",
                "to_dt": "",
                "optimize": True,
                "retest": True,
                "config": {
                    "market": "GMO:XRP_JPY",
                    "ref_market": "BINANCE:XRP_USDT",
                    "order_size": 200,
                    "magnifier": 5,
                },
                "param_grid": [
                    {"name": "acceleration", "default_value": 0.02, "values": [0.02], "factor": 1000},
                    {"name": "step", "default_value": 0.04, "values": [0.02, 0.04], "factor": 1000},
                    {"name": "maximum", "default_value": 0.2, "values": [0.2], "factor": 1000},
                    {
                        "name": "box_size_rate",
                        "default_value": 0.0015,
                        "values": [0.001, 0.0015, 0.002, 0.0025, 0.003],
                        "factor": 100000,
                    },
                ],
            }

            LOG.info("Starting backtest with job_data")
            LOG.info(f"Job data: {json.dumps(job_data, indent=2)}")

            # Run the backtest
            run_logs = run_backtest(
                DEFAULT_ENV_NAME,
                job_data,
                job_data["config"],
                job_data["param_grid"],
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
            best_result_dir = Path(BEST_RESULT_DIR)

            if not best_result_dir.exists():
                LOG.warning(f"Best result directory does not exist: {best_result_dir}")
                return

            # Find the newest file in the best result directory
            result_files = []
            for filename in os.listdir(best_result_dir):
                file_path = best_result_dir / filename
                if file_path.is_file():
                    result_files.append((file_path, os.path.getmtime(file_path)))

            if not result_files:
                LOG.warning(f"No result files found in {best_result_dir}")
                return

            # Sort by modification time (newest first)
            result_files.sort(key=lambda x: x[1], reverse=True)
            newest_file, mtime = result_files[0]

            LOG.info(f"Reading best parameters from: {newest_file}")

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
