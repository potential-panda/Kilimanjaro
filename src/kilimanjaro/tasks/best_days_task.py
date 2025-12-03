import copy
import json
import os
import subprocess
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


PARAMS_TEMPLATE = {
    "acceleration": 0.02,
    "box_size_rate": 0.002,
    "margin_pct_min": 0.0015,
    "maximum": 0.2,
    "period": 60,
    "step": 0.02,
    "tick_cnt_threshold": 50,
    "volume_threshold": 0.5,
}

PRESET_REMOTE = {
    "xrp": "orion-01:/home/ubuntu/env/QIN_TENGFEI/",
    "eth": "orion-03:/home/ubuntu/env/QASIM_TARIQ/",
}


class BestDaysTask(Task):
    def __init__(self, config):
        super().__init__()
        self.preset = config["preset"]
        self.start_time = None

    def execute(self) -> None:
        """Execute backtest and log best parameters from result file"""
        try:
            # Record start time before executing
            self.start_time = time.time()
            LOG.info(f"Task started at: {self.start_time}")

            # Generate job_data using preset
            backtest_job_data = job_data.create(DEFAULT_ENV_NAME.name, "TWO_DAYS_AGO", self.preset)

            LOG.info("Starting backtest with job_data")
            LOG.info(f"Job data: {json.dumps(backtest_job_data)}")

            # Run the backtest
            run_logs = run_backtest(
                DEFAULT_ENV_NAME,
                backtest_job_data,
                backtest_job_data["config"],
                backtest_job_data["param_grid"],
            )

            LOG.info(f"Backtest completed. Got {len(run_logs)} run logs")

            params_path = self.populate_best_params()
            if params_path:
                self.scp_to_remote(params_path)

        except Exception as e:
            LOG.error(f"Error executing BestDaysTask: {e}")
            raise

    def scp_to_remote(self, local_file: Path) -> None:
        remote_path = PRESET_REMOTE.get(self.preset)
        if not remote_path:
            LOG.warning(f"No remote path configured for preset: {self.preset}")
            return

        try:
            scp_cmd = ["scp", str(local_file), remote_path]
            LOG.info(f"Running scp command: {' '.join(scp_cmd)}")
            result = subprocess.run(scp_cmd, check=True, capture_output=True, text=True)
            LOG.info(f"Successfully copied params.json to {remote_path}")
            if result.stdout:
                LOG.info(f"scp stdout: {result.stdout}")
        except subprocess.CalledProcessError as e:
            LOG.error(f"Failed to copy params.json to {remote_path}: {e}")
            LOG.error(f"scp stderr: {e.stderr}")

    def populate_best_params(self) -> Path:
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

            with open(newest_file, "r") as f:
                content = f.read()
                result_data = json.loads(content)
                best_params = result_data.get("params", {})
                merged_params = copy.deepcopy(PARAMS_TEMPLATE)
                merged_params.update(best_params)

                # Save to ./tmp/{preset}/params.json
                output_dir = Path("./tmp") / self.preset
                output_dir.mkdir(parents=True, exist_ok=True)
                output_file = output_dir / "params.json"

                with open(output_file, "w") as out_f:
                    json.dump(merged_params, out_f, indent=2)

                LOG.info(f"Saved merged parameters to: {output_file} {merged_params}")
                return output_file

        except Exception as e:
            LOG.error(f"Error reading best parameters: {e}")
            LOG.exception(e)
