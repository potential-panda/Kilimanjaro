# Autonomous Readiness Plan

## What an agent can execute end-to-end today

- **Data inventory** (stage 1): fully executable today via Makalu's
  `cli.py query` and `doc/catalog.yaml` — no new tooling needed.
- **Hypothesis generation and spec drafting** (stages 2–3): executable today
  as a structured-writing task once the spec schema below exists (it does
  not yet — see gaps).
- **Backtest** (stage 8): executable today via Himalaya for any hypothesis
  that's already been turned into a strategy variant — Himalaya's grid +
  metrics pipeline is mature.

## Where the loop is too slow, expensive, or ambiguous today

- **Signal scan** (stage 4) does not exist as a tool — today the only way to
  get feedback on a feature is to implement it as a strategy and run
  Himalaya, which is exactly the expensive path the brief wants to avoid
  running on every idea.
- **Feasibility/promotion gate** (stage 5) has no documented thresholds to
  apply consistently — currently it would be ad hoc per person/session.
- **Ledger** (stage 6) does not exist — there is no durable place to check
  "have we tried this before" or "why was this rejected," so an agent today
  would either skip this step or redo prior work blind.
- **Spec format** (stage 3) is undefined — without it, hypothesis quality and
  scan input are unstructured, making both the scan and the ledger
  inconsistent across sessions.

## Minimum signal scanner requirements (v1)

Must, at minimum:
1. Accept a feature expression as a **SQL expression over whitelisted
   Makalu dataset/column names only** — `BookState`, `BookChange`,
   `MarketPrice`, `TradeAggression`, `VolSpread`, `LeadLag`,
   `Feature_Mid`/`Feature_NearHFT`. Arbitrary Python feature transforms are
   explicitly deferred until a sandboxing/review mechanism exists; this is
   workable for v1 because the named datasets already cover the brief's
   priority taxonomy families (see `hypothesis-taxonomy.md`).
2. Pull the relevant window across bronze/silver/gold/feature layers via
   the existing Makalu query path (no new data pipeline).
3. Compute the full metric set from `evaluation-framework.md` section 1
   (IC, hit rate, bucketed return, decay, autocorrelation-corrected
   significance, stability by day/symbol/regime).
4. Apply the documented pass/reject thresholds and emit a single structured
   report (pass/fail + full grid, not just best cell) that becomes a ledger
   attachment.

It does **not** need: order simulation, multi-strategy comparison, a UI, or
integration with Himalaya's execution path. It is a statistics tool over a
dataframe, not a backtest engine.

## Minimum research ledger requirements (v1)

Must, at minimum, record per hypothesis: id, title, family (from the
taxonomy), edge mechanism (free text, required non-empty), feature
definition (including the source dataset/layer, e.g. `BookState` or
`Feature_Mid`), data window used, scan config + scan metrics (or a
reference to the scan report), backtest link if any, decision, reason for
that decision, and next suggested experiment.

- **Source dataset versioning**: if the feature definition or target uses a
  research-layer dataset (`TripleBarrierLabel*`, `BuyLabel_tmp`), the entry
  must record that dataset's `param_id` and a build/regen identifier. The
  catalog documents this layer as ephemeral and rebuildable — without
  pinning the version used, a scan-passed decision could silently become
  unreproducible if the label is regenerated with different parameters.
- **Decision states**: `rejected` / `needs-more-data` / `scan-passed` /
  `backtest-passed` / `awaiting-promotion-decision` / `promoted` /
  `retired`. The original five-state list collapsed the post-backtest
  human gate (workflow-strategy.md gate 2) into `backtest-passed`, leaving
  no distinct state for "backtest succeeded, pending the
  strategy-library promotion decision" vs. "promoted" vs. "backtest
  succeeded but promotion was declined." Splitting these three apart is a
  one-time schema fix, applied before any ledger entries are written.

- **Format**: plain Markdown or JSON files under version control in
  Kilimanjaro is sufficient for v1 — this keeps the ledger reviewable in
  PRs and avoids standing up new infrastructure before the workflow has
  proven it needs a database. A move to SQLite/DuckDB/Parquet is a natural
  v2 step once entry volume makes file-based search too slow, not a v1
  requirement.
- **Search**: agents grep/query the ledger by family, mechanism keywords, or
  symbol/exchange before drafting a new spec — feasible with flat files at
  v1 volume (tens to low hundreds of entries).
- **Grouping**: parameter variations and regime-specific reruns of the same
  underlying mechanism share a parent hypothesis id with a suffix, so the
  ledger can distinguish "a new idea" from "a variant of an existing idea."

## Human review points (restated from workflow-strategy.md)

1. Scan -> backtest promotion (cost gate).
2. Backtest -> strategy-library promotion (live-risk gate).
3. Any change to scan thresholds or the taxonomy itself (meta-decision gate).

## Ongoing audit cadence for autonomous stages 1–6

A one-time bootstrap validation (step 4 below) is not sufficient as a
standing control once the loop runs unsupervised at volume — a scanner bug,
a misapplied threshold, or an unnoticed research-layer label regeneration
could otherwise poison the ledger indefinitely with no one checking. v1
adds a minimum standing audit:

- **Per-family bootstrap**: a **one-time gate per taxonomy family** — the
  first 5 autonomous runs of a family are reviewed by a human before
  further unsupervised volume on that family. It does not re-trigger on its
  own; the only thing that re-opens it is the existing taxonomy/threshold-
  change gate (gate 3 above), which already requires human review whenever
  the taxonomy or scan thresholds change.
- **Ongoing sampling**: a human samples at least 10% of `rejected` entries
  and 100% of `scan-passed` entries each week, checking specifically for
  (a) threshold misapplication, (b) stale or unpinned research-layer
  dataset versions, and (c) look-ahead/leakage in the falsification test.
  100% coverage on `scan-passed` is deliberate: these are the entries that
  consume the next, more expensive stage of the loop, so a false positive
  there is more costly than a false `rejected`.
- **Confirmed-bug response**: any audit finding that traces to a scanner or
  threshold bug (a) triggers a re-check of all entries produced since that
  bug was introduced, and (b) **halts further autonomous runs in the
  affected scope — the specific taxonomy family if the bug is
  family-specific, or the scanner entirely if the bug is in shared scanner
  code — until the fix is verified.** Autonomous runs do not resume in that
  scope until the recheck is complete and the fix is confirmed; this
  prevents the backlog of bad entries from growing faster than it can be
  cleared.

## Smallest v1 workflow that can run repeatedly and learn

1. Build the hypothesis spec schema (a template/schema file, not a service).
2. Build the ledger as version-controlled Markdown/JSON files with a fixed
   schema, plus a documented convention for searching it before proposing
   new ideas.
3. Build the signal scanner as a standalone script/tool against Makalu's
   existing query path, implementing the metric set and thresholds in
   `evaluation-framework.md`.
4. Run the loop manually end-to-end on 3–5 taxonomy families to validate the
   spec schema, scanner output, and ledger schema are actually sufficient
   before investing further.
5. Only after step 4 demonstrates the loop holds together, let an agent run
   stages 1–6 autonomously per session, with a human still gating every
   stage-7 promotion.

This is intentionally a thin slice: spec schema + ledger + scanner, run
against existing Makalu/Himalaya infrastructure, with no new pipelines, no
new strategy abstractions, and no code generation. Strategy code generation
and any move toward automated promotion are explicitly deferred to a future
direction, to be revisited once the ledger shows enough scan-passed
hypotheses to justify it.
