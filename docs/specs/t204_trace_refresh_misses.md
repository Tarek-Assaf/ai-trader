# T-204: Why the replay still shows 1/26 after the T-203 refresh trigger

## Why
T-203 (V-497) added the refresh trigger (`REFRESH_BARS = 12`, `last_exit_at` persisted, cross path wins). Code
accepted. The Pod replay is still 1/26 with `runtime_gate_ha_cross` as the first gate on 25 misses, identical to
before T-203. `scripts/lab_t195_runtime_miss.py` already feeds the port's exit times (`exit_times`, lines ~145-150)
and has its own refresh branch (~226), so the T-203 report's explanation (exit event never delivered) does not
match the script. Either the refresh branch is not reached, the exit anchor is wrong, or the replay still reports
the pre-refresh gate name for entries it does take.

## Do
1. For the 25 misses, print per row: port entry time, the port exit time before it, bars since exit (runtime
   count and port count), REFRESH_BARS, `smart_ok` BUY/SHORT with each term's value (close, EMA, ADX, +DI/-DI,
   VWAP), the resulting runtime direction, and the gate name reported. Run on the Pod, same ten dates, NSXUSD,
   before 2026-07-01, read-only.
2. Name the single first differing quantity with numbers (for example bars-since-exit off by k, or a smart_ok
   term, or gate reporting). If the cause is the gate label only, fix the label and re-count matches.
3. Make the smallest fix in the replay script or `tai_wiring.py` that the evidence supports; no new tunables, no
   risk/gate edits. Add a unit test for it.

## Rules
Dry-run stays on. No broker calls, holdout read, risk/gate edits, redeploy or secrets.

## Acceptance
Replay >=22/26, or one named first-differing quantity with per-row numbers different from "ha_cross". Ruff
check/format (line 100), mypy, CI green. `docs/evidence/t204/report.md` + `summary.json`. One log entry, turn
to claude, stop. If >=22/26, Claude closes T-199.
