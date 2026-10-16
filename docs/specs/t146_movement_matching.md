# T-146: Movement-matching study (design note)

Owner request (D-038, 2026-10-13 and 2026-10-16): find out how many times a market movement
repeated in past years and what followed, comparing the SHAPE of the movement, not exact values.
Price only, no news. Owner addition (2026-10-16 22:36): the agent should also draw the history as
visual charts so it, and the owner, can see when a movement replicated.

Diagnostic only. Nothing here counts toward Gate 1, the trial counter, the ledger or promotion
files. No gate or cost config edits. The sealed 3-month holdout is never read. No broker calls, no
paper trading. Costs for netting: NSXUSD 4 pts, XAUUSD 1.6 pts per round trip (assumed, T-143); the
recorded real spreads (NAS100 1.8, XAUUSD 0.5) are reported alongside as a sensitivity line only.

## 1. Data and split (fixed in advance)
- Series: NSXUSD and XAUUSD from the HistData lake. Timeframes 1m (mandatory), 3m, 5m (main), 60m
  (extra). 1m runs first.
- History 2022-01-01 up to the day before the sealed holdout starts (take the date from the existing
  holdout config; do not open holdout files).
- In-sample (shape library and parameter choice): 2022-01-01 to 2024-12-31.
  Out-of-sample (reported as the real result): 2025-01-01 to the day before the holdout.
- A match is only valid if the matched window ends strictly before the query window starts.
  Within the in-sample part, a query may only match earlier windows.

## 2. Window representation
- Window length N in candles from the pre-registered set {30, 60, 120}.
- Normalise: for each window take close - close[0], divided by ATR(14) measured at the window start
  (ATR from data before the window, no look-ahead). This makes quiet and wild periods comparable.
- Also store the window's range in ATR and its start hour (session) as labels, not as match inputs.

## 3. Similarity (two methods, both pre-registered)
- Method A (main): dynamic time warping on the normalised window with a Sakoe-Chiba band of 10% of
  N. Use an early-abandon lower bound (LB_Keogh) so 1m scans stay feasible.
- Method B (baseline): plain Pearson correlation of the normalised windows, plus a check that the
  ATR-range of the two windows is within a factor of 1.5.
- Candidate queries: every window start on a stride of N/4 candles (not every candle) so runtime is
  bounded. State the stride in the evidence.
- Keep the top K=20 matches per query (K fixed in advance). A match must be at least one window
  length away from a better match (overlap removal: it counts once).
- Report how often Method A and Method B pick the same matches.

## 4. What is measured after each match
- Forward move at horizons H in {6, 12, 30} candles after the window end: in ATR and in points,
  direction, and the time it took to reach a 1 ATR move in either direction (or "not reached").
- Per query, the median and the share of up moves among its K matches.
- Control: for each query draw K random windows (same count, same time spread, same normalisation,
  past-only) and compute the same numbers. A shape only counts as "informative" if it beats this
  random control, out-of-sample.

## 5. Shape clusters
- Cluster in-sample windows (k-medoids on DTW distance, k chosen from {8, 16, 32} in advance).
- For each cluster report: distinct matches, share up/down at each horizon, mean forward move,
  random-control numbers, and the same figures out-of-sample using the in-sample cluster
  centres.
- Net forward move of cost before calling anything useful (3 lines: assumed cost, recorded spread,
  zero cost).
- Count trials: every (symbol, timeframe, N, method, k, horizon) combination tried is one trial.
  Print the trial count next to every result. A result counts as FOUND only if it holds
  out-of-sample after cost and a multiple-testing correction (Bonferroni on the trial count).

## 6. Visual output (owner request)
Charts are for people to check and for later vision-based review. They are not a replacement for
the numeric matching, because the computer matches on numbers; a picture alone does not make
matching easier, it makes the result inspectable.
- For each tested symbol/timeframe produce "shape cards" (PNG, using the existing viz module):
  the query window drawn as candles with the ATR-normalised line on a second panel; its top 5
  matches overlaid and drawn in separate small panels with dates in UTC and Riyadh time; and a
  forward-path panel showing what followed (median and spread band of the K matches vs the random
  control).
- One overview chart per symbol/timeframe: the times on the full history where the best clusters
  replicated (markers on a price chart), so repeating shapes are visible across years.
- Save at most 12 shape cards per symbol/timeframe (the best 6 and the worst 6 by out-of-sample
  edge) under docs/evidence/t146_charts/, plus an index in the plain-language report.
- Optional extra (not required): a vision-model cross-check that looks at the shape cards of the
  top 20 matches and says whether it agrees they look alike. Only if it can run inside the existing
  LLM caps; report the agreement rate as extra information.

## 7. Execution safety (as T-145)
- Report CPU and RAM first; plan disjoint parallel splits before launching; 13 GB total cap;
  per-worker RSS watchdog; checkpoints; no-progress stop; exit-code wrapper.
- Run 60m first as a smoke test, then 5m, 3m, 1m. Stop and report if a run exceeds its planned time
  by 2x rather than silently cutting the grid.
- Pure Python/NumPy is acceptable if pandas is missing; no new heavy dependencies without asking.

## 8. Deliverables and acceptance
- scripts/lab_t146_matching.py (or a module under src/trader/lab/) with CI-importable unit tests.
  Required tests: no look-ahead (a planted future spike never appears in a match), overlap removal,
  ATR normalisation invariance to scale, DTW of identical windows is 0, the random control keeps
  the same time spread, and a planted repeated shape is recovered while white noise gives nothing.
- docs/evidence/t146_matching_summary.json (all numbers, trial count, split dates, parameters),
  docs/evidence/t146_matching_report.md in plain language with the verdict per symbol/timeframe,
  and the charts above.
- ruff check and ruff format --line-length 100 clean (CI uses ruff 0.9.6; use X | Y in isinstance).
  mypy clean. PR, ONE log entry, validation entry, set to review, hand the turn to claude.

## 9. What this does not do
It does not trade, does not promote anything and does not prove a strategy. A shape that repeats
and is followed by a net-of-cost move out-of-sample becomes a CANDIDATE idea for the lab (a later
task), where it must still pass the normal gates.
