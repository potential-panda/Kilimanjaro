import argparse
import time
from dataclasses import dataclass
from logging import getLogger
from typing import Any, Type

import schedule
from qate.env import sys_env
from qate.env.env import Env, EnvName

from kilimanjaro.tasks import (
    BaseTask,
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


def run_task(TaskClass: Type[BaseTask], input: Any = None):
    LOG.info(f"run_task {TaskClass}")
    task = TaskClass(input)
    task.run_async()


class KilimanjaroAgent:
    def __init__(self, config: Config):
        self.config = config

    def run_market_data_task(self):
        task = MarketDataTask()
        task.run_async()

    def run_metrics_data_task(self):
        task = MetricsDataTask()
        task.run_async()

    def job_acquisition_task(self):
        task = JobAcquisitionTask()
        task.run_async()

    def start(self):
        LOG.info("Starting Kilimanjaro Agent")
        LOG.info(f"config: {self.config}")

        schedule.every(self.config.job_acquisition_interval).seconds.do(run_task, JobAcquisitionTask, self.config)
        schedule.every().day.at(self.config.market_data_schedule_time).do(run_task, MarketDataTask)
        schedule.every(self.config.metrics_data_interval).minutes.do(run_task, MetricsDataTask)

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
        env.enter()
        agent = KilimanjaroAgent(env.get("config"))
        agent.start()
    finally:
        env.leave()
