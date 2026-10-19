# T-167: Dashboard count check (does the port produce the same number and mix of trades?)

## Why
T-164/T-165 show the owner's NQ1 chart bars cannot be lined up with our NAS100/NSXUSD bars entry
by entry, and T-166 shows the port follows the Pine source rule by rule. Entry-by-entry matching
is sensitive to bar timing and price basis. Trade counts and outcome mix are not. The owner sent
two Tai dashboards (Q-067, Q-068) that give totals we can compare with the port's totals on our
own recorded data, with no bar alignment needed.

## Reference numbers (owner screenshots, see Q-067 and Q-068)
- NQ1! 3m replay (Q-067): Trades 115, SL 38, TP2 12, TP3 32, TP1 hidden (so about 33), win rate 66.96%.
- US 100 Cash CFD 3m, 13-14 Oct (Q-068): Trades 53, SL 12, TP1 15, TP2 9, TP3 17, win rate 77.36%.
The exact start and end of each chart range is not known. Treat them as ranges, do not guess one.

## Task (replay only, one bounded run, no tuning)
1. Run the existing port (scripts/lab_t162_pine_port.py, unchanged) on NSXUSD 3m recorded data
   for 13-14 Oct 2026 in three window definitions: NY midnight to NY midnight, UTC midnight to
   UTC midnight, and the owner-trade window 2026-10-06..2026-10-16. Report for each: trades,
   SL, TP1, TP2, TP3 counts, win rate, total R. Use the port's own win definition and say which.
2. Compare with the reference numbers: counts within 25 percent and outcome mix (share SL, TP1,
   TP2, TP3) within 10 points count as "same order of magnitude". Report plainly which windows,
   if any, qualify.
3. Run the same on the NQ1 reference window only as far as our NSXUSD data allows and say so.
4. One plain verdict first: "port gives N trades vs owner's 53 (CFD) and 115 (NQ1); mix X vs Y;
   conclusion". If the port gives roughly the owner's counts, say that the historical rerun can
   go ahead on the port with a documented caveat (D-050). If not, name the most likely cause
   from the numbers only (for example far more or fewer trades = filter or refresh difference).

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning
or strategy search, no secrets. Do not change the port. If lake data is touched, 1m is mandatory
for both NSXUSD and XAUUSD where data exists (the owner window has NSXUSD only; say so).
One log entry, hand turn to claude, stop.

## Deliverables
docs/evidence/t167_report.md, docs/evidence/t167_counts.json, and a small script
scripts/lab_t167_counts.py that only calls the port.
