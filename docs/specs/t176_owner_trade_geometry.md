# T-176: Does the owner's trade log have the geometry of the stored Pine script?

## Why
T-162 to T-175 (14 diagnostics, all negative, best 9/31) tried to match the owner's 31 entries
bar by bar using our NSXUSD CFD bars. Every cross, anchor, multiplier, Renko and refresh variant
fails, and the price basis between NQ1 futures and our CFD (about -222 points, D-053) makes
bar-level matching fragile. This task uses only the owner's own CSV, so the data basis cancels:
it asks whether the owner's trades have the shape the stored Pine script produces (entry = close
of the signal bar, swing stop, 1R/2R/3R targets, tranche split, break-even after TP1, bars held).
If yes, the script is right and the mismatch is data (futures vs CFD). If no, the owner ran a
different script version or settings, and the port cannot match until those are found.

## Task (CSV and source only, one bounded run, no tuning, no strategy search)
Inputs: `docs/evidence/owner/owner_trades_tv_replay_nq1_3m_20261006_20261016.csv` (New York time,
D-044) and `docs/evidence/owner/tai_smart_filter_pine_source.txt`. Read the source for the
tranche quantities/percentages, target multiples, break-even rule, `riskLimit`, `maxRisk`
(risk/ATR cap), swing lookback and the strategy order sizing. Do not edit the port.

1. Group the 54 trade rows into positions: rows with the same entry time, price and direction are
   tranches of one position. Report the 31 entries as N positions, tranche count and quantity per
   position, and compare with what the source's order sizing would produce.
2. For each position compute risk R = |entry - stop price| from the Bracket Stop Loss rows (or the
   break-even/other exits) and report the distance of every Bracket Take Profit / market exit
   from entry in units of R. Report how many exits sit at 1.0R, 2.0R, 3.0R (tolerance 0.05R) and
   how many are something else (market-order exits: list their signal text, time, R multiple).
3. Check break-even: after a TP1-type exit, does the remaining tranche stop at entry (exit price
   = entry within one tick)? Report the count.
4. Check the swing stop: the stop distance should be `lowest(low[1],5) - 0.2*ATR(14)` (long) or
   the mirror. Using NSXUSD 3m bars only for ATR (basis cancels in a range), report the ratio
   R/ATR at the owner entry bar and how many positions satisfy 0 < R/ATR <= 2.0 (the cap).
5. Check durations: the `Duration (bars)` column against the exit-minus-entry time in 3m bars.
6. Report any row type the source cannot produce (for example "Sell market order" exits that are
   not stop/target, or position sizes that do not match the tranche logic) with the source line
   that would be needed.

## Verdict first
"Owner trade geometry matches the stored Pine script yes/no/partly; positions N; tranche split
matches: ...; exits at 1R/2R/3R: a/b/c of M; break-even matches: x of y; R/ATR within cap: p of N;
rows the script cannot produce: list." Say plainly which of these supports 'script is right, data
differs' and which supports 'different version or settings'. Do not run entry matching again.

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning,
no secrets. Lint with ruff check and ruff format (line length 100). Deliverables:
docs/evidence/t176_report.md, docs/evidence/t176_geometry.json, scripts/lab_t176_geometry.py,
tests/unit/test_t176_geometry.py. One log entry, hand turn to claude, stop.
