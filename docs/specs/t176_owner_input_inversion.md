# T-176: Which indicator input values would the owner's 31 entries need? (input inversion)

## Why
T-175 (accepted as negative): even with the port's state driven by the owner's 54 real exits, only
7/31 entries pass all conditions within +-2 bars (0/31 first, 7/31 refresh). 30 of the 31 entries are
refresh or scale-in entries. In the refresh class the lowest-passing condition is location (distance
from VWAP, 56 of 150 checks), then risk (90) and ADX/DI (107). A cross is not the main trigger. The
two untested explanations are (a) the owner runs non-default indicator inputs (thresholds, lengths,
toggles off) and (b) a data or version difference (NQ1 futures versus NSXUSD CFD, Pine version).
This task separates them with numbers only: it reads what the indicator values at the owner's
entries are and which input values the owner would need. It tunes nothing.

## Task (replay only, one bounded run, no tuning, no strategy search)
Import T-162/T-168/T-169/T-175 scripts; do not edit them or the port. NSXUSD 3m, owner window, full
warm-up, closed mode (forming as a second column), baseline VWAP. For each of the 31 owner entries,
at the bar of the entry and at offsets -2..+2, record the raw values: EMA gap in ATR, ADX, +DI/-DI,
distance to VWAP in ATR (vwapDistance), close-versus-VWAP side, ATR, and the risk inputs the port
uses. Use the owner state from the 54 exits as T-175 does.

1. For each smart-filter condition, give the distribution (min, p25, median, p75, max) of the
   value at the owner bars, next to the Pine default threshold, and the share of the 31 entries
   that the default admits at the best offset in +-2.
2. For each condition alone, give the least-permissive input value that admits at least 25 of 31
   entries (e.g. maxVWAPDistance needed; ADX minimum needed). State how far it is from the default
   as a ratio. Not a search for the best strategy: one value per condition, read off the data.
3. Report the all-conditions-together result using those 25-of-31 values (one run, reported as a
   fact), and the count with each single condition switched off (toggle off: EMA, ADX/DI, VWAP
   direction, location, no-chase, risk). 6 variants plus baseline, registered here, no more.

## Verdict first
"Owner inputs differ from defaults yes/no; needed values: <condition: value vs default> ...;
all-together with needed values reproduces N of 31; single toggles off: <best toggle> N of 31;
>= 25 of 31: yes/no." Selection caution: 7 variants; gains below 15 of 31 are noise. If no input
change reaches 25 of 31, say the cause is probably data (NQ1 futures vs NSXUSD CFD) or Pine version,
and do not test it. If a combination reaches >= 25 of 31, name it; the next step is to confirm those
inputs with the owner screenshot of the indicator settings, not to adopt them silently.

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning beyond
the registered variants, no secrets. NSXUSD only in the owner window (state it). Lint with ruff check
and ruff format (line length 100). Unit test on a synthetic series for the threshold read-off. The
explains flag is true only when a registered variant reaches >= 25 of 31 within +-2.
Deliverables: docs/evidence/t176_report.md, docs/evidence/t176_inputs.json,
scripts/lab_t176_inputs.py, tests/unit/test_t176_inputs.py. One log entry, hand turn to claude, stop.
