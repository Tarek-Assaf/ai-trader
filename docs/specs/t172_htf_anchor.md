# T-172: Is the 54-minute Heikin Ashi bar anchored differently from TradingView?

## Why
T-171 (accepted as a negative diagnostic): the port's HA cross (`buy_cross` / `sell_cross`) is silent
at 31 of 31 owner entry bars; only 1 of 31 owner entries sits within 2 bars of a same-direction port
cross (median age 46.5 bars, 27 of 31 have no cross in 60 bars). Polarity is not inverted (no condition
has opposite >= 21), and the mirror gives 0 of 31. The other conditions (VWAP side, EMA, ADX/DI) agree
with the owner at roughly 60-80 percent, so the filters are not the main gap: the cross event itself
lands on different bars.

The Pine source builds the cross from `request.security(ticker.heikinashi(...), myTime, ...)` with
`myTime` = 18 x chart timeframe = 54 minutes. TradingView builds an intraday 54-minute bar from the
start of the exchange session, not from UTC epoch multiples. `_ha_series` and `_ha_complete` in
`scripts/lab_t162_pine_port.py` group bars by `epoch_minute - epoch_minute % 54`. Because 1440 is not
a multiple of 54, that grid drifts every day against a session grid, so every 54m bucket boundary,
and with it every HA open/close and cross bar, can be wrong. The earlier clock work (D-044, T-159 to
T-161) fixed the owner CSV clock, not this bucket anchor. This task tests that one hypothesis with
numbers only.

## Task (replay only, one bounded run, port logic unchanged except the registered anchor, no tuning)
Import the T-162/T-168/T-169/T-171 scripts; do not edit them. Add the anchor as a parameter inside the
new script only (copy the grouping code; do not change the port). NSXUSD 3m, owner window with full
warm-up, baseline VWAP, all other port rules unchanged. Bucket anchors (exactly these four):
1. `epoch` : current port grouping (baseline, must reproduce T-171: 1 of 31 within 2 bars).
2. `cme_session` : buckets restart at 18:00 America/New_York every trading day and the last bucket
   is cut at the session break (17:00 New_York); 54-minute steps from the restart, partial last bucket
   allowed (a partial bucket is a normal bucket in TradingView; do not skip it).
3. `ny_midnight` : buckets restart at 00:00 America/New_York each calendar day, partial last bucket allowed.
4. `utc_midnight` : buckets restart at 00:00 UTC each calendar day, partial last bucket allowed.
Run each anchor in both HA modes (`closed`, `forming`). Partial buckets must be built from the bars
that exist (do not skip incomplete groups, that was a port simplification). That is 8 variants.

For each variant report:
- port trades, and owner-entry matches exact, within 1, within 2, within 3 bars of 31 (T-169 matching,
  offset 0);
- the T-171 cross timing: owner entries within 2 bars of a same-direction port cross, median age, and
  the histogram (0, 1-2, 3-5, 6-10, 11-20, 21-60, none);
- direction `same/opposite/none` at the owner bar (as T-171).

## Verdict first
"Anchor hypothesis yes/no; best variant <anchor, mode>: owner enters N of 31 within 2 bars of a
same-direction cross; entries matched exact/+-1/+-2/+-3 = a/b/c/d of 31 (baseline 1/3/4/4); any variant
>= 25 of 31: yes/no." If a variant reaches >= 25 of 31 within +-2, say so and name it; the historical
rerun is then queued on it. If none improves cross timing above 8 of 31, name the next most likely
cause from the numbers only (for example the Renko/`setupType` path, the `originalFilter`, the HA
open formula, or the owner's own input settings differing from the Pine defaults) and do not test it.

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning or
strategy search (the four anchors x two modes are the whole registered set), no secrets. NSXUSD only
in the owner window (say so; 1m coverage statements cover NSXUSD and XAUUSD). Lint with ruff check and
ruff format (line length 100). Add a unit test for the three new anchors on a small synthetic series.
One log entry, hand turn to claude, stop.

## Deliverables
docs/evidence/t172_report.md, docs/evidence/t172_anchors.json, scripts/lab_t172_htf_anchor.py,
tests/unit/test_t172_htf_anchor.py
