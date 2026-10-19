# T-195: Why the wired Tai runtime misses 22 of 25 port entries (read-only diagnostic)

## Why
T-187 (V-464) replayed the wired runtime (`ThreeMinuteBarBuilder` -> `TaiSignalProvider`) on the same
recorded NSXUSD 3m bars as the closed T-162 port at session_1800_ny. Result: 2 of 25 matched, 0 extras,
23 misses (22 `missing_runtime_direction`, 1 `bar_or_stop_difference` at 2026-06-22T23:21Z).
The owner requires the agent to behave exactly like the Tai indicator, so the runtime must reproduce
the port. "missing_runtime_direction" is a label, not a cause. This task finds the cause. Do not fix it yet.
Delegated decision (owner-authorized-by-delegation, standing): narrowest next diagnostic.

## Method (read-only, offline, Pod or local; no broker, no network, no holdout, no lake writes)
Reuse `scripts/lab_t187_runtime_replay.py` (do not rebuild). Add a new script `scripts/lab_t195_runtime_miss.py`.
For each of the 25 port entries, record at the entry bar, side by side, for port and runtime:
HA 54m open/close/colour, EMA200, ADX, +DI/-DI, VWAP, the session/anchor value, each gate boolean, and
the first gate that fails in the runtime. Then run these ordered hypotheses, one switch at a time on the same
bars, and report matches/extras/misses for each (no tuning, no new variants):
1. History window: `TaiSignalProvider` keeps a deque of max(2*minimum_history, 600) = 600 bars, so HA/EMA/ADX
   recursion starts from a different seed than the port, which runs on the full series. Replay the runtime
   with an unbounded history (and with the port's start bar) and compare.
2. Warm-up gate: `minimum_history=300` suppresses signals until 300 bars exist; check whether the first
   misses fall inside the gate.
3. Bar construction: compare `ThreeMinuteBarBuilder` 3m bars with the port's resampled 3m bars on the same
   minutes (count, first differing bar, OHLC).
4. Pine differences listed in docs/specs/t162_pine_port.md (refresh entries ignoring HA, management from the
   entry bar, target-first, lastExitBar/barsSinceExit on chart bars): confirm the runtime path applies them.
5. Position lock: confirm the runtime is not dropping signals because a prior (simulated) position is open.

## Output
`docs/evidence/t195/summary.json` and `report.md`: per-entry table (25 rows), the first failing gate per
miss, the count of misses explained by each hypothesis, and one plain sentence naming the cause (or the
narrowest remaining unknown). Also one proposed minimal code fix, described only.

## Rules
Sealed holdout never read (stay before 2026-07-01T00:00:00Z); no broker or order call; no network; no risk or
gate edits; no redeploy; no secrets. NSXUSD only (XAUUSD has no rows in this window).

## Acceptance
- Every one of the 22 `missing_runtime_direction` misses and the 1 `bar_or_stop_difference` has a named
  cause, or the report says exactly which part is unexplained.
- Unit test for any new helper. Ruff check and format (line 100), mypy, pytest, CI green.
- One log entry, hand the turn to claude, stop.
