# Markets

Two things live here, both upstream of the hypothesis-scanning loop in
`specs/`/`ledger/`/`scan-reports/` — a strategy is only as good as the
market reality it's tested against.

## `registry.yaml`

The tradeable market universe: exchange, symbol, asset/quote, whether it's
leveraged, the order-type constraint, and the fee (negative = maker rebate).
Schema: `../schemas/market_registry.schema.json`. Loader: `../tools/markets.py`.

Every entry's exchange/symbol must exist as bronze data in Makalu — this is
the fixed, deliberately small set of markets Kilimanjaro targets, not an
open-ended list.

## `waves/` and `graphs/`

Daily volatility-vs-spread waves: for a market, at each 30-minute JST bucket
and tested horizon X (seconds), what fraction of the time does the price
move more than 1x/2x/3x the spread? This has to be answered *before*
testing any signal against a horizon — a horizon that can't reliably clear
the spread can't be profitable regardless of what the signal predicts.

Computed directly from bronze `OrderBook` (the only dataset Kilimanjaro
treats as guaranteed) via DuckDB reading Makalu's parquet files directly —
not through Makalu's query CLI, whose JSON output caps at 200 rows and
can't handle this volume. See `../tools/volatility_wave.py`'s module
docstring for the exact mid/spread definitions and bucketing rule.

```bash
# Compute a wave + graph for one market
python tools/volatility_wave.py compute --exchange GMO --symbol BTC_JPY --plot

# Answer "what's the spread/volatility/viable X" for a specific time of day
python tools/volatility_wave.py query --wave-csv markets/waves/GMO_BTC_JPY.csv --time 09:00
```

**Data coverage varies a lot by market** — confirmed against live Makalu
(2026-06-18): `GMO/BTC_JPY` (and presumably the other JPY-margin pairs) has
~340 days of bronze history, but the spot markets (`COINCHECK`, `GMO` spot,
`BITBANK`) only have 7 days. A wave computed from 7 days shows real
intraday structure but is not yet enough data to call the daily pattern
statistically stable day-over-day — treat early spot-market waves as a
first look, not a settled answer, until more history accumulates.
