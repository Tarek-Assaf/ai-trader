# T-182: Session-anchored 54-minute Heikin Ashi groups (narrow diagnostic on the T-162 port)

## Why
Claude review of T-181 and re-read of the port (`scripts/lab_t162_pine_port.py` lines 194-260).
The Pine script gets its signal from `request.security(ticker.heikinashi(...), "54", ...)`
(tfmult 18 x 3m). The port builds those 54-minute groups on a **UTC epoch grid**
(`epoch - epoch % 54`) and **drops every group that is not complete**. TradingView does not do
either: its 54-minute bars start at the **session open** of each trading day (CME equity futures
NQ1!: 18:00 New York, Sunday evening for Monday) and restart there every day, because 54 does not
divide 1440. The last bar of a session is shorter, and a bar that straddles the 17:00 to 18:00 New
York maintenance break or a weekend is cut at the session boundary. With the wrong phase, the HA
open/close and the cross bar time are wrong by up to 53 minutes, which fits the typical gap of
about 1.8 hours seen in T-177/T-178 and the 3/31 within 3 bars result. T-179 ruled out data noise;
it did not test the grouping phase.

## Task (diagnostic only; port logic otherwise unchanged)
1. Add an option to the port (default stays the current behaviour so earlier evidence is
   reproducible) `ha_anchor` with exactly these pre-declared values, no sweep, no other offsets:
   - `epoch` (current),
   - `session_1800_ny`: groups of 54 minutes counted from the first 3m bar after each 18:00
     New York session open (America/New_York, DST aware), restarting each trading day; a partial
     last group is kept and cut at the session end (17:00 New York) or at a data gap, never
     dropped; the HA chain (previous HA open/close) continues across groups and days,
   - `rth_0930_ny`: same rule anchored at 09:30 New York (regular hours only chart), reported
     as a secondary check.
2. Keep both HA modes (closed and forming/replay) and the other known differences from T-162
   (refresh without HA, management from the entry bar, target-first, chart-bar counters).
3. Check the session VWAP anchor in the port against the Pine source (`ta.vwap(hlc3)`
   resets at the session open of the chart's exchange, 18:00 New York for NQ1) and report if the
   port resets elsewhere. Fix only if it differs; list it separately.
4. Replay NAS100 3m recorded data 2026-10-05 .. 2026-10-16 with the owner CSV as New York time
   (D-044). For each anchor and HA mode report entries matched within 0/1/2/3 bars and within 30
   bars, extra entries, and per-entry first differing rule for the unmatched ones.
5. Success bar unchanged: at least 25 of 31 entries reproduced. If one anchor reaches it, say
   which and why it is the TradingView rule. If none, state the best result and stop. No tuning,
   no further variants.

## Verdict first
"Best anchor X, mode Y: n/31 within 3 bars (was 3/31), n/31 within 30 bars; gate 25/31 met
yes/no; VWAP anchor correct yes/no."

## Rules
No broker calls, orders, sealed holdout reads, lake or production writes, tuning, secrets. Pod
data only. Lint with ruff check and ruff format (line length 100; no per-line noqa). Tests: add
known-answer cases for the session grouping (partial last group, DST day, weekend gap). Deliver
`docs/evidence/t182_report.md`, `docs/evidence/t182_entries.csv`, `docs/evidence/t182_port.json`.
One log entry, hand the turn to claude, stop.
