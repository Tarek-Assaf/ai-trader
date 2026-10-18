# T-164: Is the owner's chart time axis aligned with our recorded bars? (diagnostic only)

## Why
T-163 showed no VWAP anchor, 54m Heikin Ashi anchor or filter subset gets near 25/31. Even the
EMA200 side filter alone passes only 24/29 owner entries, and the full filter set 3-9/29. A
filter that is correct on his chart but wrong on ours at the same timestamp points to a time or
price alignment problem (D-044 clock, NQ1 futures vs Capital.com NAS100 CFD basis, bar label
open vs close), not to indicator settings. T-159 already found a repeatable offset of about
+240 minutes in an earlier (UTC) reading. Test alignment directly, independent of indicators.

## Task (replay only, one bounded Pod run)
1. For each of the 31 owner entries (America/New_York, D-044), take the owner entry price.
   Scan recorded NSXUSD 1m bars (mid) for time shifts from -360 to +360 minutes in 1-minute
   steps. For each shift report the median absolute difference between the owner entry price and
   the close/open/hlc3 of the shifted bar, after removing a constant NQ1-vs-NAS100 basis
   (median difference). Report the best shifts and a permutation p-value (2,000 permutations,
   fixed seed). State whether the best shift is 0 (clock correct) or not.
2. Repeat step 1 per owner entry (best shift per entry) and print the distribution of per-entry
   best shifts, to show whether one constant offset explains them (e.g. DST, bar-open label).
3. Using the best constant shift (if its p < 0.01), re-evaluate the four Pine smart filters
   (EMA200 side, ADX+DI, VWAP side, VWAP distance <= 1.5 ATR) at the shifted entry bar for all
   30 owner entries with a bar, with the T-162 port functions and NY 18:00 equal-weight VWAP.
   Report pass counts per filter and all four, and the T-162 port match count within +-2 bars
   after shifting. Do not tune anything else.
4. Also test bar-label convention: owner entry time = bar open vs bar close (shift by one 3m
   bar) and report filter pass counts for both.
5. Plain verdict first: which shift (if any) aligns the data, and the resulting match count out
   of 31. If a shift gives >= 25/31 reproduced within +-2 bars, say so (Claude then queues the
   historical rerun with the shift documented as a data-clock correction). If not, say what the
   owner must supply (his Pine Inputs/Style screenshot, or a CSV export of his chart bars).

## Rules
Offline replay on the Pod. No broker calls, no orders, sealed holdout never read, no lake
writes, no production-state writes, no tuning or strategy search. NSXUSD 1m/3m/5m/60m used
(1m mandatory); XAUUSD has no owner rows (state it). Reuse scripts/lab_t162_pine_port.py and
scripts/lab_t163_filter_anchor_diagnosis.py without changing their contracts. Stop after the run.

## Deliverables
scripts/lab_t164_clock_price_alignment.py, tests/unit/test_t164_clock_price_alignment.py,
docs/evidence/t164_report.md (plain verdict first), t164_shifts.json, t164_entries.csv.
