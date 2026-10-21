# T-217: Log-only instrumentation to find why the feed socket drops at minute boundaries

## Evidence so far (T-216 and owner logs, 2026-10-21)
- 48 reconnects in ~2 h, all `ConnectionClosedError`, close code 1006 (no close frame), no reason.
- Connection ages cluster at N x 60 s (about 60, 118, 178, 238, 298, 358); drops land at hh:mm:00.
- `seconds_since_ping_reply` equals connection age because the app ping is every 8 minutes, so
  it is not evidence of missed pongs. The keepalive rewrite is NOT approved.
- No DNS/TLS/reset errors around drops. Backfill ~2.5-3.1 s per instrument, similar in dropping and
  non-dropping periods. Source (broker, network path, or local boundary work) is undetermined.

## Required (log-only, no behaviour change)
Add bounded, secret-free instrumentation to the existing recorder process. Do NOT open another
Capital.com session and do NOT change reconnect, ping, backfill, lease, risk or order behaviour.
1. Timestamp every received WebSocket frame with its message type (counts per type per minute
   are enough; do not log payloads).
2. Event-loop scheduling lag: sample every second, log when lag exceeds 250 ms.
3. At each hh:mm:00 boundary log: reader block duration, bar-commit start/end and duration,
   Tai feed-step start/end and duration, and which thread ran them.
4. On every drop log the recursive exception chain: type, message (redacted, bounded),
   errno, `__cause__`, `__context__`, plus the last frame type and time.
5. Write all of it to the rotating volume log (`/workspace/logs/trader.log`). Keep volume of
   lines bounded (aggregate per minute; no per-tick lines).
6. Tests: redaction of exception messages, bounded output, lag sampler fires on a blocked loop,
   and the drop record contains the cause chain.
7. After deployment (owner), the follow-up analysis compares the 10 s before each drop with
   non-drop boundaries. That analysis is a separate task; do not implement fixes here.

## Acceptance
- No trading, risk, gate, dry-run, order, broker-session or secret change. Ruff (100), mypy,
  CI green. One log entry, turn to claude, stop.
