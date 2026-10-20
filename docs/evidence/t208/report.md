# T-208 recorder degradation and missing minute bars

**Window:** 2026-10-20 12:33:00Z through the read-only check at approximately 16:30Z (the recorder container had been up for about four hours). **Scope:** image-supplied Pod only; no restart, configuration, code, broker write, order, lake write, or secret access was performed.

## Status snapshot

`/workspace/data/recorder-status.json` reported `status=degraded`, `ping_reply_ok=false`, `market_status=unknown`, `market_status_source=unknown,unknown`, `reconnect_count=247`, `rejected_candles=10`, `last_reject_reason=ask_close_below_bid_close`, `ticks_received=796`, and `last_closed_bar_at=2026-10-20T14:34:00Z`.

The scheduler log repeatedly reported `Capital.com market status unavailable (CapitalComError)` and `Capital.com transport error for ping (ConnectError)`. The same window contained 251 precise reconnect records: 202 with `reason=CapitalComError` and 49 with `reason=ConnectionClosedError`; the latter carried WebSocket close code 1006 and unknown close reason. Generic `WebSocket reconnecting (CapitalComError)` warnings were also present. The API log recorded a startup `POST .../api/v1/session` HTTP 429 at 12:33:18Z. This is evidence of provider/session or transport pressure, but does not prove that every reconnect was caused by the 429.

The status file exposes only an aggregate rejection count and the last reason. No per-rejection timestamp or event line is present, so the ten rejected candles cannot be listed individually. The only observed reason is `ask_close_below_bid_close`.

## Missing closed minutes

The active `NAS100/1m_capital/2026/10.parquet` lake file was read read-only. In the inclusive window 12:33Z through 14:34Z there were 122 expected minutes and 118 complete bid minutes plus 118 complete ask minutes. Both sides were absent for four one-minute stamps:

- 2026-10-20 12:41Z
- 2026-10-20 12:45Z
- 2026-10-20 13:42Z
- 2026-10-20 14:11Z

Each gap is one minute; there were no longer contiguous gaps in this window. The earlier T-207 runtime-vs-lake comparison (107 runtime closed-minute lines versus 117 lake bars) is consistent with a feed/runtime observation gap, but the direct lake query above is the authoritative count for this T-208 window.

## Cause classification

### (a) Capital.com session, WebSocket, or provider limits ? observed

The startup session request returned HTTP 429. There were repeated `ConnectionClosedError`/close-code-1006 events, ping `ConnectError` failures, and `CapitalComError` reconnects. These are the strongest observed causes of the degraded recorder. There is no evidence here of a deliberate order or account mutation.

### (b) Reconnect and resubscribe path ? plausible, not proven as a separate defect

`CapitalComStreamClient.run_forever()` in `src/trader/integrations/capital_com/stream.py` calls `_rest.ping()`, opens the socket, validates `_subscribe()`, sends `_ping()`, and then calls `_backfill()` before entering the receive loop. Any exception in that sequence is handled by the broad reconnect path. `_backfill()` calls REST minute history from the last bid/ask watermarks, so the intended recovery is present, but the current `CapitalComError`/`ConnectError` failures can prevent the sequence from completing. The logs do not emit a successful-subscribe or backfill-count marker, so a resubscribe-specific failure cannot be isolated from this run.

### (c) Market-status lookup ? observed

`src/trader/workers/recorder.py` catches all `market_details()`/schedule exceptions, logs `Capital.com market status unavailable`, stores `unknown`, and sets `expected_open[epic]=None`. The status snapshot remained `unknown,unknown`; this prevents a trustworthy market-open expectation and contributes to degraded liveness, but it does not itself synthesize missing candles.

### (d) Other data-quality rejection ? observed, limited telemetry

`src/trader/data/recorder.py` rejects invalid updates and only increments a bounded counter plus `last_reject_reason`. Ten rejections were recorded, with the last reason `ask_close_below_bid_close`. Because there is no timestamped rejection log, their relationship to the four missing minutes cannot be established.

## Backfill and 3-minute build behavior

The stream client does attempt REST backfill after a successful REST ping, WebSocket connect, subscription validation, and ping. That is the only recovery path identified in `stream.py`; it is not a separate lake repair job. `ParquetCandleStore.upsert_many()` persists only accepted updates. `CapitalDataRecorder._commit()` emits a closed minute only after both bid and ask are present, and it does not synthesize missing minutes. `read_closed_lake_minutes()` in `src/trader/experiments/tai_feed.py` skips any minute missing either side. `ThreeMinuteBarBuilder.add()` in `src/trader/experiments/tai_wiring.py` emits a 3-minute bar only when all three expected minute timestamps exist.

Therefore the feed does not fill these gaps before 3-minute construction. Missing or unpaired minutes are skipped, and any 3-minute bucket containing one of the four gaps is withheld rather than built from an incomplete three-minute set. The runtime is consequently fed an incomplete closed-minute sequence until a later REST backfill supplies the missing pair.

## Smallest proposed fix (do not implement in T-208)

Add bounded, secret-free recovery telemetry at the reconnect boundary in `src/trader/integrations/capital_com/stream.py` (the `run_forever()` sequence around `_subscribe()`, `_ping()`, and `_backfill()`): log a reconnect-attempt id with subscribe success/failure, backfill start/end, number of bid/ask updates emitted, and the exception class when the sequence aborts. Keep the existing REST backfill and no-fill semantics unchanged. This is the smallest change that distinguishes provider/session failure from a resubscribe or backfill failure and lets the next run prove whether each missing minute was repaired. A separate data-quality change may later add timestamped bounded rejection counters, but that is outside T-208.

## Limitations

- The Pod exposes no historical CPU/memory or per-rejection time series in the image-supplied files; resource sizing was therefore based on cgroup counters and current process snapshots, not a sampled three-hour maximum.
- The log window has no successful-subscribe/backfill-count records, so resubscribe failure is not independently proven.
- No broker order, account mutation, configuration change, restart, or lake write was performed.

