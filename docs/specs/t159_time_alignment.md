# T-159: Find and fix the time alignment fault (follow-up to T-158 Stage 1)

Status: spec for Codex. No broker calls, no orders, no gate/cost/ledger/promotion writes, no lake
writes, sealed holdout never read, no parameter tuning of strategy rules. All four timeframes
1m/3m/5m/60m and both NSXUSD and XAUUSD wherever lake data is used; 1m mandatory.

## Why
T-158 Stage 1: at zero shift the owner NQ1 moves and our recorded NAS100 moves agree on only
0.3704 of 27 comparable points (correlation -0.2352). At +240 minutes agreement is 0.8621 (29
points, correlation 0.7448). Stages 2 to 5 (bars, indicators, replay rules, engine) pass. So the
first fault is a clock offset somewhere between the owner CSV, the recorded data and our code.
The sample is small, so the offset must be confirmed before anything is changed.

## Steps
1. Confirm the offset with more evidence than 27 points. Use at least three independent checks,
   for example: (a) all owner entries AND exits with a finer shift grid (5-minute steps, -6h to
   +6h) and a bootstrap or permutation test to show the best shift is not luck; (b) the recorded
   data's own session gaps (daily break, Sunday open) against the known CME/Capital.com
   schedule, converted with and without DST for Oct 2026 (US DST ends 1 Nov 2026); (c) the lake
   bar-time convention (open vs close) and the Pod recorder's timestamp source (UTC or local).
2. Locate WHERE the offset enters: owner CSV time zone, recorder timestamps, lake loader, or
   replay code. Name the file and line. State whether +240 minutes is a clean time-zone/DST
   explanation (give the arithmetic) or something else.
3. Apply the smallest correction at the layer that is wrong, with a unit test using a known
   timestamp. Do not rewrite lake data; do not change strategy rules or thresholds.
4. Rerun the T-157 rule-by-rule replay on the 31 owner entries with the corrected alignment and
   report how many entries now match (0/31 before). Report numbers only; if still low, list the
   first failing rule per entry as T-157 did.

## Stop rules
- If step 1 does not give a clear, repeatable best shift (agreement at best shift not clearly
  above all others, or the permutation test is not significant), stop after step 1 and report.
- Maximum one correction. No variant rounds.

## Deliverables
`scripts/lab_t159_time_alignment.py`, unit tests, `docs/evidence/t159_report.md` (plain-language
owner verdict first) and `docs/evidence/t159_time_alignment.json`.
