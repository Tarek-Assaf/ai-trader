# T-168: Dashboard count rerun with full warm-up (fixes a flaw in T-167)

## Why
T-167 passed `start`/`end` to `port.run`, which cuts the input bars to that window. The port then has
no warm-up (EMA200, 54m Heikin Ashi, ADX, VWAP, ATR all start cold), so the two-day windows gave 0
trades and the Oct 6-16 window started cold too. "forming" mode gave 0 trades in every window, which
is suspicious on its own. The T-167 counts (0 / 33 / 0) therefore say nothing reliable about whether
the port matches the owner's dashboards (53 CFD trades, 115 NQ1 trades). Own mistake in the T-167
spec: it did not say to keep warm-up. This task fixes that.

## Task (replay only, one bounded run, no tuning, port unchanged)
1. Do not edit scripts/lab_t162_pine_port.py. In a new script scripts/lab_t168_counts.py call
   `port.run`-equivalent steps (read_nas100_minutes, resample_ohlc, replay) over ALL NSXUSD/NAS100
   3m data the lake has up to 2026-10-17 NY midnight (at least 5 days of warm-up before 2026-10-06),
   then filter the resulting trades by entry time into the windows: NY midnight 13-15 Oct, UTC
   midnight 13-15 Oct, 6-16 Oct NY, plus per-NY-day counts for every day in the lake.
2. Report for each window and for both modes (closed, forming): trades, SL, TP1, TP2, TP3, BE,
   win rate (state the definition), total R, trades per day. Verdict first, same format as T-167.
3. Forming mode: if it still gives 0 trades, report for the whole range how many bars pass each
   entry condition separately (direction, EMA, ADX/DI, VWAP, no-chase, HA state, refresh) so the
   blocking condition is visible. Do not change the port to fix it.
4. Compare with the owner numbers exactly as in docs/specs/t167_dashboard_count_check.md step 2.
   If counts are within 25 percent and mix within 10 points, say the historical rerun can go ahead
   with a documented caveat (D-050). If not, name the most likely cause from the numbers only.

## Rules
No broker calls, no orders, sealed holdout never read, no lake or production writes, no tuning or
strategy search, no secrets. 1m is mandatory for any lake-wide coverage statement (NSXUSD and
XAUUSD; the owner window has NSXUSD only, say so). Lint with ruff check and ruff format (line
length 100) before handing over. One log entry, hand turn to claude, stop.

## Deliverables
docs/evidence/t168_report.md, docs/evidence/t168_counts.json, scripts/lab_t168_counts.py
