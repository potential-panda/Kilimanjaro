import copy
from logging import getLogger

from kilimanjaro.data_access import JobDataAccess

from ..task import Task

LOG = getLogger(__name__)

JOB_DIR = "jobs"
RESULTS_DIR = "results"


JOB_DATA_TMPL = {
    "job_id": "",
    "strategy_name": "orion",
    "variant": "v601",
    "from_dt": "",
    "to_dt": "",
    "optimize": True,
    "retest": True,
    "config": {
        "market": "GMO:XRP_JPY",
        "ref_market": "BINANCE:XRP_USDT",
        "order_size": 150,
        "magnifier": 5,
    },
    "param_grid": [
        {"name": "acceleration", "default_value": 0.02, "values": [0.02], "factor": 1000},
        {"name": "step", "default_value": 0.04, "values": [0.02, 0.04], "factor": 1000},
        {"name": "maximum", "default_value": 0.2, "values": [0.2], "factor": 1000},
        {
            "name": "box_size_rate",
            "default_value": 0.0015,
            "values": [0.001, 0.0015, 0.002, 0.0025, 0.003],
            "factor": 100000,
        },
    ],
}


class AddBacktestTask(Task):
    def __init__(self, config):
        super().__init__()
        self.env_name = config["env_name"]
        self.from_dt = config["from_dt"]
        self.job_dir = JOB_DIR
        self.results_dir = RESULTS_DIR
        self.job_data_access = JobDataAccess(self.job_dir, self.results_dir)

    def execute(self) -> None:
        job_data = copy.deepcopy(JOB_DATA_TMPL)
        job_data["from_dt"] = self.from_dt
        job_data["config"]["market"] = "GMO:XRP_JPY"
        job_data["config"]["ref_market"] = "BINANCE:XRP_USDT"
        job_data["config"]["order_size"] = 200
        self.job_data_access.create_job(job_data)

        job_data = copy.deepcopy(JOB_DATA_TMPL)
        job_data["from_dt"] = self.from_dt
        job_data["config"]["market"] = "GMO:ETH_JPY"
        job_data["config"]["ref_market"] = "BINANCE:ETH_USDT"
        job_data["config"]["order_size"] = 0.2
        self.job_data_access.create_job(job_data)

        job_data = copy.deepcopy(JOB_DATA_TMPL)
        job_data["from_dt"] = self.from_dt
        job_data["config"]["market"] = "GMO:BTC_JPY"
        job_data["config"]["ref_market"] = "BINANCE:BTC_USDT"
        job_data["config"]["order_size"] = 0.005
        self.job_data_access.create_job(job_data)
