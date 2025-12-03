import json
import os
import subprocess
import time
from logging import getLogger
from pathlib import Path

from qate.boot.config import TradingProfile
from qate.env import sys_env
from qate.env.env import Env, EnvName
from qate.trading.param import ParamGrid

LOG = getLogger(__name__)

BACKTEST_SCRIPT = "/panda/potential-panda/Himalaya/start.sh"


options = {
    "from_dt": "20251109_2200",
    "to_dt": "20251112_1900",
    "variant": "v601",
    "optimize": True,
    "retest": True,
}
config = {
    "market": "GMO:XRP_JPY",
    "ref_market": "BINANCE:XRP_USDT",
    "order_size": 150,
    "magnifier": 5,
}
param_grid = ParamGrid()


def setup_env(env_name: EnvName, strategy_name: str, variant: str, config: dict, param_grid: dict) -> Env:
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
    env.save_object("param_grid.json", param_grid, True)
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


def create_cmd(env_name: EnvName, options: dict) -> list:
    from_dt = options.get("from_dt").replace("-", "")
    to_dt = options.get("to_dt").replace("-", "")
    variant = options.get("variant")
    optimize = options.get("optimize", False)
    retest = options.get("retest", False)

    cmd = [BACKTEST_SCRIPT, "--env", env_name.name, "--from-dt", from_dt]
    if to_dt:
        cmd.extend(["--to-dt", to_dt])
    if variant:
        cmd.extend(["--variant", variant])
    if optimize:
        cmd.append("--optimize")
    if retest:
        cmd.append("--retest")

    return cmd


def _get_results(bt_env: Env, since_timestamp: float) -> list:
    run_log_dir = Path(bt_env.work_dir) / "run_log"
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


def run_backtest(env_name: EnvName, options: dict, config: dict, param_grid: dict) -> list[dict]:
    start_time = time.time()
    bt_env = setup_env(
        env_name,
        options["strategy_name"],
        options["variant"],
        config,
        param_grid,
    )
    cmd = create_cmd(env_name, options)
    LOG.info(f"Starting backtest {' '.join(cmd)}")

    bt_process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        stdout, stderr = bt_process.communicate()
        if bt_process.returncode is None:
            bt_process.wait()

        LOG.info(f"Command completed with return code: {bt_process.returncode}")
        if stdout:
            LOG.debug(f"STDOUT: {stdout}")
        if stderr:
            LOG.debug(f"STDERR: {stderr}")
    except Exception as e:
        LOG.error(f"Error during subprocess communication: {e}")
        if bt_process.poll() is None:
            bt_process.terminate()
            bt_process.wait()
        raise

    if bt_process.returncode == 0:
        LOG.info("Backtest completed")
        return _get_results(bt_env, start_time)
    else:
        raise Exception(f"Backtest failed: {stderr}")
