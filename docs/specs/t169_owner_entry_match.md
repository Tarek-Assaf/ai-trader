# T-169: Match the port's 33 trades against the owner's 31 entries (full warm-up)

## Why
T-168 shows the port gives 33 closed trades in the owner window 2026-10-06..2026-10-16, and the
owner's own export has 31 entries in that window (about 3 per day on both sides). So the total
count already agrees. The dashboards (53 CFD, 115 NQ1) have unknown date ranges; 115 trades at
about 3 per day is about 38 days, so they most likely cover a longer range than two days. The
two-day count (10/11) is not comparable to them. The open question is the goal itself: does the
port enter at the same moments as the owner? T-165 matched with a cold start; this repeats the
match with the T-168 warm run.

## Task (replay only, one bounded run, no tuning, port unchanged)
1. Reuse scripts/lab_t168_counts.py steps (do not edit the port or the T-168 script; import it).
   Use the closed-mode trades from the full warm-up run (owner times are New York time, D-044).
2. Match each of the 31 owner entries to the nearest port entry with the same direction. Report
   hits at exactly the same 3m bar, within +-1, +-2, +-3 bars, and the count of owner entries
   with no port entry of that direction within +-10 bars. Also report the port trades with no
   owner entry within +-3 bars. Do this for NSXUSD closed mode only.
3. Report a signed time-offset histogram (port minus owner, in 3m bars, -10..+10) and the median
   entry price difference (port minus owner) for matched pairs, to separate a timing offset from
   a price-basis offset (NQ1 futures vs CFD).
4. For each unmatched owner entry (up to the first 10) list which port entry condition was false
   on the owner's bar (direction, EMA, ADX/DI, VWAP, HA state, cooldown, risk). Do not change
   the port to fix them.
5. Verdict first: "N of 31 owner entries reproduced within +-3 bars (target 25)". If >=25, say
   the historical rerun can go ahead on the port. If not, name the most likely cause from the
   numbers only (constant offset, price basis, a condition that blocks most misses).

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning or
strategy search, no secrets. 1m is mandatory for any lake-wide coverage statement (NSXUSD and
XAUUSD; the owner window has NSXUSD only, say so). Lint with ruff check and ruff format (line
length 100). One log entry, hand turn to claude, stop.

## Deliverables
docs/evidence/t169_report.md, docs/evidence/t169_match.json, scripts/lab_t169_match.py
