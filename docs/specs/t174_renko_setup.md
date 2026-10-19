# T-174: Does the owner run the Renko trading setup instead of Open/Close?

## Why
T-173 (accepted as negative): no Heikin Ashi timeframe multiplier or bucket anchor reproduces the
owner's 31 entries (best 5/31 within 2 bars, 6/31 cross timing, 52 variants = noise). The Open/Close
cross (`ta.crossover(haClose, haOpen)`) is silent at the owner's entry bars in every variant tried, so
the owner may not be using the Open/Close setup. The Pine source has a second path: input
`setupType` (options "Open/Close" or "Renko"). With "Renko" the original signal is
`ta.crossover/crossunder(renkoEMA1, renkoEMA2)` where `renkoClose` comes from
`request.security(ticker.renko(syminfo.tickerid, "ATR", 3), myTime, close)`, EMA lengths 2 and 10
(inputs "Renko EMA 1/2"). Everything else (smart filter, risk OK, refresh entries) is shared.
The port from T-162 implements only Open/Close. This task tests that one hypothesis, numbers only.

## Task (replay only, one bounded run, no tuning, no strategy search)
Import the T-162/T-168/T-169/T-171/T-172/T-173 scripts; do not edit them. Add the Renko path inside
the new script only. NSXUSD 3m, owner window with full warm-up, baseline VWAP, all other port rules
(smart filter, risk OK, refresh entries, management) unchanged; only `originalBuy/originalSell` is
replaced by the Renko cross (and `trendUp/trendDown` by renkoClose > renkoOpen if the port uses them).

Renko construction (state every assumption in the report): build a Renko series from the 54-minute
bars (tfmult 18, anchor from the grid) with the standard close-based rule: new brick when the 54m close
moves at least one brick size above the last brick high (up) or below the last brick low (down); a
reversal needs two bricks as in TradingView; brick size = ATR(3) of the source bars, frozen at the
time each brick forms. `renkoClose` seen on a 3m chart bar is the last completed brick close as of the
last completed 54m bar (`closed`) or including the forming 54m bar (`forming`).

Registered grid (the whole set, no extensions): ATR source timeframe in {54m, 3m}, bucket anchor in
{epoch, ny_midnight}, mode in {closed, forming}. 8 variants, each as long and short crossings.

For each variant report (as T-173): port trades; owner-entry matches exact/+-1/+-2/+-3 of 31; owner
entries within 2 bars of a same-direction Renko EMA cross; median age; histogram (0, 1-2, 3-5, 6-10,
11-20, 21-60, none); direction same/opposite/none at the owner bar. Also report the number of Renko
bricks and the number of crosses in the window per variant (sanity check).

## Verdict first
"Renko setup explains it yes/no; best variant <atr tf, anchor, mode>: owner enters N of 31 within 2
bars of a same-direction cross; matched exact/+-1/+-2/+-3 = a/b/c/d of 31 (Open/Close baseline
1/3/4/4); any variant >= 25 of 31 within +-2: yes/no." Selection caution: with 8 variants a gain below
15 of 31 is noise; say so and do not call it a finding. If a variant reaches >= 25 of 31 within +-2,
name it; the historical rerun is then queued on it. If none exceeds 8 of 31, name the next most likely
cause from the numbers only (for example refresh-entry timing driven by the owner's real exits, a
different data source such as NQ1! futures versus the NSXUSD CFD bars, or a different Pine version)
and do not test it.

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning beyond
the registered grid, no secrets. NSXUSD only in the owner window (state it). Lint with ruff check and
ruff format (line length 100). Add a unit test for the Renko brick builder on a small synthetic series
(up run, reversal needing two bricks). The `different_*_explains` flag must be true only when
some variant reaches >= 25 of 31 within +-2 (T-173's flag was wrong and was corrected by the reviewer).
One log entry, hand turn to claude, stop.

## Deliverables
docs/evidence/t174_report.md, docs/evidence/t174_renko.json, scripts/lab_t174_renko.py,
tests/unit/test_t174_renko.py
