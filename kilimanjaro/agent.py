import argparse
import time
from dataclasses import dataclass
from logging import getLogger

import schedule
from qate.env import sys_env
from qate.env.env import Env, EnvName

from kilimanjaro.tasks import JobAcquisitionTask, MarketDataTask, MetricsDataTask

LOG = getLogger(__name__)
ENV_NAME = EnvName.KILIMANJARO_AGENT


@dataclass
class Config:
    job_acquisition_interval: int = 12  # seconds
    market_data_schedule_time: str = "04:00"  # 4am daily
    metrics_data_interval: int = 30  # minutes


class KilimanjaroAgent:
    def __init__(self, config: Config):
        self.config = config

    def run_market_data_task(self):
        task = MarketDataTask()
        task.run()

    def run_metrics_data_task(self):
        task = MetricsDataTask()
        task.run()

    def job_acquisition_task(self):
        task = JobAcquisitionTask()
        task.run()

    def start(self):
        LOG.info("Starting Kilimanjaro Agent")
        LOG.info(f"Job acquisition interval: {self.config.job_acquisition_interval} seconds")

        schedule.every(self.config.job_acquisition_interval).seconds.do(self.job_acquisition_task)
        schedule.every().day.at(self.config.market_data_schedule_time).do(self.run_market_data_task)
        schedule.every(self.config.metrics_data_interval).minutes.do(self.run_metrics_data_task)

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
        env.visit()
        agent = KilimanjaroAgent(env.get("config"))
        agent.start()
    finally:
        env.leave()
