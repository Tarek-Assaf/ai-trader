# T-114: Strategy Lab speed and visibility

Author: Claude. For: Codex. Read `src/trader/lab/runner.py`, `pipeline.py`, `engine.py`, `miner.py`, `scripts/lab_run.py`, `scripts/lab_data_check.py`.

## Problem (measured on the Pod)
The lake holds about 16 years of M1 bars per instrument (NSXUSD from 2010-11, XAUUSD from 2009-03, about 5.5 million bars each). `scripts/lab_run.py --max-ideas 20 --timeframes 5` ran for over 30 minutes at 100% of one core with no output and no worker processes, so the time is spent before the process pool starts. `lab_data_check.py` also needed minutes just to print coverage. The Pod has 8 cores and 754 GB of RAM, but the Pod container is limited to 16 GB (cgroup `memory.max`), and the live services need part of it. A second lab process was OOM-killed while the first held about 8.7 GB, with six workers at about 700 MB each. Memory is therefore a hard constraint as well as speed.

## Memory rules (hard)
- Total lab footprint (parent plus all workers) stays under 8 GB on the full 16-year data. Load columnar (Arrow/NumPy arrays), never per-bar Python objects; share one read-only copy across workers (memory-mapped or shared memory) instead of one copy per worker.
- Read the container limit from `/sys/fs/cgroup/memory.max` and cap the worker count so workers times per-worker RSS fits within 50% of it.
- Refuse to start a second lab process while one is running (lock file), and log peak RSS in `progress.json`.
- Test: a fixture asserts the loader returns arrays, not objects, and the worker-cap function respects a small fake limit.

## Goals and acceptance targets (8-core Pod, same data)
1. `lab_data_check.py` coverage in under 30 seconds.
2. Loading one (instrument, timeframe) series in under 60 seconds the first time and under 10 seconds from cache.
3. `lab_run.py --max-ideas 20 --timeframes 5` (both instruments) in under 15 minutes.
4. A full run (timeframes 1, 3, 5; 100 ideas per source) in under 8 hours. If that cannot be met, report measured timings per phase in the PR so the owner can choose the window.

## Work
1. **Visibility first.** `lab_run.py` prints timestamped phase lines to stdout (flushed) and writes them to `progress.json`: loading, mining, evaluating n/total, holdout, done. Log per-phase wall time. Do this before any optimisation so the next Pod run shows where time goes.
2. **Fast loading.** Read Parquet columns with pyarrow (columns to Python lists or NumPy), never `to_pylist()` per row. Build `Candle` objects from columns. `coverage_report` must use Parquet footer metadata (row counts, min and max timestamp) and the file list, not a full load.
3. **Cache.** Cache the resampled, de-duplicated, holdout-excluded series per (instrument, timeframe) under `/workspace/state/lab/cache/`, keyed by the lake file list with sizes and mtimes and the holdout month set. A changed lake or holdout invalidates it. The cache is derived data only; never cache anything from the holdout.
4. **Training window.** Add `data.max_training_years` to `config/strategy_lab.yaml` (default 6, counted back from the start of the sealed holdout) and a `--years N|all` flag. Walk-forward windows are sized from the used window. Gate values do not change.
5. **Indicator reuse.** Compute each (indicator, period) series once per instrument/timeframe series and share it across all variants, neighbour checks, OOS and stress runs of that series (cache in the worker initializer). Results must be identical to today.
6. **Pattern mining.** Time `PatternMiner.scan`; bound its grid if it dominates; run it per group inside the pool rather than in the parent.
7. **Group-level parallelism.** Loading and mining for each (instrument, timeframe) group may run in worker processes. Keep `workers = CPU count - 2`, `nice 10`, per-idea error isolation and the one-run-at-a-time rule.
8. **Cheap pre-screen (optional, only if still needed to meet target 3 or 4).** Evaluate each idea first on a short in-sample slice; drop ideas that clearly lose (for example profit factor below 0.9 or fewer than 30 trades). Every dropped idea still counts in the multiple-testing trial count, is written to the ledger as a fail with reason `prescreen`, and the sealed holdout is never touched. Gate 1 thresholds are unchanged.

## Safety (unchanged)
No change to gates, the sealed-holdout rules (the validator reads it once per strategy), the trial counting across the whole run and ledger, or the "no orders" rule. The lab never imports broker or order code.

## Tests
- Deterministic equivalence: for a fixed fixture series and spec set, the optimised path returns the same decisions and metrics as the previous path (keep a small reference implementation in the test).
- Indicator cache returns the same values as direct computation.
- Loader from columns equals the old row loader on a fixture; coverage from Parquet metadata equals coverage from a full load.
- Cache invalidates when a lake file changes or the holdout months change.
- Pre-screen (if implemented) still counts dropped ideas in the trial count.
- Progress file contains the phase names and counts.

## Pod validation (owner)
Paste the phase timings from `lab_run.py --max-ideas 20 --timeframes 5`, then run the full job.
