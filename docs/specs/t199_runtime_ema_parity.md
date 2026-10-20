# T-199: Make the runtime EMA200 (and the entries) match the closed T-162 port

## Why
T-196 (V-479/V-480): for 22 of the 23 `ha_cross` misses the first differing quantity is `ema200` (the other is
`ha_open_54m`). The runtime and the port compute the same indicator from different seeds or histories, so the runtime
rejects entries the port takes. T-195 showed 25 of 26 port entries are missed. The owner needs the agent to behave like
the indicator. This is the next narrowest fix. Dry-run stays ON.

## Build (offline first, then one Pod run)
1. Read `TaiSignalProvider._features` (src/trader/experiments/tai_wiring.py) and `_ema` in
   scripts/lab_t162_pine_port.py. Quote the lines that seed and update EMA200 in each.
2. Find why the values differ (likely: the runtime keeps a bounded deque of bars so the EMA seed starts later than the
   port's full series; or SMA vs first-value seed). Show the numeric difference at three entry bars.
3. Fix the runtime so EMA200 equals the port's value at every bar for the same input (a longer or persisted warm-up
   history, or the same seed rule). Do not change the strategy rules, risk or gate files.
4. Handle the single `ha_open_54m` difference the same way if it has the same root cause; otherwise report it.
5. Re-run the T-187/T-195 replay of the wired runtime against the port on the same ten dates (bounded loader,
   Pod, one run). Report matched/extras/misses before and after.

## Rules
No broker or order call, no network, no holdout read (before 2026-07-01), no risk/gate edits, no redeploy, no secrets.
Dry-run default stays true. Add unit tests: runtime EMA200 equals port EMA200 on a fixed fixture, including a restart
mid-series case.

## Acceptance
- Root cause quoted with the two differing lines and numeric evidence.
- Replay after the fix reproduces at least 22 of the 26 port entries, or the report names exactly what still differs.
- Ruff check/format (line 100), mypy, full CI green. One log entry, hand the turn to claude, stop.
