# T-183: Identify the owner's chart inputs (tfmult) for the Tai port (diagnostic only)

## Why
T-182 showed the 54-minute grouping phase is not the cause (best 11/31 within 3 bars). The Pine
source takes the HA timeframe from the input `tfmult` times the chart minutes
(`calculatedMinutes = round(resolutionMinutes * tfmult)`, source lines ~191-215). We assumed the
default 18 (3m x 18 = 54m). The owner's chart may use a different tfmult, and the first cross time
is very sensitive to it. This task identifies the input; it is not strategy tuning because the
target is the owner's own recorded trades, not profit.

## Task
1. Using the T-182 port at `ha_anchor=session_1800_ny`, both HA modes (closed, forming), run
   tfmult in the pre-declared integer set 6, 8, 9, 10, 12, 14, 15, 16, 18, 20, 24, 30, 36 only (13
   values, all other inputs at the stored defaults in the Pine source; no other parameter varied).
2. Replay NAS100 3m recorded data 2026-10-05 .. 2026-10-16, owner CSV as New York time (D-044).
   Per tfmult and mode report entries matched within 0/1/2/3 and 30 bars and extra entries.
3. Because 26 trials were run, report a permutation null (seed 183, 2,000 draws, same-hour shuffle
   of owner entries, as in T-178) for the best result so a lucky value is not mistaken for the
   input. State plainly whether the best tfmult stands out from the other 25 trials.
4. Gate unchanged: at least 25 of 31 within 3 bars. If one tfmult reaches it and stands out, name
   it. If none, state the best and stop; no further variants.

## Verdict first
"Best tfmult X, mode Y: n/31 within 3 bars, n/31 within 30; gate met yes/no; stands out from
other trials yes/no (p=...)."

## Rules
No broker calls, orders, sealed holdout reads, lake or production writes, secrets. Pod data only.
Ruff check and format, line length 100, no per-line noqa. Deliver `docs/evidence/t183_report.md`,
`docs/evidence/t183_sweep.csv`, `scripts/lab_t183_tfmult.py`, `tests/unit/test_t183_tfmult.py`.
One log entry, hand turn to claude, stop.
