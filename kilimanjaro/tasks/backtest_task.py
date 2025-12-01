import json
import os
import subprocess
import time
from logging import getLogger
from pathlib import Path
from typing import Any, Dict, Optional

from qate.boot.config import TradingProfile
from qate.env import sys_env
from qate.env.env import Env, EnvName

from .base import BaseTask

LOG = getLogger(__name__)

# Backtest configuration defaults
DEFAULT_BACKTEST_ENV = EnvName.QIN_TENGFEI
DEFAULT_BACKTEST_WORK_DIR = "/panda/potential-panda/Himalaya"


class BacktestTask(BaseTask):
    """Task for executing backtests"""

    def __init__(self):
        super().__init__()
        # job_id and job_data will be set by registry.create_task()
        self.backtest_process: Optional[subprocess.Popen] = None
        self.bt_env: Optional[Env] = None
        self.start_time: Optional[float] = None

    def _resolve_backtest_env(self) -> EnvName:
        """Resolve which backtest environment to use"""
        env_name = self.job_data.get("env")
        if env_name:
            return EnvName[env_name]
        return DEFAULT_BACKTEST_ENV

    def _create_backtest_cmd(self, env_name: EnvName) -> list:
        """Create the backtest command from job data"""
        from_dt = self.job_data.get("from_dt").replace("-", "")
        to_dt = self.job_data.get("to_dt").replace("-", "")
        variant = self.job_data.get("variant")
        optimize = self.job_data.get("optimize", False)
        retest = self.job_data.get("retest", False)

        cmd = ["python", "src/b.py", "--env", env_name.name, "--from-dt", from_dt]
        if to_dt:
            cmd.extend(["--to-dt", to_dt])
        if variant:
            cmd.extend(["--variant", variant])
        if optimize:
            cmd.append("--optimize")
        if retest:
            cmd.append("--retest")

        return cmd

    def _setup_backtest_env(self, env_name: EnvName) -> Env:
        """Setup the backtest environment with job configuration"""
        strategy_name = self.job_data.get("strategy_name", "orion")
        variant = self.job_data.get("variant", "v0")
        param_grid_data = self.job_data.get("param_grid", [])
        config = self.job_data.get("config", {})

        desc = {
            "modules": {
                "config": f"{strategy_name}.config.Config",
                "param_grid": "builtins.dict",
                "params": "builtins.dict",
                "trading": "qate.boot.config.TradingProfile",
                "features": "builtins.dict",
            },
            "name": env_name.name,
        }

        env = Env(sys_env.ENV_DIR, env_name)
        env.setup(desc, True)
        env.save_object("config.json", config, True)
        env.save_object("param_grid.json", param_grid_data, True)
        env.save_object("params.json", {}, True)
        env.save_object(
            "features.json",
            {
                "INVENTORY_HEALTH_CHECK": False,
            },
            True,
        )
        env.save_object(
            "trading.json",
            TradingProfile(
                f"{strategy_name}",
                variant,
            ),
            True,
        )
        return env

    def _read_recent_run_log_results(self, since_timestamp: float) -> list:
        """Read run log results created since the given timestamp"""
        if not self.bt_env:
            return []

        run_log_dir = Path(self.bt_env.work_dir) / "run_log"
        results = []

        try:
            for filename in os.listdir(run_log_dir):
                if filename.endswith(".json"):
                    file_path = run_log_dir / filename
                    try:
                        file_mtime = os.path.getmtime(file_path)
                        if file_mtime >= since_timestamp:
                            with open(file_path, "r") as f:
                                run_log_data = json.load(f)
                                run_log_data["file_name"] = filename
                                run_log_data["file_mtime"] = file_mtime
                                results.append(run_log_data)
                    except Exception as e:
                        LOG.warning(f"Error reading run log file {filename}: {e}")
        except Exception as e:
            LOG.error(f"Error reading run log directory: {e}")

        results.sort(key=lambda x: x.get("file_mtime", 0), reverse=True)
        return results

    def execute(self) -> None:
        """Execute the backtest task"""
        self.start_time = time.time()

        try:
            # Setup environment
            target_env_name = self._resolve_backtest_env()
            self.bt_env = self._setup_backtest_env(target_env_name)

            # Create command
            cmd = self._create_backtest_cmd(target_env_name)
            LOG.info(f"Starting backtest for job {self.job_id}: {' '.join(cmd)}")
            self.is_running = True

            # Execute backtest
            backtest_work_dir = DEFAULT_BACKTEST_WORK_DIR
            shell_env = os.environ.copy()
            shell_env["PYTHON_GIL"] = "0"

            self.backtest_process = subprocess.Popen(
                cmd,
                env=shell_env,
                cwd=backtest_work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Wait for completion
            try:
                stdout, stderr = self.backtest_process.communicate()
                if self.backtest_process.returncode is None:
                    self.backtest_process.wait()

                LOG.info(f"Command completed with return code: {self.backtest_process.returncode}")
                if stdout:
                    LOG.debug(f"STDOUT: {stdout}")
                if stderr:
                    LOG.debug(f"STDERR: {stderr}")
            except Exception as e:
                LOG.error(f"Error during subprocess communication: {e}")
                if self.backtest_process.poll() is None:
                    self.backtest_process.terminate()
                    self.backtest_process.wait()
                raise

            # Process results
            if self.backtest_process.returncode == 0:
                LOG.info(f"Backtest completed successfully for job {self.job_id}")
                run_log_results = self._read_recent_run_log_results(self.start_time)
                LOG.info(f"Found {len(run_log_results)} results")

                self.is_running = False
                self.is_complete = True
            else:
                LOG.error(f"Backtest failed for job {self.job_id}: {stderr}")
                self.is_running = False
                self.is_complete = True

        except Exception as e:
            LOG.error(f"Error in backtest task: {e}")
            self.is_running = False
            self.is_complete = True
        finally:
            self.cleanup()

    def check_progress(self) -> None:
        """Check progress for running backtest"""
        if self.backtest_process and self.backtest_process.poll() is None:
            LOG.debug(f"Backtest {self.job_id} is still running")

    def cleanup(self) -> None:
        """Clean up resources"""
        self.backtest_process = None
        self.bt_env = None
