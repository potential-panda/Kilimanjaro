import copy
from typing import Any, Dict

# Job data template
JOB_DATA_TEMPLATE = {
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

# Presets for different market configurations
PRESETS = {
    "xrp": {
        "config": {
            "market": "GMO:XRP_JPY",
            "ref_market": "BINANCE:XRP_USDT",
            "order_size": 200,
        },
    },
    "eth": {
        "config": {
            "market": "GMO:ETH_JPY",
            "ref_market": "BINANCE:ETH_USDT",
            "order_size": 0.2,
        },
    },
    "btc": {
        "config": {
            "market": "GMO:BTC_JPY",
            "ref_market": "BINANCE:BTC_USDT",
            "order_size": 0.005,
        },
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge override dict into base dict"""
    result = copy.deepcopy(base)

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)

    return result


def create(from_dt: str, preset_name: str = "xrp") -> Dict[str, Any]:
    """
    Create job_data by merging template with preset overrides

    Args:
        from_dt: Start date/time for the backtest
        preset_name: Name of preset to use (default: "xrp")

    Returns:
        Merged job_data dict

    Raises:
        ValueError: If preset_name is not found
    """
    if preset_name not in PRESETS:
        raise ValueError(f"Unknown preset '{preset_name}'. Available presets: {list(PRESETS.keys())}")

    # Clone template
    job_data = copy.deepcopy(JOB_DATA_TEMPLATE)

    # Set from_dt
    job_data["from_dt"] = from_dt

    # Merge preset overrides
    preset = PRESETS[preset_name]
    job_data = _deep_merge(job_data, preset)

    return job_data
