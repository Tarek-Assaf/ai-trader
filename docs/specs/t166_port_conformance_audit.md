# T-166: Pine-port conformance audit (data-independent proof the port is the indicator)

## Why
T-164 and T-165 show the owner's NQ1 chart bars cannot be matched with our NAS100/NSXUSD data
(best +3 min, 17/31 within tolerance; per-entry shifts scattered). So "reproduce 25 of 31
entries" cannot be proven on our data. What can be proven without the owner's bars is that the
port (T-162) follows the Pine source rule by rule. This task proves that, so the historical
rerun can be trusted the moment the owner supplies matching bars.

## Task (offline, no Pod data scan needed; Pod or desktop, short)
1. Write docs/evidence/t166_conformance.md: a table with one row per rule in
   docs/evidence/owner/tai_smart_filter_pine_source.txt (entry signals, refresh entries without
   Heikin Ashi state, filters, sizing, TP1/TP2/stop/trail, target-first order, management from the
   entry bar, lastExitBar/barsSinceExit in chart bars, request.security HA values, EMA200, DMI
   Wilder, date filter, inputs). Columns: Pine source lines, port function/line in
   scripts/lab_t162_pine_port.py, known-answer test name, status (covered / missing / differs).
2. For each row marked missing or differs, add the known-answer unit test (hand-made bar series
   with the expected Pine result worked out by hand and written in the test comment) and fix the
   port only if the test proves a difference. Do not change any rule that already matches.
3. List every input/default the Pine source has that the port hard-codes, and every input whose
   owner value is unknown (the Inputs/Style screenshot is still outstanding). Mark each unknown
   input as an owner dependency; do not guess values.
4. Verdict first, plain language: "port matches the source on N of M rules; K differences fixed;
   U unknown inputs need the owner."

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning or
strategy search, no secrets. Keep T-162 public contracts. If lake data is touched at all, all four
timeframes 1m/3m/5m/60m and both NSXUSD and XAUUSD, 1m mandatory. One log entry, hand turn to
claude, stop.

## Deliverables
docs/evidence/t166_conformance.md, updated tests/unit/test_t162_pine_port.py (or a new
tests/unit/test_t166_conformance.py), and port fixes only where a test proves a difference.
