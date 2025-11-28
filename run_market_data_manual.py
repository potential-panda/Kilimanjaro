#!/usr/bin/env python3
"""
Script to manually run the market data task without waiting for the scheduled time.

Usage:
    python run_market_data_manual.py

Optional arguments:
    --exchange EXCHANGE_NAME --symbol SYMBOL_NAME  (run for specific market)
"""
import argparse
import logging

from qate.env import sys_env
from qate.env.env import Env

from kilimanjaro.agent import ENV_NAME, KilimanjaroAgent

logging.basicConfig(
    level=logging.INFO,
    format="{asctime} {levelname:8s} {name} {message}",
    style="{",
)

LOG = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Manually run market data task")
    parser.add_argument("--exchange", help="Optional: specific exchange (e.g., BINANCE, GMO)")
    parser.add_argument("--symbol", help="Optional: specific symbol (e.g., BTC_USDT, ETH_JPY)")
    args = parser.parse_args()

    # Load agent environment and config
    env = Env(sys_env.ENV_DIR, ENV_NAME)
    env.load()

    try:
        env.enter()
        config = env.get("config")
        agent = KilimanjaroAgent(config)

        # If specific market is requested, customize the job data
        if args.exchange and args.symbol:
            LOG.info(f"Running market data task for {args.exchange} {args.symbol}")
            # Override the task execution with custom job data
            job_data = {"task_type": "market_data", "markets": [{"exchange": args.exchange, "symbol": args.symbol}]}

            # Create and run task directly
            config_dict = {
                "output_dir": config.market_data_output_dir,
                "download_tmp_dir": config.market_data_download_tmp_dir,
                "merge_tmp_dir": config.market_data_merge_tmp_dir,
            }

            task = agent.task_registry.create_task("market_data", "manual_run", job_data, agent.client, config_dict)

            if task:
                LOG.info("Starting market data task...")
                task.execute()
                LOG.info("Market data task completed")
            else:
                LOG.error("Failed to create market data task")
        else:
            LOG.info("Running market data task for all default markets")
            agent.run_market_data_task()

            # Wait for task to complete
            if agent.task_thread:
                LOG.info("Waiting for task to complete...")
                agent.task_thread.join()
                LOG.info("Task completed")

    finally:
        env.leave()


if __name__ == "__main__":
    main()
