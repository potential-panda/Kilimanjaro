# Overlapping returns, and what an "episode" is

This note records a statistical hazard baked into how the expression scanner
measures signals, why it inflates our confidence, and the concrete defenses the
framework uses (and will use) against it. It also pins down the definition of an
**episode** — the natural non-overlapping unit that most of the fixes rely on.

Read `doc/scan-evaluation.md` first for the split / rank-stability / verdict
machinery; this note is about the *inference* underneath a single cell of that
grid.

---

## 1. The problem

An expression signal is evaluated on a **1-second grid**. At most seconds the
condition is "on", and we measure a return over some horizon `H` (seconds) ahead
— either a fixed horizon (`FixedHorizonReturn`, bid/ask at `ts+H`) or the
run to the next flip. Two observations one second apart therefore look forward
into windows that overlap by `H-1` seconds. They share almost all of their
price path.

That violates the one assumption every textbook t-test, Sharpe ratio, and
p-value leans on: **independent observations**. The consequences:

- **Standard errors are too small.** With `H`-second overlap and per-second
  sampling, each return shares information with roughly its `H` neighbours. The
  variance of the *mean* is understated by up to a factor of `~H`, so the
  reported `t_stat` is inflated by up to `~sqrt(H)`. A horizon of 60s can turn a
  genuinely marginal edge into a `t = 8` that means nothing of the sort.
- **`n` is a lie for inference.** We may print `n = 2,000,000` trades, but the
  number of *independent* looks is closer to `n / H`. The point estimate
  (`avg_net`) is fine — overlap biases the *mean* essentially not at all — it is
  only the **uncertainty** around it that is corrupted.
- **Autocorrelation and regime clustering** make it worse: returns cluster in
  time (a trending hour produces a run of same-signed returns), so even beyond
  the mechanical horizon overlap the series is serially dependent.

This is a classic, well-studied problem — Hansen–Hodrick (1980) and Newey–West
(1987) are the standard references. It is not unique to us; it is what you get
whenever you sample a signal faster than your holding period.

**Bottom line:** the point estimate is trustworthy, the significance is not. So
overlap is dangerous for *acceptances* (it manufactures false confidence) and
harmless for *rejections* (if the edge is zero even with inflated `t`, it is
zero). That asymmetry is exactly why the funnel is safe to run fast on
overlapping data at the screening tier — see §4.

---

## 2. Determining an episode

Most of the defenses below need a **non-overlapping unit** to sample or measure
on. That unit is the *episode*, and we already compute it.

> **An episode is a maximal continuous run of seconds during which the signal
> condition holds** — from the second it crosses the threshold from below
> (`age = 0`) until the second before it drops back under.

The machinery is `src/kilimanjaro/op/age.py::threshold_age`:

```python
above   = series.abs() > threshold
crossed = above & ~above.shift(1, fill_value=False)   # rising edge only
group   = crossed.cumsum()                            # <-- unique id per episode
age     = group.groupby(group).cumcount().astype(np.int32)
age[~above] = -1
```

Two things fall out of this for free:

- **`group = crossed.cumsum()` is the episode id.** It increments by 1 at every
  rising edge and is constant for the whole run, so every second inside one
  episode carries the same `group` value. Grouping trades by `group` (over the
  `above` mask) collapses an episode to a single event.
- **`age` locates you within the episode.** `age = 0` is the crossing instant;
  `age = k` is `k` seconds into the run; `age = -1` means no episode is active.

Worked example from the docstring (`threshold = 0.3`):

```
series : [-0.1, -0.4,  0.2,  0.4,  0.5,  0.1,  0.6]
|s|>0.3: [  F,    T,    F,    T,    T,    F,    T ]
crossed: [  F,    T,    F,    T,    F,    F,    T ]
group  : [  0,    1,    1,    2,    2,    2,    3 ]   # 3 episodes: {1},{2},{3}
age    : [ -1,    0,   -1,    0,    1,   -1,    0 ]
```

Three episodes, one of them two seconds long. The signal was "on" for four
seconds total but there are only **three independent events**. `ExpressionSignal`
already gates on this via `min_age` / `max_age`, so restricting a scan to
`age == 0` yields exactly **one row per episode** — the crossing — which is the
event-sampling fix in §3.

An episode is the honest atom of a level signal: entering, holding, and exiting
one position. Counting episodes instead of seconds is what turns an inflated
`n = 2M` into an interpretable event count.

---

## 3. The fixes

Two independent families. Family A leaves the sampling alone and corrects the
*statistics*; Family B changes *what we sample* so the statistics need no
correction. They compose.

### Family A — fix the inference, keep per-second sampling

1. **Block bootstrap (already in the framework).**
   `stats.py::block_bootstrap_pvalue` resamples the return series in contiguous
   blocks of length `~n^(1/3)` (circular moving-block), which preserves local
   dependence instead of assuming it away. The reported `p_value_bootstrap` is
   therefore robust to the overlap in a way `p_value_1sided` (plain t) is not.
   When the two disagree, **believe the bootstrap.**
   - Caveat: for level signals `n` can hit millions; we decimate to
     `_BOOT_MAX_N = 100_000` in order-preserving stride before bootstrapping.
     That keeps the block structure and is *conservative* (smaller effective n).
   - To make it exactly horizon-aware, set the block length `>= H` rather than
     `n^(1/3)`; `n^(1/3)` already exceeds `H` once `n > H^3` (e.g. `H=60` needs
     `n > 216k`, easily met by level signals), so the default is usually fine.

2. **Effective sample size.** As a back-of-envelope sanity check,
   `n_eff ~= n / H`, and a de-overlapped t is roughly `t_reported / sqrt(H)`.
   Worked: a scan showing `t = 65` on `H = 120` deflates to `~65/sqrt(120) ~= 6`
   — still significant, but a very different claim. Reflex: **mentally divide any
   level-signal t by `sqrt(H)` before believing it.**

3. **Newey–West / HAC standard errors** are the formal version of (1)+(2). We
   have not needed them given the bootstrap, but they are the textbook move and
   worth adding if we ever want a closed-form corrected t in the output table.

### Family B — fix the sampling, so returns don't overlap

4. **Event / episode sampling (available today).** Take one observation per
   episode at the crossing: filter `age == 0` (i.e. `max_age = 1`). Consecutive
   crossings are `>= 1` episode apart, so with a horizon shorter than the gap
   between episodes the overlap is gone and the plain t-test is legitimate again.
   The cost is throwing away within-episode data; the gain is honest inference.

5. **Position-process measurement — `EpisodeReturn` (proposed, not yet built).**
   Instead of measuring a fixed horizon from every second, measure the **actual
   trade the episode implies**: enter at the crossing (`age = 0`), hold while the
   condition persists, exit when it clears — with a max-hold cap. This yields
   **exactly one return per episode, non-overlapping by construction**, and
   `avg_hold_s` becomes a real distribution instead of a constant `H`. It is also
   the natural home for **bid/ask execution** (enter at ask, exit at bid), which
   is the spread-aware test the basis-follow candidate needs before it can be
   taken seriously as anything but a mid-to-mid curiosity. This is the main piece
   of future work this note motivates.

6. **Daily IC aggregation.** Reduce each day to one number (e.g. the day's
   information coefficient or mean return), then do inference across days. Days
   are much closer to independent than seconds. This matches the spec's
   `significance_method: block_bootstrap_by_day`.

---

## 4. How this maps onto the tiers

The funnel (`user-research-philosophy`: expression scan -> grid scan -> Himalaya)
already handles the asymmetry from §1 sensibly, and gets one concrete addition:

- **Screening tier (expression scan).** Keep per-second sampling for speed — it
  is fine for *killing* ideas, where inflated significance is harmless. Defenses:
  trust `p_value_bootstrap` over the t-test, apply the `sqrt(H)` reflex, and lean
  on **rank stability across the train/holdout split**, which is far more robust
  to overlap than any single p-value because overlap can't fake *consistency*
  across two disjoint time windows. **Add an `n_episodes` column** (distinct
  `group` count) next to `n` so the real event count is always visible — a signal
  with `n = 2M` but `n_episodes = 300` is wearing a costume.
- **Candidate tier (anything that survives screening).** Build `EpisodeReturn`
  (§3.5) and re-measure. Non-overlapping returns + real bid/ask execution + a
  true holding-period distribution. This is where an acceptance actually earns
  its confidence, and it feeds the grid scan / Himalaya decision.

This tier is now **automated** in the scanner loop (`driver/episode_retest.py`,
steps 9b/9c): the screening scan nominates its rank-stable combos, they are
re-measured on non-overlapping episodes, and the pass/fail verdict is decided on
those honest numbers — positive in both windows + FDR on the holdout episode
p-value (BH family = the full screening grid, so the relaxed screening gate does
not weaken the correction). The screening tier's own p-values never gate a
verdict.

So: **overlap never reaches a promotion decision.** It is confined to the
screening tier, where its only failure mode — over-optimism — is caught by the
holdout split and washed out entirely once a survivor is re-measured per episode.

---

## Status of the defenses

| defense | status |
|---------|--------|
| Block bootstrap p-value | **built** (`stats.py::block_bootstrap_pvalue`) |
| Episode identification | **built** (`op/age.py::threshold_age`, `group` id) |
| Event sampling (`age == 0`) | **available** via `ExpressionSignal` age gate |
| Episode id on signal rows | **built** (`op/age.py::threshold_episode`; emitted by ExpressionSignal) |
| `EpisodeReturn` calc + bid/ask execution | **built** (`return_calc/episode.py`; `cli.py grid expr` spec `return_calc: episode`) |
| Automated candidate tier (episode re-test in the loop) | **built** (`driver/episode_retest.py`; loop steps 9b/9c; verdict via `policy.evaluate_episode`) |
| `n_episodes` + `n_eff` columns in per-second output | **built** — distinct episode count and effective sample size (`stats.py`); n=930k with n_eff=20k flags an overlap-inflated sample |
| Newey–West / HAC closed-form SE | optional; bootstrap covers it for now |
