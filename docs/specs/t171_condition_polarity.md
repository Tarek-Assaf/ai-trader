# T-171: Is the port opposite to the owner, or just silent? Three-way split and cross timing

## Why
T-170 (accepted as a negative diagnostic): the clock is not clearly shifted (best offset +1, only
11 of 31 prices contained), equal-weight VWAP changes nothing (4 of 31, baseline 4), and the
cross event `direction` agrees with the owner at 0 of 31 owner bars. But `agree` in T-170 counts
only an exact same-direction match, so "0 of 31" mixes two different facts: the port shows the
opposite direction, or the port shows nothing at that bar (`none`). VWAP side (5 of 31) and HA
state (8 of 31) are also far below the 50 percent a coin flip gives, which would be the signature
of an inverted condition, not of noise. This task separates these cases with numbers only.

## Task (replay only, one bounded run, port unchanged, no tuning)
Import the T-168/T-169/T-170 scripts; do not edit the port. NSXUSD 3m, full warm-up, closed mode,
baseline VWAP (volume weighted), offsets -1, 0, +1.
1. Three-way split. For each condition in T-170 (direction, ha_state, vwap_side, ema_filter,
   adx_di) report counts of `same as owner`, `opposite to owner` and `none` over the 31 entries
   at each offset. Name every condition whose `opposite` count is >= 21 of 31 (that would be a
   polarity problem) and every condition whose `none` count is >= 21 (that would be a timing or
   missing-signal problem).
2. Cross timing. For each owner entry, at offset 0, report the number of 3m bars since the port's
   most recent same-direction cross (buy_cross for long, sell_cross for short) and since the most
   recent opposite-direction cross, looking back up to 60 bars (null if none). Give the median
   and the histogram of the same-direction value (0, 1-2, 3-5, 6-10, 11-20, 21-60, none). Verdict:
   "owner enters N of 31 within 2 bars of a same-direction port cross" (a high N means the
   direction logic is right and only the entry gating differs; a low N means the cross itself
   differs).
3. One registered mirror diagnostic (not tuning): re-score the existing T-169 baseline matching
   with every port trade's direction flipped (long becomes short, short becomes long) and entry
   bars unchanged. Report exact, +-1, +-2, +-3 of 31 next to the T-169 baseline (4 within +-3).
   This earlier mirror ran under the wrong clock (D-044), so it is the only variant allowed here.
4. Verdict first: "polarity problem yes/no (conditions with opposite >= 21: ...); silent-signal
   problem yes/no (conditions with none >= 21: ...); owner enters N of 31 within 2 bars of a
   same-direction cross; mirror gives M of 31 within +-3 (baseline 4)". If any run reaches >= 25
   say so; otherwise name the most likely cause from the numbers only.

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning or
strategy search, no secrets. NSXUSD only in the owner window (say so; 1m coverage statements cover
NSXUSD and XAUUSD). Lint with ruff check and ruff format (line length 100). One log entry, hand
turn to claude, stop.

## Deliverables
docs/evidence/t171_report.md, docs/evidence/t171_polarity.json, scripts/lab_t171_polarity.py
