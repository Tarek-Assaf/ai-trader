# Staged mutate-and-retest search

Owner approval: 2026-10-12 19:20 Riyadh (D-034). Idea (owner): take a strategy that survives one screen, modify it,
test the modification at the next level, and repeat, which may produce strategies that are reliable. This spec
makes that safe against self-deception: every variant ever tried counts against the result, and the sealed holdout
is read only once at the end.

## Rules (fixed before any run)
1. Fully declarative: mutations are changes to `StrategySpec` fields chosen from a fixed menu. No LLM, no
   generated code, no edits to gates, costs or the holdout handling.
2. One global trial counter. Every spec evaluated at any stage (including parents and every variant dropped
   early) is written to the experiment ledger. The final Gate 1 call passes `trials` = the total number of distinct
   specs evaluated in the whole search, plus the ledger's prior count for that market. The count never resets.
3. The sealed holdout is touched only by the final stage through the existing sealed reader. No mutation or
   ranking may use it.
4. At most three generations (parents -> children -> grandchildren), then stop.
5. Markets are separate; each result is reported for its own market and also checked on the other.

## Stages
- S0 Parents: take every attempt from an existing results file (Round A-C, `*_c.json`, and Round D when it
  exists) that has at least 300 trades. Keep the best 10 per market and timeframe by out-of-sample profit factor
  after costs with a positive net per trade in the training years (near misses; they have failed Gate 1, which is
  fine, they are the raw material).
- S1 Mutation menu, one change at a time from each parent (fixed list; at most 40 children per parent):
  stop/target multiples (1.0/2.0, 1.5/3.0, 2.0/4.0, 2.5/5.0, 3.0/6.0 ATR), time stop (12, 24, 36, 72 bars),
  session window (any, London, New York, London+New York), trend filter (EMA50 vs EMA200 with / against),
  volatility filter (ATR(14) below / above its 100-bar median), entry delay of one bar, skip the first 15
  minutes of a session.
- S2 Rank and confirm: rank children on the EARLY training years only (the first 60% of the training window).
  A child survives only if, on the LATER training years it was not ranked on AND on the other market (same
  spec, instrument changed), its net per trade after costs is positive, with at least 100 trades in each.
- S3 Next generation: survivors become parents (up to 3 generations).
- Final: survivors of the last generation go through the unchanged `ValidationPipeline` (Gate 1 with
  out-of-sample, walk-forward, regimes, neighbour parameters, 50% cost stress, multiple-testing adjustment and the
  sealed holdout) with the global `trials`. Report passes, near misses and the total trial count.

## Deliverables
- `src/trader/lab/staged.py` (menu, generations, trial counter; unit-tested with synthetic data that a planted
  mutation is found and that the trial count includes dropped variants) and `scripts/lab_staged_search.py` with a
  per-batch progress counter and a memory cap like `lab_run.py`.
- Evidence `docs/evidence/staged_search_<market>_<timeframe>.json`: per generation the number of specs, survivors,
  total trials so far, the best survivors, the final Gate 1 outcome and per-year diagnostics.
- Run order: 15m and 5m first (3m and 1m only if the owner asks), NAS100 then gold, one process at a time.
- Acceptance: a run that ends with zero passes is a valid result and is reported as such. The report states the
  total number of variants tried, so a pass can be judged against that number.
