# T-205: Explain the one remaining replay miss (2026-06-30T14:27Z SELL)

## Why
T-204 replayed the production provider with exit callbacks: 25/26 port entries matched, 0 extras (V-498, V-499).
One miss remains: port SELL entry 29762.821 / stop 29770.751522703096; runtime offers a nearby SELL at entry
29763.442 / stop 29799.556551589813. The stop distance differs ~4.6x (7.93 vs 36.11 points), so the stop
logic or the exit-history state, not the entry gate, is the likely cause.

## Do
1. Read-only, NSXUSD, same dates. For the 14:27Z bar, print port and runtime values for every input to the
   stop (swing/ATR/HA extreme, lookback window, history length) and name the first differing quantity.
2. Check whether the runtime candidate is the same bar as the port entry or a later bar (the T-204 row says
   "nearby later"); state which.
3. Report in `docs/evidence/t205/report.md` + `summary.json`. Do not change `src/`; if a code bug is found,
   describe it with numbers and propose the fix for review.

## Rules
Dry-run stays on. No broker write calls, holdout reads, risk/gate edits, redeploy or secrets.

## Acceptance
First differing quantity named with numbers. Ruff check/format (line 100), mypy, CI green. One log entry,
turn to claude, stop.
