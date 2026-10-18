# T-163: Why does the Pine port miss the owner's 31 entries? Owner-conditioned replay

Status: spec for Codex. Diagnostic only: no broker calls, no orders, no gate/cost/ledger
writes, no lake writes, sealed holdout never read, no tuning, no new variants beyond the
anchor list below. Pod data only. The owner window (5-16 Oct 2026) exists only for NAS100
(NQ1 on the owner side); XAUUSD has no rows there (T-161). The later historical rerun must
cover 1m/3m/5m/60m, both NSXUSD and XAUUSD, 1m mandatory.

## Why
T-162 ran the port free: it takes its own trades, so one early difference shifts every later
entry (refresh entries depend on the previous exit). 1/31 therefore does not show whether the
ENTRY rules are wrong or whether the run simply drifted. The `first_difference` column was a
generic label, and HA 54m values were blank in the CSV. Also suspicious: forming mode took 0
entries (likely a bug), and closed mode took 25 shorts vs 8 longs.

## Steps
1. **Owner-conditioned replay (main test).** Use the owner's own trades as the state: the port
   is flat exactly when the owner is flat and `lastExitBar` is the owner's exit bar (owner exit
   times from the owner CSV, New York time). For each owner flat period, report the first
   chart bar where the port would enter (after exit + 12 bars for refresh, or on an HA cross)
   and compare with the owner's next entry (0/1/2 bars, direction). Count entries reproduced
   out of 31. This isolates the entry rules.
2. **Per-filter table for every owner entry** (closed and forming): HA state, HA cross within
   2 bars, bars since owner exit (>=12?), EMA200 side, ADX/DI, VWAP side, VWAP distance,
   risk skip, with the real values; name the FIRST failing rule (not a generic label). Show
   the blank HA 54m values fixed: print HA open/close for the 54m group containing each owner bar.
3. **HA anchor audit.** Run the HA step with these 54m group anchors: UTC-epoch (T-162), CME
   session open 18:00 New York, 00:00 New York. For each, count owner entries with an HA cross
   within 2 bars, and print the last 8 HA candles before 5 named owner entries. No other anchors.
4. **Forming-mode bug.** Explain why forming took 0 entries. Fix it if it is a bug and rerun
   steps 1-3 for forming. Explain the 25 short / 8 long imbalance (HA direction vs filters).
5. **Data alignment.** Owner entry price minus recorded NAS100 close at the same 3m bar, for
   all 31: report mean, spread and whether the offset is stable (a stable futures basis
   confirms the time alignment; a wandering offset means the clock or session is off). State
   that volume is zero in the recorder and run VWAP once weighted by time (equal) and once with
   the VWAP filters switched off, as a sensitivity only (no tuning, report counts).
6. **Management check given the owner's entry.** For each owner trade, apply the port exit rules
   (target-first, entry-bar management) from the owner entry using recorded highs/lows in R
   multiples of the port's own stop; report exit-time and exit-type agreement (stop/BE/TP1-3)
   against the owner exit rows.

## Acceptance (decision rule, D-047)
- Entry rules are considered reproduced if step 1 gives at least 25 of 31 within 2 bars.
  Then Claude queues the historical Tai rerun using the port.
- If below 25: the report must name the single rule or alignment that blocks most owner
  entries, with the values; Claude queues the narrowest next fix.
- Report starts with a plain owner verdict (X of 31 with owner-conditioned entries; main cause).
- Tests: unit tests for the conditioned-replay state handling and anchor options; ruff clean
  at line length 100; one log entry; hand turn to claude. Never turn human.
