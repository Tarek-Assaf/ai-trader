# T-161: Rerun the registered Tai switches and HA checks with the correct clock

Status: spec for Codex. Diagnostic only: no broker calls, no orders, no gate/cost/ledger/
promotion writes, no lake writes, sealed holdout never read, no new parameter tuning. Pod data
only. All four timeframes 1m/3m/5m/60m (plus 54m) and both NSXUSD and XAUUSD wherever lake data
is used; 1m mandatory.

## Why
T-160 fixed the owner clock (New York time). The alignment check now picks shift 0 (49 pairs,
87.8%). But the baseline replay still passes only 1/31 entries (2/31 within +-3 bars) and the
first failing rule is the 54m Heikin Ashi state for 19 of 31 entries. Important: every earlier
test of the nine registered T-157 switches (closed vs forming HA, UTC / Riyadh / CME anchors,
Wilder vs simple, EMA seed, +-1 bar shift) and the T-158 Stage 4 HA counts ran with the owner
times 4 hours wrong, so their "nothing helps" result is not valid. This task repeats those
exact tests once with the correct clock. It is a rerun, not a new variant round.

## Steps
1. Rerun the full set of switches already coded in scripts/lab_t157_rule_by_rule.py (no new
   switches, no new values) with the New York conversion in place, for mid, bid and ask bases.
   Report for each switch how many of the 31 entries pass all rules at the center bar and
   within +-1/+-2/+-5 three-minute bars, and the first failing rule counts.
2. Rerun T-158 Stage 4 item 2 (HA state category per entry: flip on current bar / same colour no
   flip / opposite colour) with the correct clock, and print the last 8 54m HA candles (HA open,
   close, colour) before each of the first 5 owner entries so the owner and Claude can read them
   by eye. Also print the owner's NQ1 entry price next to the recorded NAS100 mid price at the
   same minute for those 5 entries (level difference only for information).
3. Say plainly which switch (if any) gets 20 or more of 31 entries, and which first failing rule
   remains. Do not add or tune anything.

## Stop rules
- No new switches, windows or thresholds. If no registered switch reaches 20/31, stop and report
  the first failing rule per entry; Claude will then write the next step.
- One Pod run. Old T-157/T-158/T-160 evidence files stay unchanged; write new files.

## Deliverables
`scripts/lab_t161_variants_correct_clock.py` (may import and call the T-157/T-158 code),
unit tests for any new code, `docs/evidence/t161_report.md` (plain-language owner verdict first),
`docs/evidence/t161_variants.json`, `docs/evidence/t161_replay.csv`.
