# T-149: Tai Smart Filter on the recorded Capital.com data (5-16 Oct 2026)

Status: spec for Codex. Start after T-148 is handed over (or earlier only if Claude says so in
the handoff). Diagnostic only: no broker calls, no orders, no Gate 1, no ledger, trial-counter
or promotion writes, no edits to risk, gate or cost files. Owner request 2026-10-16 11:02 Riyadh.

## Why
The Pod recorder has stored real Capital.com 1-minute bid/ask bars since 5 Oct 2026. The owner
trades the Tai Smart Filter indicator on 3m and reports about 30 trades with a 70% win rate.
At about 3.6 signals a day on 3m our own history gives roughly 40 signals in 11 days, so the
recorded window may be the one the owner is looking at. This task runs the owner's rules
unchanged on that real data so his trades can be compared entry by entry.

## Data (read only)
- Files: DATA_LAKE_DIR (default /workspace/data/lake)/<symbol>/1m_capital/2026/10.parquet for
  symbols NAS100 (Capital.com name for NSXUSD) and XAUUSD. Owner confirmed 2026-10-16: NAS100
  has days 5-9 and 11-16 Oct, XAUUSD 5-9 and 12-16 Oct, about 11,600 and 9,500 one-minute bars.
  Rows are per (epic, UTC minute, side bid/ask) with OHLC and spread. Do not modify the files.
- Before anything else confirm that these dates are outside the sealed holdout definition.
  If any recorded minute lies inside it, stop and ask in the handoff.
- Data check first: per symbol first and last minute, missing minutes inside trading hours, bars
  with bid > ask or zero spread, and spread (points) by UTC hour: median, 90th percentile, max.

## Simulation
- Reuse scripts/lab_tai_smart_filter.py rules exactly as accepted in T-147 (signal timeframe =
  chart timeframe x 18 built from closed chart bars, Heikin Ashi cross, EMA200 side, ADX(14,14)
  >= 20 with DI side, VWAP side, 1.5 ATR no-chase limit, swing stop plus 0.2 ATR capped at 2 ATR,
  refresh entries after 12 bars). No parameter changes.
- Chart timeframes 1m, 3m, 5m for both symbols (60m is too short here; say so).
- Indicators on the bid/ask mid price; fills on the real side: long entries at ask, long exits
  at bid, short entries at bid, short exits at ask, so the recorded spread is the real cost.
  Stop-first if stop and target hit in the same bar. Report in points and R.
- Exit accounting as in T-147: owner scale-out (a third at 1R, 2R, 3R, stop to entry after 1R)
  as the real result, plus the dashboard accounting for comparison, plus single exits 1R/2R/3R.
- Warm-up: indicators may use the earlier histdata only if clearly stated; otherwise skip the
  first trades until 200 chart bars exist and state when trading starts.

## Output
- docs/evidence/t149_recorded_tai_summary.json and docs/evidence/t149_recorded_tai_table.md.
- A trade list file docs/evidence/t149_recorded_tai_trades.csv: symbol, timeframe, direction,
  signal time in UTC and in Riyadh (UTC+3), entry, stop, TP1/TP2/TP3, exit time, exit reason,
  result in points and R (real and dashboard), spread at entry.
- Per symbol and timeframe: trades, trades per day, win rate (any target reached, and positive
  real result), net points, total R, max drawdown. Say plainly that 11 days is a first look.
- If the owner has supplied his trade list in docs/owner/ (date, time, direction, result),
  add a side-by-side: which of his trades match a signal in time (+/- 3 bars) and direction,
  which signals he did not take, which of his trades have no signal. Do not invent his data.

## Acceptance
Data check done and reported, all six symbol/timeframe runs complete, trade list readable by a
non-programmer, no writes to the lake, safety flags clean (no broker, no orders, no holdout),
tests, ruff check and ruff format at line length 100 BEFORE handover, UTF-8 intact, one log entry,
turn handed to claude.
