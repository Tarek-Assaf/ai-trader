# T-208: Why is the recorder degraded and missing bars? (read-only diagnosis)

## Why
T-207 (V-503): the port finds no Tai candidate in the 12:33-14:34Z window, so zero entries is normal. But the Pod health reports
degraded since ~14:40Z (recorder reconnects, rejected candles, unknown market status) and the runtime logged 107 closed
minutes against 117 in the lake (10 missing). A feed that drops minutes can miss or distort entries, so this must be understood
before we trust a long demo run. Reconnect count was 82 earlier (Q-022, T-193 left the cause unproven).

## Do (read-only on the Pod; no restart, config or code change)
1. From the Pod logs and /health for 12:33Z to now: list each reconnect and rejected-candle event with time and reason string,
   the market_status values seen, and each gap in closed 1m bars (start, end, length). Group by cause.
2. Say which of these are (a) Capital.com session or websocket limits (the 10-minute ping/token expiry, rate limits), (b) our
   reconnect logic failing to resubscribe, (c) a market-status lookup failing, (d) other. Quote the log line for each group.
3. State whether a missing runtime minute is back-filled from the lake/REST before the 3m bar is built, or whether the 3m bar is
   built from an incomplete minute set. Cite the code (src/trader/data/recorder.py, src/trader/experiments/tai_feed.py).
4. Propose the smallest fix, with file and function. Do not implement it.

## Rules
Read-only. No secrets in output. No restart, no config, no orders.

## Acceptance
`docs/evidence/t208/report.md` + `summary.json`; one log entry; turn to claude; stop.
