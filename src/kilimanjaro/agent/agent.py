import argparse
import json
import os
import subprocess

# Import JobStatus from the API module
import sys
import threading
import time
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import schedule
from potpanda.boot import sys_env
from potpanda.boot.env import Env
from potpanda.boot.env_name import EnvName
from potpanda.boot.profile import StrategyProfile
from potpanda.boot.run_log import RunLogger

sys.path.append(str(Path(__file__).parent.parent / "api"))

LOG = getLogger(__name__)
ENV_NAME = EnvName.KILIMANJARO_AGENT

# Agent configuration
JOB_ACQUISITION_INTERVAL = 12  # seconds
PROGRESS_CHECK_INTERVAL = 600  # seconds
API_BASE_URL = "http://localhost:8766"
BACKTEST_ENV = EnvName.QIN_TENGFEI
BACKTEST_WORK_DIR = "/panda/potential-panda/potential-panda-k2/src"


def _resolve_backtest_env(job_data: Dict[str, Any]) -> EnvName:
    env_name = job_data.get("env")
    if env_name:
        return EnvName[env_name]
    return BACKTEST_ENV


def create_cmd(job_data, env_name: EnvName):
    from_dt = job_data.get("from_dt").replace("-", "")
    to_dt = job_data.get("to_dt").replace("-", "")
    variant = job_data.get("variant")
    optimize = job_data.get("optimize", False)
    retest = job_data.get("retest", False)

    cmd = ["python", "-m", "k2.a", "backtest", "--env", env_name.name, "--from-dt", from_dt]
    if to_dt:
        cmd.extend(["--to-dt", to_dt])
    if variant:
        cmd.extend(["--variant", variant])
    if optimize:
        cmd.append("--optimize")
    if retest:
        cmd.append("--retest")

    return cmd


@dataclass
class Config:
    api_base_url: str = API_BASE_URL
    job_acquisition_interval: int = JOB_ACQUISITION_INTERVAL
    progress_check_interval: int = PROGRESS_CHECK_INTERVAL
    backtest_work_dir: str = BACKTEST_WORK_DIR


class KilimanjaroAgent:
    def __init__(self, config: Config):
        self.config = config
        self.job_id: Optional[str] = None
        self.client = httpx.Client(base_url=config.api_base_url, timeout=30.0)
        self.backtest_thread: Optional[threading.Thread] = None
        self.backtest_process: Optional[subprocess.Popen] = None

    def acquire_job(self) -> Optional[Dict[str, Any]]:
        response = self.client.post("/job/acquire")
        if response.status_code == 200:
            job_data = response.json()
            if job_data:  # Not empty
                LOG.info(f"Acquired job: {job_data.get('job_id')}")
                return job_data
        else:
            LOG.error(f"Failed to acquire job: {response.status_code}")
        return None

    def setup_backtest_env(self, job_data: Dict[str, Any], env_name: EnvName) -> Env:
        strategy_name = job_data.get("strategy_name", "orion")
        variant = job_data.get("variant", "v0")
        param_grid_data = job_data.get("param_grid", [])
        config = job_data.get("config", {})
        profile = {
            "modules": {
                "config": f"bot.{strategy_name}.config.Config",
                "param_grid": "builtins.dict",
                "params": "builtins.dict",
                "strategy": "potpanda.boot.profile.StrategyProfile",
                "features": "builtins.dict",
            },
            "name": env_name.name,
        }

        env = Env(sys_env.ENV_DIR, env_name)
        env.setup(profile, config, True)
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
            "strategy.json",
            StrategyProfile(
                f"bot.{strategy_name}",
                variant,
            ),
            True,
        )
        return env

    def read_recent_run_log_results(self, since_timestamp: float) -> list:
        """Read run log results created since the given timestamp"""
        run_logger = RunLogger(sys_env.get_run_log_dir(is_backtest=True))
        run_log_dir = run_logger.root_dir

        results = []
        try:
            # List all JSON files in the run_log directory
            for filename in os.listdir(run_log_dir):
                if filename.endswith(".json"):
                    file_path = os.path.join(run_log_dir, filename)
                    try:
                        # Check if file was created after the backtest started
                        file_mtime = os.path.getmtime(file_path)
                        if file_mtime >= since_timestamp:
                            with open(file_path, "r") as f:
                                run_log_data = json.load(f)
                                # Add file metadata
                                run_log_data["file_name"] = filename
                                run_log_data["file_mtime"] = file_mtime
                                results.append(run_log_data)
                    except Exception as e:
                        LOG.warning(f"Error reading run log file {filename}: {e}")
        except Exception as e:
            LOG.error(f"Error reading run log directory: {e}")

        # Sort by creation time (newest first)
        results.sort(key=lambda x: x.get("file_mtime", 0), reverse=True)
        return results

    def _execute_backtest_thread(self, job_data: Dict[str, Any]):
        """Execute backtest in background thread"""
        start_time = time.time()

        try:
            target_env_name = _resolve_backtest_env(job_data)
            bt_env = self.setup_backtest_env(job_data, target_env_name)

            cmd = create_cmd(job_data, target_env_name)
            LOG.info(f"Starting backtest for job {self.job_id}: {' '.join(cmd)}")
            self.update_job_progress(self.job_id, {"progress": "started"})

            shell_env = os.environ.copy()
            shell_env["PYTHON_GIL"] = "0"
            self.backtest_process = subprocess.Popen(
                cmd,
                env=shell_env,
                cwd=self.config.backtest_work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Wait for completion and ensure proper cleanup
            try:
                stdout, stderr = self.backtest_process.communicate()
                # Ensure returncode is set
                if self.backtest_process.returncode is None:
                    self.backtest_process.wait()

                LOG.info(f"Command completed with return code: {self.backtest_process.returncode}")
                if stdout:
                    LOG.debug(f"STDOUT: {stdout}")
                if stderr:
                    LOG.debug(f"STDERR: {stderr}")
            except Exception as e:
                LOG.error(f"Error during subprocess communication: {e}")
                # Force process termination if needed
                if self.backtest_process.poll() is None:
                    self.backtest_process.terminate()
                    self.backtest_process.wait()
                raise

            if self.backtest_process.returncode == 0:
                LOG.info(f"Backtest completed successfully for job {self.job_id}")

                # Read run log results
                run_log_results = self.read_recent_run_log_results(start_time)

                result_data = {
                    "stdout": stdout[-1000:] if stdout else "",
                    "env_name": bt_env.name.name,
                    "run_logs": run_log_results,
                    "run_log_count": len(run_log_results),
                }

                self.update_job_progress(self.job_id, {"progress": "DONE", "result": result_data})
            else:
                LOG.error(f"Backtest failed for job {self.job_id}: {stderr}")
                self.update_job_progress(
                    self.job_id,
                    {
                        "progress": "ERROR",
                        "result": {
                            "error": stderr[-1000:],
                        },
                    },
                )
        except Exception as e:
            LOG.error(f"Error in backtest thread: {e}")
            self.update_job_progress(
                self.job_id,
                {
                    "progress": "ERROR",
                    "result": {
                        "error": str(e),
                    },
                },
            )
        finally:
            self.backtest_process = None
            self.backtest_thread = None
            self.job_id = None

    def update_job_progress(self, job_id: str, update_data: Dict[str, Any]):
        update_data["ts"] = time.time()
        try:
            response = self.client.post(f"/job/update/{job_id}", json=update_data)
            if response.status_code != 204:
                LOG.error(f"Failed to update job {job_id}: {response.status_code}")
        except Exception as e:
            LOG.error(f"Error updating job progress: {e}")

    def check_running_job_progress(self):
        if not self.job_id or not self.backtest_process:
            return

        if self.backtest_process.poll() is None:
            self.update_job_progress(self.job_id, {"progress": "running"})

    def job_acquisition_task(self):
        # Check if there's already a job running
        if self.job_id or (self.backtest_thread and self.backtest_thread.is_alive()):
            return

        job_data = self.acquire_job()
        if not job_data:
            return

        self.job_id = job_data["job_id"]
        self.backtest_thread = threading.Thread(
            target=self._execute_backtest_thread,
            args=(job_data,),
            daemon=True,
        )
        self.backtest_thread.start()

    def start(self):
        LOG.info("Starting Kilimanjaro Agent")
        LOG.info(f"API URL: {self.config.api_base_url}")
        LOG.info(f"Job acquisition interval: {self.config.job_acquisition_interval} seconds")
        LOG.info(f"Progress check interval: {self.config.progress_check_interval} seconds")

        # Schedule tasks
        schedule.every(self.config.job_acquisition_interval).seconds.do(self.job_acquisition_task)
        schedule.every(self.config.progress_check_interval).seconds.do(self.check_running_job_progress)

        # Run scheduler loop
        while True:
            try:
                schedule.run_pending()
            except Exception:
                LOG.exception("Unhandled exception while running scheduled task")
            try:
                time.sleep(10)  # Check every 10 seconds
            except KeyboardInterrupt:
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true")
    args = parser.parse_args()

    if args.setup:
        Env.setup_tool(sys_env.ENV_DIR, ENV_NAME, Config())
        exit(0)

    env = Env(sys_env.ENV_DIR, ENV_NAME)
    env.load()

    try:
        env.enter()
        agent = KilimanjaroAgent(env.get("config"))
        agent.start()
    finally:
        env.leave()
