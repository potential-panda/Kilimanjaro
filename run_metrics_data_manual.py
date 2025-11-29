#!/usr/bin/env python3
"""
Script to manually run the metrics data task without waiting for the scheduled time.

Usage:
    python run_metrics_data_manual.py

Optional arguments:
    --host HOST_NAME  (run for specific host, e.g., orion-01, orion-03)
"""
import argparse
import logging

from qate.env import sys_env
from qate.env.env import Env

from kilimanjaro.agent import ENV_NAME, Config, KilimanjaroAgent

LOG = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Manually run metrics data task")
    parser.add_argument(
        "--host",
        help="Optional: specific host (e.g., orion-01, orion-03, localhost)",
    )
    args = parser.parse_args()

    # Load agent environment and config
    env = Env(sys_env.ENV_DIR, ENV_NAME)
    try:
        env.visit()
        config = env.load_object("config.json", Config)
        agent = KilimanjaroAgent(config)

        # If specific host is requested, customize the job data
        if args.host:
            LOG.info(f"Running metrics data task for host: {args.host}")
            # Override the task execution with custom job data
            job_data = {"task_type": "metrics_data", "hosts": [args.host]}

            # Create and run task directly
            config_dict = {
                "download_tmp_dir": config.metrics_data_download_tmp_dir,
            }

            task = agent.task_registry.create_task("metrics_data", "manual_run", job_data, agent.client, config_dict)

            if task:
                LOG.info("Starting metrics data task...")
                task.execute()
                LOG.info("Metrics data task completed")
            else:
                LOG.error("Failed to create metrics data task")
        else:
            LOG.info("Running metrics data task for all default hosts (orion-01, orion-03)")
            agent.run_metrics_data_task()

            # Wait for task to complete
            if agent.task_thread:
                LOG.info("Waiting for task to complete...")
                agent.task_thread.join()
                LOG.info("Task completed")

    finally:
        env.leave()


if __name__ == "__main__":
    main()
