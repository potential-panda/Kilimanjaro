import os
from datetime import datetime, timedelta
from logging import getLogger
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo

import msgpack
import qate.env.sys_env as sys_env
from qate.store.s3 import download
from qate.util.serializer import MetricSerializer

from .base import BaseTask

LOG = getLogger(__name__)

# Default configuration
DEFAULT_DOWNLOAD_TMP_DIR = "/panda/metrics-data"
DEFAULT_ORG = "panda-org"

# Default host configuration
DEFAULT_HOST_CONFIG = {
    "orion-01": {
        "bucket": "panda-cc-orion-01",
        "envs": [
            "QIN_TENGFEI",
        ],
    },
    "orion-03": {
        "bucket": "panda-cc-orion-03",
        "envs": [
            "QASIM_TARIQ",
        ],
    },
}


def get_intervals(start_str: str) -> list[str]:
    """Get list of 5-minute intervals from start time to now"""
    start_time = datetime.strptime(start_str, "%Y%m%d_%H%M").replace(tzinfo=ZoneInfo("Asia/Tokyo"))
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    result = []

    current = start_time
    while current <= now:
        current += timedelta(minutes=5)
        result.append(current.strftime("%Y%m%d_%H%M"))

    return result


def process_file(influxdb_writer, full_path: Path) -> int:
    """Process a single msgpack file and write to InfluxDB"""
    count = 0
    with open(full_path, "rb") as f:
        unpacker = msgpack.Unpacker(f, raw=False)
        for i, obj in enumerate(unpacker, 1):
            point = MetricSerializer.deserialize(obj)
            influxdb_writer.add(point)
            count = i

    LOG.info(f"Processed {full_path}. Inserted {count} metrics into {influxdb_writer.bucket_name}")
    # Mark file as done
    name = str(full_path)
    os.rename(name, name + "__done")
    return count


class MetricsWorker:
    """Worker to download and process metrics for a specific host/env"""

    def __init__(self, host: str, env_name: str, download_dir: Path):
        self.host = host
        self.env_name = env_name
        self.influxdb_bucket_name = env_name
        self.s3_bucket_name = DEFAULT_HOST_CONFIG[self.host]["bucket"]
        self.download_dir = download_dir / self.s3_bucket_name
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.client = sys_env.get_s3_client()

    def load_files(self) -> int:
        """Load downloaded files into InfluxDB"""
        total_count = 0
        influxdb_writer = sys_env.get_influxdb_writer(self.influxdb_bucket_name, 4096, no_retention=True)
        files = sorted(self.download_dir.iterdir(), key=lambda p: p.stat().st_ctime, reverse=True)

        for f in files:
            if not f.is_file():
                continue
            if not f.name.endswith(".msgpack"):
                continue
            if self.env_name in f.name:
                try:
                    count = process_file(influxdb_writer, f)
                    total_count += count
                except Exception as e:
                    LOG.error(f"Error processing file {f}: {e}")

        influxdb_writer.close()
        return total_count

    def download_file(self, s3_name: str) -> bool:
        """Download a single file from S3 if not already downloaded"""
        if "_Metrics_" in s3_name and s3_name.endswith("msgpack.bz2"):
            file_path = self.download_dir / s3_name[:-4]
            if file_path.is_file():
                return False  # Already exists

            file_done_path = self.download_dir / (s3_name[:-4] + "__done")
            if file_done_path.is_file():
                return False  # Already processed

            try:
                download(self.client, self.s3_bucket_name, s3_name, self.download_dir)
                return True
            except Exception as e:
                LOG.warning(f"File {self.s3_bucket_name}/{s3_name} is not available: {e}")
                return False
        return False

    def get_last_date_str(self) -> str:
        """Get the last processed date string"""
        files = sorted(self.download_dir.iterdir(), key=lambda p: p.stat().st_ctime, reverse=True)
        if not files:
            return ""

        for f in files:
            if not f.name.endswith((".msgpack", ".msgpack__done")):
                continue
            try:
                last_file_name = f.resolve(strict=False).name
                parts = last_file_name.split(".")[0].split("_")
                if len(parts) >= 5:
                    yyyymmdd = parts[-2]
                    hhmm = parts[-1]
                    return f"{yyyymmdd}_{hhmm}"
            except Exception as e:
                LOG.warning(f"Error parsing filename {f.name}: {e}")
                continue

        return ""

    def update(self) -> Dict[str, int]:
        """Download and process new metric files"""
        stats = {"downloaded": 0, "processed": 0, "skipped": 0}

        last_dt_str = self.get_last_date_str()

        # Default to 28 days ago if no last date
        lookback_days_ago = datetime.today() - timedelta(days=28)
        lookback_str = lookback_days_ago.strftime("%Y%m%d") + "_0000"

        if lookback_str > last_dt_str or not last_dt_str:
            last_dt_str = lookback_str

        intervals = get_intervals(last_dt_str)
        LOG.info(f"Update {self.host}/{self.env_name}: from {intervals[0]} to {intervals[-1]}")

        if len(intervals) < 1:
            LOG.info(f"No new intervals to process for {self.host}/{self.env_name}")
            return stats

        # Download files for all intervals except the last one (might be incomplete)
        const_str = "Metrics"
        for dt_str in intervals[:-1]:
            s3_name = f"{self.env_name}_{const_str}_{dt_str}.msgpack.bz2"
            if self.download_file(s3_name):
                stats["downloaded"] += 1
            else:
                stats["skipped"] += 1

        # Load files into InfluxDB
        processed = self.load_files()
        stats["processed"] = processed

        return stats


class MetricsDataTask(BaseTask):
    """Task for downloading and processing metrics data from S3 to InfluxDB"""

    TASK_TYPE = "metrics_data"

    def __init__(self, job_id: str, job_data: Dict[str, Any], client, config: Dict[str, Any]):
        super().__init__(job_id, job_data, client)
        self.config = config
        self.download_dir = Path(config.get("download_tmp_dir", DEFAULT_DOWNLOAD_TMP_DIR))
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.hosts = self._get_hosts()
        self.stats = {
            "total_hosts": 0,
            "total_envs": 0,
            "total_downloaded": 0,
            "total_processed": 0,
            "total_skipped": 0,
            "errors": 0,
        }

    def get_task_type(self) -> str:
        return self.TASK_TYPE

    def update_progress(self, update_data: Dict[str, Any]) -> None:
        """Override to just log progress instead of calling API (scheduled task)"""
        LOG.info(f"Metrics data task progress: {update_data}")

    def _get_hosts(self) -> list[str]:
        """Get list of hosts to process from job data or use defaults"""
        hosts_data = self.job_data.get("hosts")
        if hosts_data:
            return hosts_data
        # Default to orion-01 and orion-03 as specified
        return ["orion-01", "orion-03"]

    def _process_host(self, host: str) -> Dict[str, int]:
        """Process metrics for a single host"""
        LOG.info(f"Processing host: {host}")

        if host not in DEFAULT_HOST_CONFIG:
            LOG.error(f"Unknown host: {host}")
            return {"downloaded": 0, "processed": 0, "skipped": 0, "errors": 1}

        host_stats = {"downloaded": 0, "processed": 0, "skipped": 0, "errors": 0}
        envs = DEFAULT_HOST_CONFIG[host]["envs"]

        for env_name in envs:
            try:
                LOG.info(f"Processing {host}/{env_name}")
                worker = MetricsWorker(host, env_name, self.download_dir)
                env_stats = worker.update()

                host_stats["downloaded"] += env_stats["downloaded"]
                host_stats["processed"] += env_stats["processed"]
                host_stats["skipped"] += env_stats["skipped"]

                LOG.info(f"Completed {host}/{env_name}: {env_stats}")

            except Exception as e:
                LOG.error(f"Error processing {host}/{env_name}: {e}")
                host_stats["errors"] += 1

        return host_stats

    def execute(self) -> None:
        """Execute the metrics data download and processing task"""
        try:
            self.mark_started()
            LOG.info(f"Starting metrics data task for hosts: {self.hosts}")

            self.stats["total_hosts"] = len(self.hosts)

            # Process each host
            for host in self.hosts:
                try:
                    host_stats = self._process_host(host)

                    self.stats["total_downloaded"] += host_stats["downloaded"]
                    self.stats["total_processed"] += host_stats["processed"]
                    self.stats["total_skipped"] += host_stats["skipped"]
                    self.stats["errors"] += host_stats["errors"]

                    # Count total environments processed
                    if host in DEFAULT_HOST_CONFIG:
                        self.stats["total_envs"] += len(DEFAULT_HOST_CONFIG[host]["envs"])

                    self.update_progress(
                        {
                            "progress": "running",
                            "stats": self.stats,
                        }
                    )

                except Exception as e:
                    LOG.error(f"Error processing host {host}: {e}")
                    self.stats["errors"] += 1

            LOG.info(f"Metrics data task completed. Stats: {self.stats}")
            self.mark_completed(
                {
                    "stats": self.stats,
                    "hosts_processed": self.hosts,
                }
            )

        except Exception as e:
            LOG.error(f"Error in metrics data task: {e}")
            self.mark_failed(str(e))
        finally:
            self.cleanup()

    def cleanup(self) -> None:
        """Clean up resources"""
        pass
