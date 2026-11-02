# T-279: Native Tai signals vs real TradingView alerts (no CSV needed) - read-only

## Problem
The bot already contains a full port of "Tai Trading - Smart Filter" (HA on the chart timeframe x 18,
EMA200, ADX/DI, VWAP, location filter, 5-bar structure stop, max risk 2 ATR, refresh entries, virtual
trade chain; `src/trader/experiments/tai_wiring.py`). It has never been shown to give the same signals
as the owner's TradingView alerts (T-221 stopped for lack of a TradingView CSV). Real alerts are now
logged with timestamps, so the alerts themselves are the ground truth.

## Required (read-only: no broker, order, config, risk, gate or lease change; no secrets)
1. Collect every TradingView alert received since multi-account start (journal/audit/api log):
   epic, direction, alert time, bar close.
2. Replay the recorded Capital.com bars for the same window through the port in BOTH HTF modes
   (`closed` and `forming`), with the virtual trade chain and refresh logic on.
3. Match each alert to a native signal within +-1 bar. Report per mode: alerts matched, alerts with no
   native signal (name the first failing gate), native signals with no alert.
4. For each unmatched alert print the gate inputs side by side (HA open/close of the 18x bar, EMA200,
   ADX/+DI/-DI, VWAP, ATR, VWAP distance, structure stop, risk/ATR, chain state).
5. Verdict: which mode matches best, the single largest cause of mismatch, and a proposed fix.
   Do not apply it. Note Pine facts to check: TP is tested before SL on the same bar (optimistic);
   a BE exit after TP1/TP2 is booked +1R/+2R; `request.security(..., lookahead_off)` shows the forming
   HTF bar live but only the finished bar in history (live signals can repaint vs history).
6. Report to `docs/evidence/t279/report.md` plus `summary.json`.

## Acceptance
Read-only and secret-free; ruff/mypy/CI green for any code; one log entry; turn to claude.
