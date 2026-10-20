# T-202: First differing EMA200 and HA-cross quantity, runtime vs closed port (read-only)

## Why
T-199 Pod replay (V-494): runtime matches 1 of 26 port entries; 25 fail first at `runtime_gate_ha_cross`.
EMA200 at three entry bars differs by 0.16 to 0.40 points (port higher). Bounded and unbounded runtime EMA also differ
from each other, so the EMA seed or history start is not the same as the port's. T-196 named the HA gate but the fix in T-199
did not close it. We need the first differing number, not another hypothesis.

## Do (Pod terminal, offline, NSXUSD, before 2026-07-01; extend `scripts/lab_t195_runtime_miss.py` or add `scripts/lab_t202_trace.py`)
1. EMA200: find where the port's EMA200 starts (first bar, seed value, SMA seed or first-close seed, bar series it runs on)
   and where the runtime's starts. Recompute the runtime EMA with the port's seed and start and report whether the three
   sample deltas go to ~0. If not, name the next difference (close definition, bar set, session filter).
2. HA cross: for 10 of the 25 misses, at the entry bar and the bar before, print side by side port vs runtime:
   HA 54m open, close, previous open, previous close, and the buy/sell flag. Name the first quantity that differs
   (54m bucket origin, group completeness rule, previous-value carry, seed open) and quote the two differing code lines.
3. Describe ONE minimal fix in the report. Do not edit `tai_wiring.py` in this task.

## Rules
No broker or order call, no network, no sealed holdout read, no risk or gate edits, no redeploy, no secrets, dry-run stays on.

## Acceptance
`docs/evidence/t202/report.md` and `summary.json` contain: EMA seed/start for both sides and the recomputed deltas; the 10 HA
rows side by side; the first differing quantity named with the two code lines; the fix described. Ruff check/format (line 100),
mypy, CI green. Then T-199 can be re-judged against >=22/26 after the fix task.
