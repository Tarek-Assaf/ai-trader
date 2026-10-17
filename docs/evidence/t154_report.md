# T-154 diagnostic report

## What was wrong

T-153 reported zero library windows because a child process could time out
before writing its final group JSON, and the parent fallback replaced the
missing count with zero. That made a partial run look like an empty library.
The XAUUSD lake itself contains 6,140,217 rows. NSXUSD was present in the
current Pod image under its canonical `NSXUSD/1m` directory; older images used
`NAS100/1m_capital`, so the loader now checks that known alias only when the
canonical directory is empty.

The source also contains real gaps. Exact adjacent spacing is therefore kept
for 1m, 3m and 5m windows. Hourly windows may cross the confirmed HistData
daily 20:00/21:00-to-23:00 UTC break, while weekend and unknown gaps remain
hard boundaries.

## What changed

* `scripts/lab_t146_matching.py` records library and OOS window counts before
  expensive matching begins, and applies the narrow hourly daily-break rule.
* `scripts/lab_t146_runner.py` preserves those counts in partial checkpoints
  and summaries.
* `src/trader/lab/runner.py` maps the known legacy NAS100/US100 paths to the
  canonical NSXUSD loader when needed.
* Tests cover in-session spacing, weekend rejection, unknown session-gap
  rejection, hourly daily-break handling, partial-count preservation, and
  legacy symbol mapping.

## Bounded result

The 2025-2026 Pod copy yielded nonzero inventories for all 1m/3m/5m groups.
XAUUSD 60m yielded 724 length-30 and 190 length-60 windows. NSXUSD 60m
yielded zero windows even after the confirmed daily-break exception because
the source has additional same-day gaps such as 11:00-13:00 and 20:00-23:00
UTC. Per the acceptance rule, the study stops at that point and writes no
matching or net-of-cost verdict. The complete counts are in
`t154_lake_inventory.json` and the plain-language inventory note.

All checks were read-only: no broker calls, orders, holdout reads, gate or
ledger changes, cost edits, or promotion activity.
