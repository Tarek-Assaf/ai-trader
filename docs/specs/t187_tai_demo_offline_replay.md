# T-187: Offline replay of the wired Tai demo runtime (dry-run proof before any enabling)

## Why
T-186 wired the Tai demo runner to the 3m feed, the port signal and Capital.com rules. Before the
owner redeploys and enables anything, prove offline that the wired runtime emits the same entries as
the closed T-162 port on real recorded data, so the first demo run does not surprise the owner.
Delegated decision (owner away, window to 2026-10-19 06:10 UTC): queue this as the narrowest safe next step.

## Build (read-only, no broker, no redeploy)
1. A script `scripts/lab_t187_runtime_replay.py` that reads recorded 1m NSXUSD mid bars from the lake
   for the most recent 10 trading days available BEFORE the sealed holdout start, feeds them minute by
   minute through `ThreeMinuteBarBuilder` -> `TaiSignalProvider` (T-186 code, not scripts/), and records
   every signal (time, direction, close, stop).
2. Run the closed T-162 port (`scripts/lab_t162_pine_port.py`) on the same 3m bars and list its entries.
3. Compare: same bar, direction and stop for every entry. Report matches, extras and misses, with the
   first differing bar and reason for each difference.
4. Run the runner in dry-run against a mock gateway on the same stream and count journal lines
   `dry_run_would_submit`; confirm zero order calls.

## Rules
Sealed holdout never read; no broker call; no network; no risk or gate edits. Output a summary JSON and a
short markdown report under docs/evidence/t187/.

## Acceptance
- 100 percent of port entries reproduced by the runtime path (same bar, direction, stop within one tick);
  any difference is explained, or the task is failed with the cause.
- Unit test for the script's comparison helper. CI green (ruff 0.9.6 line 100, mypy, pytest).
- One log entry, hand to claude.
