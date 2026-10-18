# T-165: Align our bars to the owner's chart using within-trade price moves (diagnostic only)

## Why
T-164 matched owner entry prices to our bars after removing one constant NQ1-vs-NAS100 basis.
That basis is not constant (futures carry, roll), so the per-entry best shifts were scattered
(-330 to +161 minutes) and the test is weak. Inside one owner trade the basis cancels: the
owner CSV has an entry and an exit (time and price) for each of the 31 trades. The price move
exit minus entry, and the path in between, can be compared with our bars with no basis at all.

## Task (replay only, one bounded Pod run)
1. Parse the 31 trades (entry and exit rows, America/New_York, D-044). For each trade compute
   the owner move = exit price - entry price (signed by direction as in the file).
2. For shifts s from -360 to +360 minutes (1 minute steps), compute our NSXUSD 1m mid move for
   the same trade: close of the bar at exit_time+s minus open of the bar at entry_time+s.
   Score per shift: number of trades where our move has the same sign as the owner move and
   within 25 percent or 10 points of it (report both counts), and the median absolute error.
3. Fixed seed (164), 2,000 permutations: shuffle our moves across trades to get a p-value for
   the best shift. State whether the best shift is 0.
4. Also test the owner's reported favorable and adverse excursion: for each trade, between
   entry and exit, did our bars reach the owner's favorable excursion (in points) and not
   exceed the adverse excursion beyond a 25 percent tolerance at shift 0 and at the best shift.
5. Repeat step 2 with each owner time treated as bar open and as bar close (3m bar, shift by
   one 3m bar) and with trade durations from the "Duration (bars)" column as a cross-check of
   the 3m chart assumption (does exit_time - entry_time equal bars x 3 minutes? list misfits).
6. Plain verdict first: which shift (if any) aligns, with the match count out of 31. If one
   shift (or one shift in a documented rule such as DST) gives p < 0.01 and at least 20/31 trades
   with our move within tolerance, say so and Claude will queue the T-162 replay at that shift.
   If not, say plainly that the owner's chart bars cannot be matched with our data and what the
   owner must supply (chart-bar CSV or the Pine Inputs/Style screenshot).

## Rules
Offline replay on the Pod. No broker calls, no orders, sealed holdout never read, no lake or
production-state writes, no tuning or strategy search. NSXUSD 1m mandatory (3m/5m/60m only where
a step needs them), XAUUSD has no owner rows (state it). Reuse the T-162/T-164 helpers without
changing their contracts. Stop after the run.

## Deliverables
scripts/lab_t165_trade_pair_alignment.py, tests/unit/test_t165_trade_pair_alignment.py,
docs/evidence/t165_report.md (plain verdict first), t165_shifts.json, t165_trades.csv.
