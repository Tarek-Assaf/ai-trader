# T-219: Did Tai miss entries on 2026-10-20/21? (read-only parity on recorded live bars)

## Problem
Tai has evaluated every 3-minute bar since the lease fix (counters show only `no_signal` at
`ha_cross`), with zero entries across the whole 2026-10-21 US session. The owner trades the same
indicator by hand. The replay parity (T-204: 25 of 26 on 10 historical days, ~2-3 entries/day)
says a full session with no entry is unusual but possible. We must know if it is a quiet day or a
live-path defect.

## Required (read-only; no broker, order, config, risk, gate or lease change)
1. Using the recorded 1-minute lake on the Pod (newest data, 2026-10-20 and 2026-10-21 UTC),
   run BOTH the closed-reference Tai port and the production `TaiSignalProvider`/runner path
   over the same bars. Reuse an existing lab (`lab_t149_recorded_tai.py`, `lab_t187_runtime_replay.py`
   or the T-204 runner); state which and the exact command.
2. Report entries from each, per day, with timestamp/side/entry/stop, and the first failing gate
   for every bar where one fires and the other does not.
3. Verdict: (a) both empty -> quiet days, no defect; (b) port fires, runtime does not -> list the
   first divergent bars and the gate; (c) both fire but the live agent did not -> the live path
   differs from the replay (feed, bar builder, spread guard, stale checks): list bars and reasons.
4. Check the live bar builder against the lake: for 2026-10-21 14:00-20:00Z compare the 3-minute
   bars the agent built (or the ThreeMinuteBarBuilder run on lake 1-minute bars) with 3-minute bars
   aggregated from the lake; report any mismatches.
5. Write the report to `docs/evidence/t219/report.md` and a `summary.json`.

## Acceptance
- Read-only, secret-free; no trading, risk, gate, dry-run, order or broker change. Ruff (100),
  mypy, CI green for any code. One log entry, turn to claude, stop. If a defect is found, do not
  fix it in this task; propose the fix.
