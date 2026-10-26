# CLAUDE.md

## Purpose

Kilimanjaro is an alpha/edge discovery system. It proposes signal formulas,
evaluates them against historical market data, records reproducible evidence,
and stops at a human review gate. It does not contain or import production
strategy implementations.

## Dependency boundary

- Hase owns the market-data root and bronze derivations.
- Makalu owns derived market datasets and feature pipelines.
- Qate is used only for shared data-definition objects such as `Market` and
  `DtRange` when calling those data providers.
- Kilimanjaro owns discovery specifications, isolated experiment artifacts,
  statistical evaluation, the ledger, and the research service.

Do not add dependencies on a concrete trading strategy repository. Schedule and
risk assumptions used by a scan must be declared in its spec and implemented by
Kilimanjaro's generic framework.

## Entry points

`src/cli.py` is the command-line entry point. Common commands:

```text
python src/cli.py research ...
python src/cli.py generate ...
python src/cli.py grid horizon --spec SPEC --start YYYY-MM-DD --end YYYY-MM-DD
python src/cli.py loop ...
python src/cli.py factory ...
python src/cli.py signal-corr ...
python src/cli.py blend-corr ...
python src/cli.py return-eval ...
python src/cli.py ledger ...
```

The autonomous loop validates and deduplicates specs, scans them, assigns a
verdict, appends evidence to the ledger, and creates review packets. It never
promotes or deploys a strategy.

## Layout

- `src/kilimanjaro/data/`: Hase/Makalu access and isolated provider adapters
- `src/kilimanjaro/signal/`: generic expression signals
- `src/kilimanjaro/return_calc/`: reusable label/return definitions
- `src/kilimanjaro/framework/`: grid execution, schedules, gates, statistics
- `src/kilimanjaro/driver/`: proposal, scan, verdict, ledger, review workflow
- `src/kilimanjaro/research/`: durable continuous-discovery service
- `cycle/`: schemas, campaigns, hypotheses, evidence, ledger, and handoffs

## Data and evaluation rules

- Treat Hase/Makalu bronze `Trade` and `OrderBook` as the guaranteed source.
- Derive missing candidate datasets in an isolated job lake; never mutate a
  shared lake from a research job.
- Evaluate on chronological train/holdout windows and select for rank stability.
- Use bid/ask execution prices. Do not add a second spread charge.
- Record implementation hashes, package versions, data manifests, parameters,
  and random seeds so experiments remain reproducible.
- Keep promotion human-gated and treat unverified mechanisms as trend-only.

## Development

```text
python -m pytest
ruff check .
```

Preserve unrelated worktree changes. Use paths relative to the repository root
for checked-in artifacts and explicit data roots for tests and research jobs.
