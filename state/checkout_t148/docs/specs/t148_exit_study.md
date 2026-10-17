# T-148: Exit study (owner's scale-out idea) on our own best candidates, plus mirror fix

Status: spec for Codex. Diagnostic only: no broker calls, no Gate 1 evaluation, no writes to
the trial counter, ledger or promotion files, sealed holdout never read (not even via the
sealed reader), no edits to risk, gate or cost files. Owner decision Q-056 option A (D-039).

## Why
T-147 showed the owner's indicator signals do not earn money on our data, but the owner's exit
idea (sell a third at 1R, 2R and 3R, move the stop of the rest to entry after the first target)
was only tested on those signals. The lab itself only ever used a single stop and a single
target. This task asks: does the exit structure change the outcome of our own best candidates,
and when does it help or hurt? The answer should explain why, not just give a total.

## Part 1: fix the T-147 mirror control
T-147's mirror flips the entry direction but keeps the trend filters, so it takes about 9
trades a year and says nothing. Replace it with a full inversion: flip the signal direction
AND the filter sides (EMA200 side, ADX +DI/-DI side, VWAP side), and place stop and targets on
the inverted side. Rerun the mirror for all 8 groups and 5 periods of T-147 only (do not rerun
the rest) and report trades, win rate, net points zero and normal cost, real accounting.
Update docs/evidence/t147_tai_filter_summary.* only by adding a separate mirror_full section;
do not change existing T-147 numbers.

## Part 2: exit schemes on fixed entries
Entries and the initial stop are NOT changed. For each candidate keep its own entry bar,
direction and stop distance; define R = the candidate's own initial stop distance.
Candidates (pre-registered, do not add after seeing results):
- The 24 candidates of T-145 (docs/evidence/t145_by_year_summary.json: top 3 distinct per
  group, 8 groups, XAUUSD 1m by the T-145 rule). Use the same strategy hashes.
- The Tai baseline signals from T-147 (exact rules) as a 25th row per group for reference.
Exit schemes (7, pre-registered, each counted as a trial):
- E0 the candidate's original exit (as in T-145).
- E1/E2/E3 single stop at 1R and one target at 1R / 2R / 3R, all out, time stop as in E0.
- E4 owner scale-out: sell 1/3 at 1R, 1/3 at 2R, 1/3 at 3R; after 1R is hit the stop of the
  rest moves to entry. Real results per trade: stop before 1R = -1R; 1R then entry stop =
  +0.33R; 2R then entry stop = +1.0R; 3R = +2.0R.
- E5 same thirds but the stop moves to entry only after 2R is hit.
- E6 halves: sell 1/2 at 1R and the rest at 3R; stop to entry after 1R.
Time stop and maximum holding bars as in the candidate's original exit for every scheme.
If stop and a target are hit in the same bar assume the stop first, and say so. Costs per
round trip are deducted in points (NSXUSD 4, XAUUSD 1.6), once per trade, in both cost modes
(zero and normal).

## Reports
- Per candidate, scheme, year (2022 separate, 2023, 2024 not fresh, 2025, 2026 to the day
  before the holdout) and continuous 2023-to-today: trades, win rate, net points zero and
  normal cost, total R, profit factor, max drawdown.
- Paired comparison per candidate: E4 minus E0, E4 minus E2, E5 minus E4, E6 minus E4, so we see
  what the scale-out adds with the same entries. Summary per group: how many candidates improve
  at zero cost and at normal cost, and the median change.
- Which kind of market condition each scheme helps: split the trades by session (Asia/London/
  New York), by trend or range (ATR trend measure), and by volatility tercile; report net
  points and trade count per bucket for E0 versus E4. Report only buckets with >= 30 trades.
- Trial total: candidates x schemes, stated next to every result. No result is a Gate 1 pass.
- A short plain-language section: where scale-out helped, where it hurt, and the reason.

## Scope and safety
All 8 groups: NSXUSD and XAUUSD on 1m, 3m, 5m and 60m. 1m is mandatory; never 60m only.
Report CPU/RAM first, plan the parallel split with disjoint ranges before launching, per-worker
RSS watchdog, total cap 13 GB (Pod has 16 GB, 8 CPUs). Check the progress of already running
processes before assigning any range to a helper.

## Acceptance
- Evidence: docs/evidence/t148_exit_study_summary.json and docs/evidence/t148_exit_study_table.md.
- Mirror fix evidence added under mirror_full in the T-147 summary (existing numbers unchanged).
- Tests; ruff check and ruff format at line length 100 run BEFORE handing over; encoding UTF-8
  without damage (do not rewrite old handoff entries); one log entry; hand the turn to claude.
