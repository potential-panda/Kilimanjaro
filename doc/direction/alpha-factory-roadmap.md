# Alpha Discovery Factory — Readiness Roadmap

**Goal:** drive this repo to a self-feeding alpha discovery factory — the 1–7
loop running forever, with steps 1–4 autonomous and 5–7 human-gated:

1. generate formula → 2. scan (param permutations) → 3. verify/report →
4. filter survivors → 5. build qate strategy → 6. Himalaya backtest → 7. prod.

**Where we are (2026-07, honest read):** the *moat* is built and sharp — the
evaluation/reward layer (train/holdout rank stability, block-bootstrap, FDR,
bid/ask cost policy, path-aware Martin/Calmar/JPY metrics), the calibration
discipline (scan↔Himalaya fidelity), the ledger, and a fast scan (~20–40s/grid).
The gap to a 24/7 factory is *hardening*, not inventing. This doc is the tracked
punch list, in priority order. Reward-first still holds; the human keeps
promotions (5–7).

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done.

---

## 1. Broaden calibration to the generator's mechanisms  ← **the real gate**

**Why:** the generator emits ExpressionSignal formulas on *fixed-horizon* and
*episode* return calcs, but only **flip + between-signal** is calibrated against
Himalaya (ETH, and now BTC). You cannot trust the judge to auto-filter on a
mechanism it has never been checked against — so scaling generation is blocked
until these mechanisms earn the same trust. Highest leverage item.

- [x] Establish the ground-truth path — **finding:** orion only backtests
      SAR-flip strategies (v1..v9), so there is NO Himalaya counterpart for
      expression mechanisms. Full calibration is **blocked on a Stage-5
      signal-driven orion strategy** (human-gated). Their execution model is
      inherited-and-verified from flip; only entry/exit-timing is unverified.
- [x] Machine-readable calibration registry — `framework/calibration.py`
      (`lookup`/`spec_mechanism`/`is_calibrated`); the review packet now stamps
      every survivor **CALIBRATED** vs **⚠️ TREND-ONLY** with a
      do-not-promote-on-scan-numbers warning. Tested. **The loop can no longer
      quietly over-trust an unverified mechanism.**
- [ ] (blocked, Stage 5) Build a signal-driven orion strategy so expression +
      fixed-horizon / episode can be Himalaya-checked; then calibrate each (a few
      param sets, varied entry conditions) and flip the registry to `calibrated`.
- [ ] Interim (no Himalaya) confidence: internal cross-validation between
      fixed-horizon and episode on the same signals (do they agree on ranking?).

**Done when:** every mechanism the generator can emit is either calibrated
(recorded) or quarantined trend-only, and Stage-A reads that status. ← **the
quarantine half is now done; the calibrate half waits on the Stage-5 strategy.**

## 2. Test hygiene + regression/invariant guards

**Why:** the real bugs we shipped and caught by hand this session (cost
double-count, scan↔Himalaya schedule mismatch, the params `schedules` KeyError)
had no test guarding them. A loop that self-modifies needs green, meaningful
tests. Today: ~118 tests, **14 dead files** (importing removed tools) + **3 real
failures**.

- [ ] Delete/rewrite the 14 stale test files (import removed tools:
      grid_scan, propose_expressions, scan_workflow, scanner, …).
- [ ] Fix the 3 currently-failing tests.
- [ ] Add regression tests encoding the caught bugs: schedule parity
      (scan == orion), cost policy (bid/ask + flat, no spread double-count),
      JPY-per-fill vs bps.
- [ ] Add invariant guards the loop can assert at runtime (causality, no
      look-ahead, dedup fingerprint, cost sanity).

**Done when:** `pytest` is green, and the caught bugs cannot regress silently.

## 3. Package the cross-repo boundaries (kill the sys.path hacks)

**Why:** four `sys.path.insert/append` reaches into sibling repos / `tools/`
(`orion_schedule`, `ledger_io`, `cli`, `api`). Fine for a script, brittle for a
long-running process.

- [ ] Make orion/qate importable via packaging (or a thin vendored shim), not path insertion.
- [ ] Move the ledger library (`tools/ledger.py`, `grid_ledger.py`) into `kilimanjaro/`
      (or a package) so the driver stops reaching into `tools/`.
- [ ] One import story; no `sys.path` mutation in library code.

**Done when:** no library module mutates `sys.path`; imports work from a clean env.

## 4. Build Stage C — the forever cadence, with guardrails

**Why:** `loop.py` walks a queue once; there is no continuous scheduler. Stage C
wraps generate+loop into a repeating cadence.

- [ ] Cadence runner (generate → loop → sleep → repeat) with resource caps and rate limits.
- [ ] Idempotency + dedup at scale (fingerprint already exists; make it the spine).
- [ ] Hard human gate on 5–7 (never auto-promote); park survivors at the review packet.
- [ ] Observability: run log, cost/time budget, "what did the loop learn today" digest.

**Done when:** the loop runs unattended for days, never crosses the human gate,
and its ledger tells you what it tried and rejected.

## 5. Deepen the generator (search, not enumeration)

**Why:** `generate.py` is 5 hardcoded template families — enumeration, not
discovery. Intentionally last (reward-first), but it's the thinnest part vs the
ambition.

- [ ] Feedback loop: bias generation toward families/regions that survived.
- [ ] Mutation / crossover (GP-style) over the expression AST, dedup-guarded.
- [ ] Guard against the AlphaGPT failure mode (cost-blind, overfit) — the reward
      layer already does this; keep it in front of every generated idea.

**Done when:** the generator proposes ideas informed by past outcomes, not a
fixed template list, without lowering the survival bar.

---

## Guiding principles (do not drift from these)

- **Reward-first, generation-second.** Never scale generation past what the judge
  can honestly reject. The moat is saying *no* cheaply and honestly.
- **Calibrate per mechanism, not per formula** (`doc/calibration.md`). Trust is
  earned once per mechanism and inherited by every formula on it.
- **Humans keep promotions.** Stages 5–7 are deliberate human acts; agents attach
  evidence to the ledger, never flip status.
- **Speed lowers the psychological cost of rejecting ideas** — keep the scan fast.
