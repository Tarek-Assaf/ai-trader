# T-196: Why the runtime HA cross gate rejects 23 port entries (read-only trace)

## Why
T-195 (V-468) showed 25 of 26 port entries are missed by the wired Tai runtime; 23 fail first at `ha_cross`.
Bar construction, history window, warm-up and position lock are ruled out. The values behind the gate were not saved.

## Build (extend `scripts/lab_t195_runtime_miss.py` or add `scripts/lab_t196_ha_trace.py`; offline, NSXUSD, before 2026-07-01)
For each of the 23 `ha_cross` misses write to `docs/evidence/t196/summary.json`, at the entry bar and the two bars before it,
side by side for port and runtime: HA 54m open/close/colour, the previous HA colour, the cross flag, EMA200, ADX, +DI/-DI, VWAP.
Then name the FIRST quantity that differs for each row (for example HA open seed, 54m bucket boundary, previous-colour memory,
cross defined on a different bar). Read the runtime HA code and the Pine port code and quote the two lines that differ.
Report `report.md`: counts per first-difference, one plain cause sentence (or the exact unknown), and one minimal code fix, described only.

## Rules
No broker or order call, no network, no sealed holdout read, no risk or gate edits, no redeploy, no secrets. Do not fix the runtime.

## Acceptance
Every one of the 23 rows has a first differing value, or the report states exactly what is unexplained. Ruff check/format (line 100), mypy, pytest, CI green.
