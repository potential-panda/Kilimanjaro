from logging import getLogger

from kilimanjaro.data_access import JobDataAccess

from .. import job_data
from ..task import Task

LOG = getLogger(__name__)

JOB_DIR = "jobs"
RESULTS_DIR = "results"


class AddBacktestTask(Task):
    def __init__(self, config):
        super().__init__()
        self.env_name_str = config["env_name"]
        self.from_dt = config["from_dt"]
        self.job_dir = JOB_DIR
        self.results_dir = RESULTS_DIR
        self.job_data_access = JobDataAccess(self.job_dir, self.results_dir)

    def execute(self) -> None:
        # Create XRP job
        xrp_job = job_data.create(self.env_name_str, self.from_dt, "xrp")
        self.job_data_access.create_job(xrp_job)

        # Create ETH job
        eth_job = job_data.create(self.env_name_str, self.from_dt, "eth")
        self.job_data_access.create_job(eth_job)

        # Create BTC job
        btc_job = job_data.create(self.env_name_str, self.from_dt, "btc")
        self.job_data_access.create_job(btc_job)
