import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from logging import getLogger
from pathlib import Path

import msgpack
from qate.core.ev_type import EventType
from qate.core.model import ExchangeName, Market, Symbol, TimeSeriesData
from qate.env.sys_env import get_s3_client
from qate.store.msgpack_loader import MarketDataLoader
from qate.store.s3 import download
from qate.util.serializer import OrderBookSerializer, TradeSerializer

from ..task import Task

LOG = getLogger(__name__)

# Default configuration
DEFAULT_OUTPUT_DIR = "/panda/market-data"
DEFAULT_DOWNLOAD_TMP_DIR = "/panda/tmp_download"
DEFAULT_MERGE_TMP_DIR = "/panda/tmp_merge"

# Default markets to process
DEFAULT_MARKETS = [
    Market(ExchangeName.BINANCE, Symbol.BTC_USDT),
    Market(ExchangeName.BINANCE, Symbol.ETH_USDT),
    Market(ExchangeName.BINANCE, Symbol.XRP_USDT),
    Market(ExchangeName.BINANCE, Symbol.SOL_USDT),
    Market(ExchangeName.GMO, Symbol.BTC_JPY),
    Market(ExchangeName.GMO, Symbol.ETH_JPY),
    Market(ExchangeName.GMO, Symbol.XRP_JPY),
    Market(ExchangeName.GMO, Symbol.SOL_JPY),
]

# S3 bucket configuration
BUCKETS = {
    "vineyard-01": ["panda-cc-i-13100000693141", "CHIHIRO_TAKAO"],
    "vineyard-02": ["panda-cc-i-14100000694045", "CHLOE_BINGHAM"],
    "vineyard-01_a": ["panda-cc-vineyard-01", "CHIHIRO_TAKAO"],
    "vineyard-01_b": ["panda-cc-vineyard-01", "CARLY_GOODWIN"],
    "vineyard-02_a": ["panda-cc-vineyard-02", "CHLOE_BINGHAM"],
    "vineyard-11": ["panda-cc-vineyard-11", "CASSANDRA_HILL"],
    "vineyard-12": ["panda-cc-vineyard-12", "CHIHIRO_TAKAO"],
    "margaux": ["panda-cc-margaux", "CASSANDRA_HILL"],
    "margaux-v1": ["panda-cc-i-53100000522554", "CARLY_GOODWIN"],
}


class DataSource:
    """Helper class for merging multiple data series"""

    class Series:
        def __init__(self, data: list):
            self.data = data
            self.pos = 0

        def rewind(self):
            self.pos = 0

        @property
        def no_more_data(self) -> bool:
            return self.pos == len(self.data)

        @property
        def top_ts(self) -> TimeSeriesData:
            return self.data[self.pos][1].get_ts()

        def get(self) -> tuple[str, TimeSeriesData]:
            item = self.data[self.pos]
            self.pos += 1
            return item

    def __init__(self, data: list[list[tuple[str, TimeSeriesData]]]):
        self.series_list = [self.__class__.Series(item) for item in data]

    def get(self) -> tuple[str, TimeSeriesData]:
        min_ts = time.time()
        min_series = None

        for series in self.series_list:
            if series.no_more_data:
                continue
            if series.top_ts < min_ts:
                min_ts = series.top_ts
                min_series = series

        if not min_series:
            return None
        return min_series.get()


def serialize(data: tuple[str, object]) -> list:
    """Serialize market data events"""
    event_type, obj = data
    if event_type == EventType.MARKET_ORDER_BOOK:
        return OrderBookSerializer.serialize(obj)
    elif event_type == EventType.MARKET_TRADE:
        return TradeSerializer.serialize(obj)
    else:
        raise Exception(f"Unexpected event type {event_type}")


def get_step(exchange: ExchangeName) -> float:
    """Get time step for merging based on exchange"""
    if exchange == ExchangeName.BINANCE:
        return 0.2
    elif exchange == ExchangeName.COINCHECK:
        return 1.0
    return 0.5


def get_prefix(env: str = None, exchange: str = None, symbol: str = None, v: int = 1):
    """Get S3 prefix for market data files"""
    if v == 1:
        params = [p for p in [env, "MarketData", exchange, symbol] if p]
    else:
        if symbol:
            symbol = "_" + symbol
        params = [p for p in [env, exchange, symbol] if p]

    return "_".join(params)


def get_all_files(s3, exchange: ExchangeName, symbol: Symbol, target_dates: list[str] = None) -> dict[str, list]:
    """Get all S3 files for a given market

    Args:
        s3: S3 client
        exchange: Exchange name
        symbol: Symbol
        target_dates: Optional list of dates in YYYYMMDD format to filter files
    """
    from qate.store.s3 import list_files

    result = {}
    hosts = []
    if exchange == ExchangeName.BINANCE:
        hosts = [
            "vineyard-11",
            "vineyard-12",
            "margaux-v1",
        ]
    else:
        hosts = [
            "vineyard-01",
            "vineyard-02",
            "vineyard-01_a",
            "vineyard-01_b",
            "vineyard-02_a",
        ]

    for host in hosts:
        bucket, env = BUCKETS[host]
        prefix = get_prefix(env, exchange.name, symbol.name, 1)
        res = list_files(s3, bucket, prefix)
        for name, items in res.items():
            # Filter by target dates if specified
            if target_dates:
                # Check if any of the target dates is in the file name
                if not any(date in name for date in target_dates):
                    continue

            if name not in result:
                result[name] = []
            result[name].extend(items)

    return result


def merge_market_data(merge_files: list[Path], exchange: str, symbol: str):
    """Merge multiple market data files into one"""
    LOG.info(f"Merging {merge_files} for {exchange} {symbol}")
    market = Market(ExchangeName[exchange], Symbol[symbol])
    loader = MarketDataLoader(market)
    series: list[list[tuple[str, TimeSeriesData]]] = []
    for tmp_file in merge_files:
        series.append(loader.load(tmp_file))

    start_ts = math.floor(min(event_list[0][1].get_ts() for event_list in series))
    end_ts = math.ceil(max(event_list[-1][1].get_ts() for event_list in series))
    end_ts += 1.0
    period = end_ts - start_ts
    LOG.info(f"Merge period: {end_ts} - {start_ts} = {period}")

    step = get_step(market.exchange_name)

    fixed_cnt = 0
    empty_cnt = 0
    pos_a = 0
    pos_b = 0
    series_a = series[0]
    series_b = series[1]
    merged = []
    current_box = start_ts
    while current_box < end_ts:
        current_box += step
        has_data = False
        while pos_a < len(series_a):
            item_a = series_a[pos_a]
            ts = item_a[1].get_ts()
            if ts < current_box:
                merged.append(item_a)
                has_data = True
                pos_a += 1
            else:
                break

        while pos_b < len(series_b):
            item_b = series_b[pos_b]
            ts = item_b[1].get_ts()
            if ts < current_box:
                if not has_data:
                    fixed_cnt += 1
                    merged.append(item_b)
                    has_data = True
                pos_b += 1
            else:
                break

        if not has_data:
            empty_cnt += 1

    LOG.info(f"Fixed {fixed_cnt} boxes, empty {empty_cnt} boxes")
    return [serialize(x) for x in merged]


@dataclass
class Config:
    output_dir: str = DEFAULT_OUTPUT_DIR
    download_tmp_dir: str = DEFAULT_DOWNLOAD_TMP_DIR
    merge_tmp_dir: str = DEFAULT_MERGE_TMP_DIR


def load_config():
    # TODO read {__class__.__name__}.json from current dir
    return Config()


class MarketDataTask(Task):
    """Task for downloading, merging and saving market data"""

    def __init__(self, params=None):
        super().__init__()
        self.config = load_config()
        self.output_dir = self.config.output_dir
        self.download_tmp_dir = self.config.download_tmp_dir
        self.merge_tmp_dir = self.config.merge_tmp_dir

        # Parse market parameter - format: "EXCHANGE:SYMBOL" (e.g., "GMO:BTC_JPY")
        # If not specified, use DEFAULT_MARKETS
        self.markets = self._get_markets(params)
        self.s3_client = None

        # Parse days_ago parameter - download data for multiple days
        # If days_ago=3, download data for -3, -2, -1 days ago
        self.target_dates = []
        if params and isinstance(params, dict) and "days_ago" in params:
            days_ago = params["days_ago"]
            today = datetime.now()
            for i in range(days_ago, 0, -1):
                target_date = today - timedelta(days=i)
                self.target_dates.append(target_date.strftime("%Y%m%d"))
            LOG.info(f"Target dates for download: {self.target_dates}")

        self.stats = {
            "total_markets": 0,
            "processed_markets": 0,
            "downloaded_files": 0,
            "merged_files": 0,
            "skipped_files": 0,
            "errors": 0,
        }

    def _get_markets(self, params=None) -> list[Market]:
        """Get markets to process.

        Args:
            params: Optional dict with 'market' key in format "EXCHANGE:SYMBOL" (e.g., "GMO:BTC_JPY")

        Returns:
            List of Market objects to process
        """
        if params and isinstance(params, dict) and "market" in params:
            market_str = params["market"]
            try:
                # Parse format: "EXCHANGE:SYMBOL"
                exchange_str, symbol_str = market_str.split(":")
                exchange = ExchangeName[exchange_str.upper()]
                symbol = Symbol[symbol_str.upper()]
                market = Market(exchange, symbol)
                LOG.info(f"Using specified market: {market}")
                return [market]
            except (ValueError, KeyError) as e:
                LOG.error(f"Invalid market format '{market_str}'. Expected format: 'EXCHANGE:SYMBOL'")
                raise ValueError(f"Invalid market format: {market_str}. Error: {e}")

        return DEFAULT_MARKETS

    def _ensure_directories(self):
        """Ensure all required directories exist"""
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        Path(self.download_tmp_dir).mkdir(parents=True, exist_ok=True)
        Path(self.merge_tmp_dir).mkdir(parents=True, exist_ok=True)

    def _process_market(self, exchange_name: ExchangeName, symbol: Symbol):
        """Process a single market: download, merge, and save"""
        LOG.info(f"Processing market: {exchange_name.name} {symbol.name}")

        result = get_all_files(self.s3_client, exchange_name, symbol, self.target_dates if self.target_dates else None)

        # Process files that don't need merging
        for name, items in result.items():
            if len(items) > 1:
                continue

            dist_file = Path(self.output_dir) / f"{name}.msgpack"
            if dist_file.is_file():
                LOG.debug(f"Dist File {dist_file} already exists")
                self.stats["skipped_files"] += 1
                continue

            item = items[0]
            downloaded_file_path = download(self.s3_client, item.bucket, item.name, Path(self.download_tmp_dir))
            os.rename(downloaded_file_path, dist_file)
            self.stats["downloaded_files"] += 1
            LOG.info(f"Downloaded: {dist_file}")

        # Process files that need merging
        cnt = 0
        for name, items in result.items():
            if len(items) < 2:
                continue

            dist_file = Path(self.output_dir) / f"{name}.msgpack"
            if dist_file.is_file():
                LOG.debug(f"Dist File {dist_file} already exists")
                self.stats["skipped_files"] += 1
                continue

            merge_files = []
            for item in items:
                file_name = item.name[:-4] if item.name.endswith(".bz2") else item.name
                merge_tmp_file = Path(self.merge_tmp_dir) / file_name
                merge_files.append(merge_tmp_file)
                if merge_tmp_file.is_file():
                    LOG.info(f"Merge File {merge_tmp_file} already exists")
                    continue
                downloaded_file_path = download(self.s3_client, item.bucket, item.name, Path(self.download_tmp_dir))
                os.rename(downloaded_file_path, merge_tmp_file)

            merged = merge_market_data(merge_files, exchange_name.name, symbol.name)
            with open(dist_file, "wb") as f:
                packed_data = b"".join(msgpack.packb(item, use_bin_type=True) for item in merged)
                f.write(packed_data)

            self.stats["merged_files"] += 1
            LOG.info(f"Merged and saved: {dist_file}")

            cnt += 1
            if cnt > 100:
                LOG.warning("Reached merge limit of 100 files per market")
                break

        self.stats["processed_markets"] += 1

    def execute(self) -> None:
        """Execute the market data download and merge task"""
        try:
            LOG.info(f"Starting market data task for {len(self.markets)} markets")

            # Ensure directories exist
            self._ensure_directories()

            # Initialize S3 client
            self.s3_client = get_s3_client()

            # Process each market
            self.stats["total_markets"] = len(self.markets)
            for market in self.markets:
                try:
                    self._process_market(market.exchange_name, market.symbol)
                except Exception as e:
                    LOG.error(f"Error processing market {market}: {e}")
                    LOG.exception(e)
                    self.stats["errors"] += 1

            LOG.info(f"Market data task completed. Stats: {self.stats}")

        except Exception as e:
            LOG.error(f"Error in market data task: {e}")
        finally:
            self.cleanup()

    def cleanup(self) -> None:
        """Clean up resources"""
        self.s3_client = None
