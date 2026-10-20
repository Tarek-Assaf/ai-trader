# T-203: Post-exit refresh trigger in the runtime (closes T-199)

## Why
T-202 (V-495): all ten inspected port entries that the runtime misses are `refresh` entries. In
`scripts/lab_t162_pine_port.py:446-451` the port, once `i - last_exit >= REFRESH_BARS` and `_smart_ok`
passes, adds a direction even when neither raw HA cross flag is true. `src/trader/experiments/tai_wiring.py`
(~439-453) only takes a direction from `features["buy"][index]` / `["sell"][index]`. HA 54m values are
identical to displayed precision. The EMA200 gap (0.16-0.40) comes from a later start, not logic: port seeds
with the SMA of its first 200 closes at 2026-06-17T09:57Z.

## Do
1. In `tai_wiring.py`, add explicit post-exit state (`last_exit_index`, set when the demo/paper position closes)
   and the refresh trigger: after `REFRESH_BARS` bars since exit, evaluate long then short through the same
   `_smart_ok`-equivalent and risk checks the cross path uses. Same constants as the port, no new tunables.
   The state must survive restart (reuse the T-191 position-state persistence).
2. Keep the cross path unchanged. One entry at a time; refresh never fires while a position is open.
3. EMA200: seed from the SMA of the first 200 closes of the full retained history (as the port does). Do not
   change retention rules. Report if the live feed cannot start at the port's start; then say what bar count
   gives <0.05 point difference.
4. Unit tests: refresh fires exactly at `REFRESH_BARS`, not before; not while open; survives restart; cross path
   still wins when both apply.
5. Re-run the T-199 Pod replay (same ten dates, NSXUSD, before 2026-07-01) and report matched/extras/misses.

## Rules
Dry-run stays on. No broker write calls, no holdout read, no risk/gate edits, no redeploy, no secrets.

## Acceptance
Replay matches >=22 of 26 port entries (or the report names the next first-differing quantity with numbers);
`docs/evidence/t203/report.md` + `summary.json`; ruff check/format (line 100), mypy, CI green. One log entry,
turn to claude, stop. If >=22/26, Claude re-judges T-199 as done.
