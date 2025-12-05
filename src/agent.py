import argparse
import time
from dataclasses import dataclass
from logging import getLogger
from typing import Any, Type

import schedule
from qate.env import sys_env
from qate.env.env import Env, EnvName

from kilimanjaro.task import Task
from kilimanjaro.tasks import (
    AddBacktestTask,
    BestDaysTask,
    JobAcquisitionTask,
    MarketDataTask,
    MetricsDataTask,
)

LOG = getLogger(__name__)
ENV_NAME = EnvName.KILIMANJARO_AGENT
API_BASE_URL = "http://localhost:8766"


@dataclass
class Config:
    api_base_url: str = API_BASE_URL
    job_acquisition_interval: int = 12  # seconds
    market_data_schedule_time: str = "04:00"  # 4am daily
    metrics_data_interval: int = 10  # minutes


def run_task(TaskClass: Type[Task], input: Any = None):
    task = TaskClass(input)
    return task.run_async()


class KilimanjaroAgent:
    def __init__(self, config: Config):
        self.config = config

    def start(self):
        LOG.info("Starting Kilimanjaro Agent")
        LOG.info(f"config: {self.config}")

        schedule.every(11).seconds.do(run_task, JobAcquisitionTask, self.config)
        schedule.every().day.at("04:00").do(run_task, MarketDataTask)
        schedule.every(11).minutes.do(run_task, MetricsDataTask)

        schedule.every().day.at("03:00").do(
            run_task,
            AddBacktestTask,
            {
                "env_name": EnvName.BACKTEST_LAST_WEEK.name,
                "from_dt": "ONE_WEEK_AGO",
            },
        )
        schedule.every().week.monday.at("05:00").do(
            run_task,
            AddBacktestTask,
            {
                "env_name": EnvName.BACKTEST_LAST_MONTH.name,
                "from_dt": "ONE_MONTH_AGO",
            },
        )
        # Schedule every 4 hours at the same minutes
        for hour in [0, 4, 8, 12, 16, 20]:
            schedule.every().day.at(f"{hour:02d}:20").do(run_task, BestDaysTask, {"preset": "xrp"})
            schedule.every().day.at(f"{hour:02d}:50").do(run_task, BestDaysTask, {"preset": "eth"})

        while True:
            try:
                schedule.run_pending()
            except Exception:
                LOG.exception("Unhandled exception while running scheduled task")
            try:
                time.sleep(10)
            except KeyboardInterrupt:
                break


def handle_setup(args):
    Env.setup_env(
        sys_env.ENV_DIR,
        ENV_NAME,
        {
            "config": Config(),
        },
    )


def handle_schedule(args):
    env = Env.load_env(sys_env.ENV_DIR, ENV_NAME)
    try:
        env.enter()
        agent = KilimanjaroAgent(env.get("config"))
        agent.start()
    finally:
        env.leave()


def handle_market_data(args):
    env = Env.load_env(sys_env.ENV_DIR, ENV_NAME)
    try:
        env.visit()
        thread = run_task(MarketDataTask)
        if thread:
            thread.join()
    finally:
        env.leave()


def handle_metrics_data(args):
    env = Env.load_env(sys_env.ENV_DIR, ENV_NAME)
    try:
        env.visit()
        thread = run_task(MetricsDataTask)
        if thread:
            thread.join()
    finally:
        env.leave()


def handle_backtest(args):
    env = Env.load_env(sys_env.ENV_DIR, ENV_NAME)
    try:
        env.visit()
        thread = run_task(
            AddBacktestTask,
            {
                "from_dt": args.from_dt,
            },
        )
        if thread:
            thread.join()
    finally:
        env.leave()


def handle_best(args):
    env = Env.load_env(sys_env.ENV_DIR, ENV_NAME)
    try:
        env.visit()
        thread = run_task(BestDaysTask, {"preset": args.preset})
        if thread:
            thread.join()
    finally:
        env.leave()


def main():
    parser = argparse.ArgumentParser(description="Kilimanjaro Agent")
    subparsers = parser.add_subparsers(dest="command")

    setup_parser = subparsers.add_parser("setup", help="Setup the agent environment")
    setup_parser.set_defaults(func=handle_setup)

    schedule_parser = subparsers.add_parser("schedule", help="Start the agent scheduler")
    schedule_parser.set_defaults(func=handle_schedule)

    a_parser = subparsers.add_parser("market-data")
    a_parser.set_defaults(func=handle_market_data)

    b_parser = subparsers.add_parser("metrics-data")
    b_parser.set_defaults(func=handle_metrics_data)

    c_parser = subparsers.add_parser("backtest")
    c_parser.add_argument("--from-dt", required=True, help="Start date for backtest")
    c_parser.set_defaults(func=handle_backtest)

    d_parser = subparsers.add_parser("best")
    d_parser.add_argument("--preset", default="xrp")
    d_parser.set_defaults(func=handle_best)

    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
