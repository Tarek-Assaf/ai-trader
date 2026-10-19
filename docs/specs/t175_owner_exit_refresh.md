# T-175: Do the owner's entries follow the Pine refresh rule once the owner's real exits drive the state?

## Why
T-171 to T-174 (all negative): the Open/Close cross (and the Renko cross) is silent at the owner's
entry bars in every HA variant tried (best 6/31 within 2 bars of a cross, noise). The owner's 31
entries collapse to about 9 groups (T-151). In the Pine source, *refresh entries* ignore the HA
state: after an exit, a new entry in the same direction can fire when the smart filter and risk
conditions hold, without any cross. Our port decides exits from its own simulated trades, so its
state (position, direction, lastExitBar, barsSinceExit) drifts from the owner's chart after the
first different exit, and the cross-only view hides the refresh entries. This task removes that
drift by driving the state from the owner's real exits. One hypothesis, numbers only.

## Task (replay only, one bounded run, no tuning, no strategy search)
Import T-162/T-168/T-169/T-171 scripts; do not edit them or the port. NSXUSD 3m, owner window with
full warm-up, baseline VWAP, closed mode (and forming as one second column), other port rules
unchanged. Use `read_owner_exits` (54 owner exit rows) to build, for each owner entry, the owner's
real state: position open or flat, direction of the previous owner position, bars since the owner's
last exit (chart bars), `lastExitBar`.

1. Classify each of the 31 owner entries as `first` (no owner exit in the previous 20 chart bars in
   the same direction and no open owner position) or `refresh` (follows an owner exit in the same
   direction within 20 bars, or adds while the owner position is open as the scale-in tranches do;
   say how many of each, and list the rule used).
2. For every owner entry, at offsets -2..+2 chart bars, with the owner state forced (not the
   port's), report which port conditions pass: direction rule (cross for `first`; Pine refresh
   rule for `refresh`), EMA, ADX/DI, VWAP/location, no-chase, risk OK, cooldown/barsSinceExit.
   Report an entry as reproduced when ALL conditions pass at any offset within +-2 bars.
3. Report counts per class and overall, the lowest-passing condition per class, and the
   `first`-class cross timing histogram (as T-174) so the cross hypothesis is still visible.

## Verdict first
"Owner-exit-driven refresh explains it yes/no; reproduced N of 31 within +-2 (first a of b, refresh
c of d); lowest-passing condition: <name>; closed vs forming: ... ; >= 25 of 31: yes/no." With one
hypothesis and no tuning, 25 of 31 is the bar; anything below 15 of 31 is noise. If reproduced
>= 25 of 31, queue the historical rerun on a port that takes real exits only as a diagnostic
(the rerun itself must use the port's own exits; say what that changes). If below 15, name the next
cause from the numbers only (NQ1 futures vs NSXUSD CFD data, Pine version) and do not test it.

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning,
no secrets. NSXUSD only in the owner window (state it). Lint with ruff check and ruff format
(line length 100). Deliverables: docs/evidence/t175_report.md, docs/evidence/t175_refresh.json,
scripts/lab_t175_refresh.py, tests/unit/test_t175_refresh.py. One log entry, hand turn to claude, stop.
