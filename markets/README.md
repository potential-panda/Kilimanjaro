# Markets

Four things live here, all upstream of the hypothesis-scanning loop in
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

## BINANCE→GMO lead-lag wave

BINANCE isn't traded directly but is the leading exchange industry-wide; a
prior successful strategy generated signal from BINANCE and traded on GMO.
This answers, at each time of day, how strongly and stably BINANCE leads
the corresponding GMO JPY pair, and at what lag.

Uses Makalu's existing gold-layer `LeadLag` dataset (already populated for
BINANCE→GMO btc/eth/xrp, ~33 days, with EWM-decayed multi-window
cross-correlation and optimal-lag search) rather than recomputing
cross-correlation from scratch — see `../tools/lead_lag_wave.py`'s module
docstring for two real data-quality findings worth knowing before reading
the numbers: `ll_alpha_gain` isn't actually clipped to [1,100] as documented
(use the log version or the binary leading-fraction instead), and
`ll_opt_lag_ms` is a value normalized to [0,1], not literal milliseconds
(the tool denormalizes it using the param_id's encoded max lag).

```bash
python tools/lead_lag_wave.py compute --asset BTC --plot
python tools/lead_lag_wave.py query --wave-csv markets/waves/leadlag_BINANCE_GMO_BTC.csv --time 09:00
```

**COINCHECK/BITBANK lead-lag is out of scope for now** — Makalu's
`populate LeadLag` pipeline only supports GMO as the lag market today
(confirmed 2026-06-18). Revisit if a strategy needs it.

Confirmed on real data: BINANCE leads GMO/BTC_JPY 81-97% of the time across
every 30-minute bucket of the day — consistently high, with a real dip
during 09:00-10:30 JST, matching the expected "stable but wave-shaped"
pattern.

## Limit-order fill probability

On the negative-fee (maker rebate) markets in `registry.yaml`
(`order_type: limit_only` — GMO spot, BITBANK ETH/XRP spot), only limit
orders are ever explored, so the key question isn't "will the market move
past the spread" but "will a resting limit order placed X basis points away
from the best bid/ask actually fill within Y seconds." Closer to the touch
fills more often but earns a worse price; farther away earns a better price
but may not fill at all.

`tools/fill_probability.py` answers this from bronze `Trade`+`OrderBook`
directly: a hypothetical buy limit at `best_bid * (1 - X/10000)` is treated
as filled within Y seconds if any subsequent trade prints at or below that
price (symmetric for sell limits above `best_ask`). **This is a proxy, not
a true fill simulation** — it ignores queue position ahead of the
hypothetical order, so it's an upper bound on the real fill probability,
not an unbiased estimate. Documented in the module docstring, not hidden.

```bash
python tools/fill_probability.py compute --exchange GMO --symbol BTC_SPOT --plot
python tools/fill_probability.py query --wave-csv markets/waves/fillprob_GMO_BTC_SPOT.csv --time 09:00 --horizon 30
```

Confirmed on real data (`GMO/BTC_SPOT`): fill probability decreases
monotonically with distance as expected (e.g. ~20-30% at 1bps vs. 0% at
20bps+ within 30s), and follows the same low-activity dip around 09:00 JST
seen in the volatility wave — consistent with both being driven by the same
underlying trading-activity pattern.
