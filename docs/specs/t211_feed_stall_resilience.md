# T-211: Feed resilience: stall alert, session renewal, reconnect telemetry (code)

## Why
T-208 (V-504): from 14:34Z the recorder produced no closed bars for 2+ hours while degraded (ping ConnectError, CapitalComError reconnects,
market status unknown, 251 reconnects, startup HTTP 429). Nothing alerted the owner, and the Tai runner could not trade. Four single
minutes were also missing earlier. Reconnect logs cannot show whether subscribe/backfill ever succeeded.

## Do
1. **Stall alert (most important):** in the recorder/scheduler worker, if the market is expected open (or status unknown) and no closed
   1m bar has arrived for 5 minutes, send ONE Telegram alert ("feed stalled since HH:MM, last bar ..."), repeat at most every 30 minutes,
   and send a recovery alert when bars flow again. Alert text has no secrets. Add `feed_stalled` and `minutes_since_last_bar` to /health.
2. **Reconnect telemetry (T-208 proposal):** in `CapitalComStreamClient.run_forever()` log per attempt: attempt id, REST ping ok,
   socket connected, subscribe ok, ping ok, backfill start/end and number of bid/ask updates emitted, exception class on abort. Secret-free.
3. **Session renewal:** check whether the REST session/CST tokens are refreshed when ping or reconnect fails with CapitalComError. If not,
   re-create the session (bounded: backoff, max attempts per minute, handle HTTP 429 with the Retry-After or a long backoff) before the next
   reconnect. Explain in the report what the failure was. Do not change order paths.
4. **Timestamped rejection log:** log each rejected candle once with time, epic, side and reason (bounded rate).
5. Unit tests with fake clocks and fake broker for each item. Ruff (line 100), mypy, CI green.

## Rules
Dry-run flag, risk.yaml, gate files, strategy and order behaviour unchanged. Telegram alert path reuses the existing notifier.

## Acceptance
Tests pass; one log entry; turn to claude; stop. Claude reviews, merges, then the owner redeploys.
