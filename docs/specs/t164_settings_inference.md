# T-164: Infer which indicator settings the owner's 30 entries are consistent with

## Why
T-163: no VWAP anchor or HA anchor makes the owner's entries pass our filters (best 9/29 pass all
four; EMA alone passes 24/29). 29 of his 31 entries are "refresh" entries, which need only the
smart filters and the risk rule, so the HA anchor barely matters. The disagreement is in the
filter inputs: his chart's settings, or his data (NQ1 futures vs our NAS100 CFD), differ from our
assumption "all four filters on with default values". This task works out which settings his
entries ARE consistent with. It is inference of unknown settings from 30 observed trades, not a
strategy search, and nothing it finds goes into production.

## Steps (offline replay, recorded NSXUSD 3m, 5-16 Oct 2026, owner times = New York)
1. Read the Pine input defaults (docs/evidence/owner/tai_smart_filter_pine_source.txt lines
   ~342-500) and list them in the report with line numbers: useEMA200 and its length, useADX,
   adxLength, adxSmoothing, minimumADX, useVWAPDirection, useLocationFilter, maxVWAPDistance,
   structureBufferATR, atrStopMultiplier, maxRiskATR, refreshBars.
2. For each owner entry with a recorded bar (about 29-30) compute, per filter, the margin by which
   it passes or fails (for example EMA: close minus EMA200 in ATR units; ADX value minus 20;
   DI difference; VWAP distance in ATR units; stop distance in ATR units).
3. Report for each filter on its own: the distribution of margins across the 30 entries, and the
   loosest single threshold that would pass >= 28 of 30 (for example maxVWAPDistance needed,
   minimumADX needed, EMA length alternatives 50/100/200). Use VWAP anchor ny_18:00 as the base
   (best in T-163) and also repeat with utc_midnight.
4. Report the smallest change set (turning filters off, or loosening thresholds) under which
   >= 28 of 30 entries pass the smart filter plus risk rule, and then, using that change set only,
   how many of the 31 owner entries the port reproduces within +-2 three-minute bars, and how
   many extra entries it produces. If the change set is "VWAP location filter off" etc., say so
   in plain words.
5. Check whether the entry price basis explains the failures: compare the offset between owner
   entry prices and our close at the same minute (NQ1 vs NAS100). If the owner CSV has entry
   prices, report the offset series (mean, spread). A drifting offset means VWAP-distance and EMA
   comparisons are not comparable across the two instruments and the filters must be compared in
   relative terms only.
6. Owner-readable verdict first: which of his settings (or data) differences explain the trades.

## Rules
No broker calls, no orders, sealed holdout never read, no lake writes, no strategy search or
profit tuning (only filter pass/fail margins on the 30 owner entries), no production file edits.
Data used: NSXUSD 1m/3m/5m/60m, 1m mandatory; XAUUSD has no owner rows (state it). One bounded
Pod run, then stop.

## Deliverables
scripts/lab_t164_settings_inference.py, tests/unit/test_t164_settings_inference.py,
docs/evidence/t164_report.md, t164_margins.csv, t164_inference.json.
Success: a change set that explains >= 28/30 entries and reproduces >= 25/31 within +-2 bars;
otherwise report the margins and stop (Claude then waits for the owner's settings screenshot).
