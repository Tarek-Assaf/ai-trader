# T-170: Why the port misses the owner's entries: per-condition agreement at the owner's bars

## Why
T-169 (full warm-up, port unchanged): only 4 of 31 owner entries are reproduced within +-3 bars
(target 25). 21 owner entries have no same-direction port entry within +-10 bars, so this is not a
small timing shift. The median port-minus-owner price is -222 points, which is a futures (NQ1)
versus CFD (NSXUSD) price basis. The Pine script uses `ta.vwap(hlc3)`, which weights by volume;
NQ1 has real volume and our CFD bars have tick volume. Direction, VWAP and HA state fail most
often. This task separates three possible causes using numbers only: (a) the bars are not aligned in
time, (b) one condition disagrees systematically (likely VWAP volume weights or the HA anchor),
(c) a basis that moves during the day.

## Task (replay only, one bounded run, port unchanged, no tuning)
Import the T-168/T-169 scripts; do not edit the port. NSXUSD 3m, full warm-up, closed mode.
1. Time alignment, independent of strategy. For each of the 31 owner entries, take the port's
   3m bar at the owner time (New York time, D-044) and at offsets -10..+10 bars. Report the
   basis = owner entry price minus bar close for each entry (list, plus median, and the median per
   NY day). Subtract the overall median basis from the owner price and report for each offset how
   many of the 31 owner prices fall inside that bar's [low, high]. Verdict line: the offset with
   the highest count and that count (a clear peak at 0 means the clock is right; a peak elsewhere
   or no peak means the bars are shifted or the basis moves).
2. Per-condition agreement at the owner's bar (offset 0). For each owner entry report, as
   long/short/none, what the port's own condition evaluates to at that bar: direction, 54m HA
   colour and flip, VWAP side (close above/below VWAP), EMA filter, ADX/DI, risk and cooldown.
   Then give, per condition, how many of the 31 agree with the owner's direction (and the same at
   +-1 bar). Name the condition with the lowest agreement.
3. One registered diagnostic variant, not tuning: rerun the T-169 matching with VWAP computed as
   an equal-weight running mean of hlc3 (volume ignored), session reset exactly as in the port.
   Everything else unchanged. Report the same numbers as T-169 (exact, +-1, +-2, +-3 of 31) next to
   the T-169 baseline. Do not try other variants.
4. Verdict first: "clock aligned yes/no (peak offset X, count Y of 31); lowest-agreement condition
   Z (n of 31); equal-weight VWAP gives N of 31 within +-3 (baseline 4)". If any run reaches >= 25
   say so; otherwise name the most likely cause from the numbers only.

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning or
strategy search, no secrets. NSXUSD only in the owner window (say so; 1m coverage statements
cover NSXUSD and XAUUSD). Lint with ruff check and ruff format (line length 100). One log entry,
hand turn to claude, stop.

## Deliverables
docs/evidence/t170_report.md, docs/evidence/t170_conditions.json, scripts/lab_t170_conditions.py
