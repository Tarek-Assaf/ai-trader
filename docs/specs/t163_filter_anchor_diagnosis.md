# T-163: Why do the owner's entries fail our filters? (diagnostic only)

## Finding from T-162 (Claude's reading of docs/evidence/t162_entries.csv)
The faithful port reproduced 1/31 owner entries. Applying the Pine smart filters to the recorded
bar at each of the 30 owner entries that have a bar, only **3 of 30** pass all four filters:
EMA200 side 25/30, ADX+DI direction 22/30, VWAP side 18/30, VWAP distance <= 1.5 ATR 13/30.
The owner's trades were taken with his filters, so something in our indicator inputs differs
from his chart. Most likely: (a) VWAP anchor/weighting (recorder bars have volume 0, so our
VWAP is equal-weighted; ta.vwap(hlc3) resets at the session start of the exchange, not UTC
midnight), (b) 54m Heikin Ashi group anchor (we used UTC epoch; TradingView anchors to the
session), (c) the owner turned some filters off or uses other settings (unknown), (d) NQ1 futures
vs Capital.com NAS100 CFD differences (basis, volume).

## Task (diagnostic, no tuning of production, no strategy search)
Replay-only on recorded NAS100 3m, 5-16 Oct 2026, owner times read as America/New_York (D-044).
1. VWAP variants: anchor = UTC midnight, New York 18:00 (CME Globex session open), New York
   09:30, New York 00:00; weighting = equal (typical price), tick-count if available. For each
   variant report how many of the 30 owner entries pass VWAP side and VWAP distance, and all
   four filters together.
2. 54m HA group anchor variants: UTC epoch, NY 18:00 session start, NY 00:00, NY 09:30, and
   plain "every 18 bars from the first bar of the day". For each, report how many owner entries
   have a HA cross on the entry bar or the same-colour state (non-refresh entries), and how many
   port entries land within +-2 bars of an owner entry (combine with the best VWAP variant).
3. Filter subset analysis: for every subset of the four filters (EMA, ADX, VWAP side, VWAP
   distance) report the fraction of owner entries passing, and the number of extra port entries
   the subset produces. State whether any single subset explains >= 28/30 owner entries.
4. Also report ATR(14) risk rule (stop distance > 0 and <= 2.0 ATR) pass count per entry.
5. Print an owner-readable verdict: which difference, if any, explains the owner's trades, and
   what to ask the owner (screenshot of indicator settings) if none does.

## Rules
Offline replay on the Pod; no broker calls, no orders, sealed holdout never read, no lake writes,
no tuning, no new strategy search. Lake data used: NSXUSD 1m/3m/5m/60m recorded in the window
(1m mandatory); XAUUSD has no owner rows, state this. One bounded Pod run. Reuse
scripts/lab_t162_pine_port.py functions; do not change that file's contract.

## Deliverables
scripts/lab_t163_filter_anchor_diagnosis.py, tests/unit/test_t163_filter_anchor_diagnosis.py,
docs/evidence/t163_report.md (starts with plain verdict), t163_variants.json, t163_entries.csv.
Success: a variant or subset that makes >= 25/31 owner entries reproducible within +-2 bars, or a
clear report of which owner setting is needed (then Claude asks the owner). Stop after the run.
