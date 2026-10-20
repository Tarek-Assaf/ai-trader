# T-204: Replay the full runner path (with exit callbacks) against the closed port (closes T-199)

## Why
T-203 (reviewed) added the post-exit refresh trigger: `TaiDemoRunner` records `last_exit_at` and calls
`TaiSignalProvider.note_exit(at)` on every exit; the provider then fires a refresh entry once
`REFRESH_BARS` (12) bars have passed since the exit (same rule as `scripts/lab_t162_pine_port.py`
lines 446-451). The T-203 Pod replay still read 1/26 because `scripts/lab_t195_runtime_miss.py` drives only the
provider and never tells it when a trade exited, so 25 of 26 port entries (refresh entries) cannot appear.
That is a harness gap, not proof the fix works or fails.

## Do
1. Change the replay harness so that, bar by bar, it calls `note_exit(exit_bar_time)` on the provider at
   each exit the port produced (or, better, drives a `TaiDemoRunner` in dry-run with a fake broker and the
   same bars so the exit callbacks come from the runtime's own shadow-exit logic). Prefer the runner path; if
   that is too large, use the port's exit times and say so.
2. Run it on the Pod, read-only, NSXUSD, same ten dates before 2026-07-01 as T-195/T-203.
3. Report matched / extras / misses vs the 26 port entries, and for every miss the first differing quantity
   with numbers (as in T-202). If runner-driven exits differ from the port's exits, say how (that is a
   separate cause, e.g. exit timing).

## Rules
Dry-run stays on. No broker write calls, no holdout read, no risk/gate edits, no redeploy, no secrets.
Harness/test changes only unless the replay exposes a code bug; if so, describe it, do not fix it silently.

## Acceptance
>=22 of 26 matched, or each miss named with numbers. `docs/evidence/t204/report.md` + `summary.json`.
Ruff check/format (line 100), mypy, CI green. One log entry, turn to claude, stop.
