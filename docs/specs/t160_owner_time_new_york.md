# T-160: Read the owner trade list as New York time and rerun the 31-trade replay

Status: spec for Codex. Diagnostic only: no broker calls, no orders, no gate/cost/ledger/
promotion writes, no lake writes, sealed holdout never read, no strategy tuning. Pod data only.
All four timeframes 1m/3m/5m/60m (plus 54m where T-157 used it) and both NSXUSD and XAUUSD
wherever lake data is used; 1m mandatory.

## Why
Owner 2026-10-18 (Q-070, option B): the TradingView export of his 31 trades is in New York time
(UTC-4 in October 2026), not UTC. T-159 found the same thing from the data: shifting the owner
times +240 minutes gives 87.8% agreement against 46.5% at zero shift (permutation p=0.0005).
Our recorder, Capital.com parser and bar building are UTC and correct. Only the reading of the
owner CSV is wrong. This replaces the earlier "owner export is UTC" note in
docs/specs/t151_owner_trades_comparison.md (mark that line superseded, do not delete it).

## Steps
1. Find every place that reads the owner CSV (at least scripts/lab_t151_owner_trades.py
   `read_owner_entries`, scripts/lab_t157_rule_by_rule.py, scripts/lab_t155_tai_parity.py,
   scripts/lab_t156_feature_dump.py, scripts/lab_t158_pipeline_audit.py,
   scripts/lab_t159_time_alignment.py). Prefer ONE shared helper that converts owner times from
   America/New_York to UTC with `zoneinfo` (not a fixed +4h, so a date after 1 Nov 2026 would
   use UTC-5). Production code under src/trader is NOT changed.
2. Unit tests with known timestamps: 2026-10-06 10:00 New York = 14:00 UTC; a winter date
   (e.g. 2026-12-01 10:00 New York = 15:00 UTC); the DST end day 2026-11-01 01:30 handled
   explicitly (state the rule used for the ambiguous hour).
3. Re-check alignment: rerun the T-159 grid on the converted times. The best shift must now be 0
   with agreement near the +240 result (about 88%). If not, STOP and report; do not add any
   other correction.
4. Rerun the T-157 rule-by-rule replay on the 31 owner entries with the converted times, same
   rules, same bases (mid/bid/ask), same tolerances (+-1/+-2/+-5 three-minute bars). Report how
   many entries match (it was 0/31) and, per entry, the first failing rule as T-157 did.
   Old evidence files from T-151/T-155/T-157 stay unchanged; write new files.
5. Plain-language owner verdict first: how many of 31 now match, which rule still fails most,
   and what the next step would be. No variant rounds and no tuning in this task.

## Stop rules
- Maximum one correction (the New York conversion). If step 3 fails, stop after step 3.
- No new indicator variants. If the match is still low, report the first failing rules and stop.

## Deliverables
`scripts/lab_t160_owner_time_ny.py` (and the shared helper if added), unit tests,
`docs/evidence/t160_report.md`, `docs/evidence/t160_owner_time_ny.json`,
`docs/evidence/t160_replay.csv`.
