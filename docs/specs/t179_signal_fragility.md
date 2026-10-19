# T-179: How fragile are the port's signals to small bar-data differences?

## Why
T-162 to T-178 (all negative or loose): the port's signals sit near the owner's entries more than
chance at 10 and 30 bars (T-177), with no stable delay (T-178: 4 of 15 after the signal, median
-7 bars, p 0.81), and the 16 unmatched entries fail on `cross` (the 54m Heikin Ashi close crossing
open). The owner's chart is NQ1! futures; our bars are NSXUSD CFD. A HA close/open cross is
sensitive when the two are nearly equal, so a different data source could move or delete cross
events without any logic difference. If the port's own signals are fragile to tiny bar noise, then
bar-exact reproduction from our data is impossible by construction, and "no stable delay" is
expected, not evidence of a different script. If they are stable, a different script or settings
is the more likely reason. This is the last narrow test of that question; no strategy search.

## Task (one bounded offline replay, port unchanged, no tuning)
Inputs: NSXUSD 3m bars for 2026-10-06 .. 2026-10-16 with full warm-up as in T-177, the T-162 port
unchanged (`scripts/lab_t162_pine_port.py`), baseline = the 45 signals in
`docs/evidence/t177_signals.json`. Do not edit the port or earlier scripts.

1. Perturbation: add independent Gaussian noise to every 3m bar's open, high, low, close, then
   repair OHLC consistency (high = max, low = min of the four). Registered noise sd in index
   points: {0.5, 1, 2, 4}. 200 draws per sd, seed 179. (No other values.)
2. For each draw run the port and report, per sd: mean and 5th..95th percentile of
   (a) baseline signals that reappear with the same direction at exactly the same bar,
   (b) within +/-3 bars, (c) within +/-30 bars, and (d) the count of new signals that have no
   baseline signal of the same direction within 30 bars.
3. Also report the median absolute difference between consecutive 3m closes (index points) so
   the noise sd can be read against the bar size, and say whether the owner/CFD basis difference
   measured earlier (T-164/T-165, if a number is stored) is larger or smaller than each sd.
4. Using the 31 owner entries: for each sd, the mean number of owner entries with a same-direction
   perturbed signal within +/-3 bars (baseline 3/31) and within +/-30 bars (baseline 15/31).

## Verdict first
"Signals fragile yes/no: at sd <x> (the smallest sd near the stored basis difference, else 1
point) only A% of baseline signals reappear at the same bar and B% within +/-3 bars; new signals
per draw: N. Owner entries within +/-3 bars: m/31 (baseline 3/31), within +/-30: k/31 (15/31)."
Say plainly what it supports: fragile -> bar-exact match from CFD bars is not a fair test and
the owner should decide on a looser criterion (Q-071 option A); stable -> a different
script, version or settings is more likely (Q-071 option C).

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning,
no secrets. Lint with ruff check and ruff format (line length 100; no per-line noqa to hide
errors). Deliverables: `docs/evidence/t179_report.md` (verdict first, short, plain),
`docs/evidence/t179_fragility.json`, `scripts/lab_t179_fragility.py`,
`tests/unit/test_t179_fragility.py` (perturbation determinism by seed and OHLC consistency on tiny
synthetic bars). One log entry, hand the turn to claude, stop.
