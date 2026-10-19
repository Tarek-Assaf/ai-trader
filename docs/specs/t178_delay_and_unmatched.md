# T-178: How do owner entries relate to port signals in time, and what are the unmatched ones?

## Why
T-177 (V-441): owner entries sit near same-direction port signals more than chance at 10 and 30
bars (9/31 and 15/31; p 0.0020 and 0.0005), not at 3 bars (3/31, p 0.0955). The median absolute
offset is 36 bars (about 1.8 hours), so a "short manual delay" is not yet shown. Before anyone
asks whether the historical rerun may use a looser criterion, we need to know whether the
coincidence has a stable shape (a consistent delay) or is only broad same-day clustering.

## Task (one bounded offline replay, no tuning, no new signal logic)
Inputs: `docs/evidence/t177_signals.json` (signals and per-owner offsets), the owner CSV (New York
time, D-044), NSXUSD 3m bars as in T-177. Do not edit the port or earlier scripts. No pod run is
needed if the JSON has what you need; otherwise reuse the T-177 script's inputs unchanged.

1. Signed-offset histogram (bars, owner minus signal; positive = owner entered AFTER the signal),
   same-direction nearest signal, all 31 owner positions, bins of 6 bars from -60 to +60 plus
   "outside". Report the median and quartiles of the signed offset for the 15 entries within 30.
2. Same statistic under the T-177 permutation null (2,000 draws, seed 178): how often does a null
   draw give an interquartile range of the signed offsets as narrow as the observed one, and how
   often a share of positive offsets as high as observed? (Tests "consistent delay", not just
   "near".)
3. Precision from the port side: of the 45 port signals, how many have a same-direction owner
   entry within +/-30 bars (report as a count and per day), and the same under the null.
4. The 16 owner entries with no same-direction signal within 30 bars: for each, report the
   nearest signal (any direction) and offset, and which one condition (cross, smart filter,
   risk cap) failed on the owner's entry bar. Table only, no new theory.

## Verdict first
"Delay structure yes/no: of the 15 entries within 30 bars, N were after the signal (median +X
bars, IQR a..b); null p for consistent sign = ...; port-side precision n/45, null mean m; the 16
unmatched entries fail mostly on <condition>." Say plainly what it supports: a stable positive
delay supports "owner enters by hand after the signal" and is the case for the owner to decide on a
looser criterion; no stable delay supports "different script, version or settings".

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning,
no secrets. Lint with ruff check and ruff format (line length 100; no per-line noqa to hide
errors). Deliverables: `docs/evidence/t178_report.md` (verdict first, short, plain),
`docs/evidence/t178_delay.json`, `scripts/lab_t178_delay.py`, `tests/unit/test_t178_delay.py`
(histogram and sign helpers on tiny synthetic input). One log entry, hand the turn to claude, stop.
