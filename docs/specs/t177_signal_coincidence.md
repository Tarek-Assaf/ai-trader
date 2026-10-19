# T-177: Do the owner's entries coincide with the port's signals more than chance?

## Why
T-176 showed the owner's CSV is a TradingView trading-panel export: broker-style tranches
(1 + 2 contracts), 21 "Sell/Buy market order" exits and Bracket orders. The stored Pine file is an
`indicator()`, so the owner placed those orders by hand (or with another script) while looking at
indicator signals. Entry-for-entry matching to the bar (T-162 to T-175, best 9/31) may therefore
be the wrong test: a human clicking after a signal appears is not bar-exact. This task asks the
one question the earlier tests did not: do the owner's entries cluster around the port's signals
at all, compared with chance? It uses a permutation null, so the futures vs CFD price basis and
a constant clock offset do not matter.

## Task (one bounded replay run, no tuning, no strategy search)
Inputs: the owner CSV (New York time, D-044), the faithful port from T-162 unchanged
(`scripts/lab_t162_pine_port.py`, full warm-up as in T-169), NSXUSD 3m bars for
2026-10-06 .. 2026-10-16. Do not edit the port or any earlier script.

1. Run the port over the window and list every signal (entry bar time, direction, first or
   refresh) it emits. Report the count per day.
2. For each of the 31 owner positions (group tranches as in T-176) find the nearest port signal
   of the SAME direction, and the nearest of ANY direction. Report the signed offset in 3m bars
   (negative = owner entered before the signal). Report the histogram for offsets within
   +/-30 bars and the median absolute offset.
3. Permutation null: keep the owner's 31 directions, draw 2,000 random entry-time sets (same
   count, same hour-of-day distribution as the owner's, uniform within the window's trading
   bars), and compute the same statistic (number of owner entries with a same-direction signal
   within +/-3, +/-10 and +/-30 bars). Report observed counts and the one-sided p-value for each
   window. Seed fixed (177).
4. Also report the same statistic with the owner's direction flipped (mirror), as a control.

## Verdict first
"Owner entries coincide with port signals better than chance yes/no: within 3/10/30 bars
observed a/b/c of 31, null mean x/y/z, p = ...; mirror control p = ...; median absolute offset
N bars." Say plainly what it supports: p < 0.01 at 10 or 30 bars supports "the port captures
the owner's signal family, the owner enters by hand with delay" (then the owner decides if
the historical rerun may go ahead under a looser criterion); no significance supports "the owner
chart ran a different script, version or settings".

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning,
no secrets. Lint with ruff check and ruff format (line length 100; no per-line noqa to hide
errors). Deliverables: `docs/evidence/t177_report.md` (verdict first, short, plain),
`docs/evidence/t177_signals.json`, `scripts/lab_t177_signals.py`, `tests/unit/test_t177_signals.py`
(the permutation and offset helpers on tiny synthetic input). One log entry, hand the turn to
claude, stop.
