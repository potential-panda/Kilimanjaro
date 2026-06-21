# Markets

Four things live here, all upstream of the hypothesis-scanning loop in
`specs/`/`ledger/`/`scan-reports/` — a strategy is only as good as the
market reality it's tested against.

## `registry.yaml`

The tradeable market universe: exchange, symbol, asset/quote, whether it's
leveraged, the order-type constraint, and the fee (negative = maker rebate).
Also holds `default_slippage_bps` — real market-order slippage from ~1 year
of actual trading (not a per-market measurement, a single empirical default
used uniformly until per-market data justifies splitting it out). Schema:
`../schemas/market_registry.schema.json`. Loader: `../tools/markets.py`.

Every entry's exchange/symbol must exist as bronze data in Makalu — this is
the fixed, deliberately small set of markets Kilimanjaro targets, not an
open-ended list.

Both `fee_bps` and `default_slippage_bps` here are the single source of
truth for a hypothesis spec's `execution_assumptions.fees_bps`/
`slippage_bps` — `tools/validate_spec.py` rejects a spec that declares a
different number without explanation, so this can't silently drift.

## Makalu Datasets Actually Read

A precise list, not "everything Makalu has" — confirmed by checking which
datasets every `tools/*.py` module actually opens, 2026-06-21:

| Layer | Dataset | Read by | Purpose |
|---|---|---|---|
| Bronze | `OrderBook` | `scanner.py`, `volatility_wave.py`, `lead_lag_wave.py`, `fill_probability.py` | feature/signal SQL, fallback target return, bronze-direct vol/lead-lag waves |
| Bronze | `Trade` | `fill_probability.py` | limit-order fill-probability proxy |
| Research | `Label_Return` (Makalu's `Return`) | `scanner.py`, via `tools/makalu_client.py` | target forward return, when Makalu has coverage for the scope — falls back to a bronze-direct ASOF join otherwise |
| Gold | `LeadLag` | `scanner.py`, via `tools/makalu_client.py` | feature/target lead-lag, when Makalu has coverage for the pair — falls back to `lead_lag_wave.py`'s bronze-direct cross-correlation otherwise |

**Not read at all:**
- Makalu's `MarketPrice` (silver) — never read directly by Kilimanjaro. It's
  only Makalu's own internal prerequisite for `Return`/`LeadLag`; nothing
  here opens it.
- Makalu's `VolSpread` (gold) — deliberately not used. It's an EMA of
  realized volatility/spread, a genuinely different metric from
  `volatility_wave.py`'s own (probability of clearing N-times-spread within
  a horizon). Swapping one for the other would misrepresent what's being
  measured, not just relocate where it's computed.
- Fill probability has no Makalu equivalent at all — `fill_probability.py`
  is bronze-direct by necessity, not choice.

The `Return`/`LeadLag` Makalu-first behavior (check coverage, populate via
Makalu's own CLI if missing, read the result, fall back to bronze-direct
if Makalu can't help) is implemented once in `../tools/makalu_client.py` —
see its module docstring for the reasoning and the populate commands it
calls.

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

## Lead-lag wave

BINANCE isn't traded directly but is the leading exchange industry-wide; a
prior successful strategy generated signal from BINANCE and traded on GMO.
This answers, at each time of day, how strongly and stably one market
leads another, and at what lag.

**Revised 2026-06-19.** Originally read Makalu's gold-layer `LeadLag`
dataset. Found for real while building `tools/scanner.py`'s
`market_context`: that dataset only covers 2025-12-30..2026-01-31, which
doesn't overlap any window this project has actually scanned. Rewritten to
compute cross-correlation directly from bronze `OrderBook` (1-second
resampled mid-price series, per-day peak-lag search over [-10s, +10s]) --
the same Bronze-First principle as the volatility wave. Also generalized
beyond the original BINANCE→GMO-JPY-margin scope: `compute_wave()` takes
any `(feature_exchange, feature_symbol, target_exchange, target_symbol)`;
`--asset` remains a convenience shorthand for the three originally-known
pairs.

```bash
python tools/lead_lag_wave.py compute --asset BTC --plot
python tools/lead_lag_wave.py compute --feature-exchange BINANCE --feature-symbol BTC_USDT --target-exchange GMO --target-symbol BTC_SPOT
python tools/lead_lag_wave.py query --wave-csv markets/waves/leadlag_BINANCE_GMO_BTC.csv --time 09:00
```

Confirmed on real data (60-day window, BINANCE→GMO/BTC_JPY): BINANCE leads
**49-82%** of the time depending on bucket -- not the "81-97%" figure the
old gold-dataset-derived number gave (computed over a different period and
methodology). Peak correlation strength is at 23:30-01:00 JST (~0.40-0.46),
decaying to a trough around 13:00-19:00 (~0.12-0.18) -- the low-activity
daytime hours are measurably less reliably led, not just less volatile.

## Static favorable trading window (`regimes/`)

`tools/regime_window.py` combines the waves above into a single, static,
history-derived answer to "which 30-minute JST buckets are actually good
to trade in for this market (or feature/target pair)" -- the intersection
of top-tercile volatility/spread, top-tercile lead-lag strength (if
cross-market), and top-tercile fill ease (if the target is `limit_only`).
A bucket only counts as favorable if every *provided* criterion agrees --
a criterion that doesn't apply (e.g. no lead-lag for a same-market
hypothesis) is omitted, never silently assumed favorable.

**Deliberately static — built once from a separate wave artifact, never
computed live on the window being scored.** Two reasons: (1) the whole
value of the wave indicators is that the pattern is knowable *in advance*,
before the trading day starts -- a live filter only tells you "today
looked good" after part of it already happened; (2) picking the favorable
window from the same data the signal is scored on would be a
regime-selection analogue of the single-cell-overfit problem
`tools/threshold_evaluator.py` already guards against for horizon and
threshold choice.

```bash
python tools/regime_window.py --key BINANCE_BTC_USDT__GMO_BTC_JPY \
  --vol-wave markets/waves/GMO_BTC_JPY.csv \
  --lead-lag-wave markets/waves/leadlag_BINANCE_GMO_BTC.csv
```

`tools/scanner.py` looks up the artifact automatically (by feature/target
market) and reports a parallel `regime_restricted` view in every scan
report, alongside (never replacing) the full-window result --
`threshold_verdict.regime_restricted_cost_buffer` is informational only
and never affects the official `status`.

**Coverage is currently limited to the markets this project has already
tested in depth**: `BINANCE_BTC_USDT__GMO_BTC_JPY` (long history, ~340
days), `BINANCE_BTC_USDT__GMO_BTC_SPOT` and `BITBANK_XRP_SPOT` (only the
7-8 days those markets have bronze data for -- noisier, a first look, not
a settled answer, same caveat as the spot-market waves above). A
hypothesis on any other market correctly reports
`regime_restricted.available: false` with a reason, not a silent guess.

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
