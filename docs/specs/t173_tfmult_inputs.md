# T-173: Does the owner's chart use a different HTF multiplier than the Pine default 18?

## Why
T-172 (accepted as negative): none of the four 54-minute bucket anchors x two HA modes comes close to
25 of 31. Best was ny_midnight/forming with 4 of 31 owner entries within 2 bars of a same-direction
cross (baseline 1 of 31). The cross event still lands on different bars, so the 54-minute grid is not
the (only) cause. The Pine `tfmult` input (default 18) sets the Heikin Ashi timeframe (3m x 18 = 54m);
the owner may run another value, or the owner's inputs may differ elsewhere. Numbers only, no guessing.

## Task (replay only, one bounded run, port logic unchanged, no strategy search)
Import the T-162/T-168/T-169/T-171/T-172 scripts; do not edit them. Add `tfmult` as a parameter inside
the new script only. NSXUSD 3m, owner window with full warm-up, baseline VWAP, everything else as T-172.
Registered grid (this is the whole set, no extensions): tfmult in {3, 5, 6, 9, 10, 12, 15, 18, 20, 24,
30, 36, 60} (HA timeframe = 3m x tfmult), anchors {epoch, ny_midnight}, modes {closed, forming}.
52 variants. tfmult 18 + epoch + closed must reproduce the T-171/T-172 baseline (1/3/4/4, 1 of 31).

For each variant report (as T-172): port trades; owner-entry matches exact/+-1/+-2/+-3 of 31; owner
entries within 2 bars of a same-direction cross; median age; histogram. Also report, per tfmult, the
best of its four (anchor, mode) combinations, in one compact table sorted by within-2-bars-of-cross.

## Verdict first
"A different tfmult explains it yes/no; best variant <tfmult, anchor, mode>: owner enters N of 31
within 2 bars of a same-direction cross; matched exact/+-1/+-2/+-3 = a/b/c/d of 31 (baseline 1/3/4/4);
any variant >= 25 of 31 within +-2: yes/no." Selection caution: with 52 variants a small gain is
noise; say so if the best is below 15 of 31 and do not call it a finding. If a variant reaches >= 25 of
31, name it and say the historical rerun is queued on it. If none improves above 8 of 31, name the
next most likely cause from the numbers only (Renko/`setupType`, a different data source such as
NQ1! futures vs the NSXUSD CFD bars, or a different Pine version) and do not test it.

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning beyond
the registered grid, no secrets. NSXUSD only in the owner window (state it). Lint with ruff check and
ruff format (line length 100). Add a unit test for the tfmult parameter on a small synthetic series.
One log entry, hand turn to claude, stop.

## Deliverables
docs/evidence/t173_report.md, docs/evidence/t173_tfmult.json, scripts/lab_t173_tfmult.py,
tests/unit/test_t173_tfmult.py
