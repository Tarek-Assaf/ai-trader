# T-189 recorder reconnect diagnosis

## Scope

This is a read-only code and evidence review. No broker request, order, deploy,
risk setting, gate, or credential was changed. The minute-gap command is
`scripts/lab_t189_minute_gaps.py`; it reads existing Parquet and a saved
Capital.com market-details payload only.

## Reconnect paths

`CapitalComStreamClient.run_forever` has four failure paths:

1. `_ConsumerCallbackFailure`: the update consumer raised; the original
   exception is reported and propagated to the recorder worker.
2. `ConnectionClosed`, `OSError`, `TimeoutError`, or `StreamProtocolError`:
   the stream increments its reconnect counter, logs the exception, calls the
   recorder callback, and retries with bounded exponential backoff.
3. Any other exception: the stream logs the exception, calls the recorder
   callback, and retries with the same backoff.
4. A no-data watchdog raises `StreamProtocolError` only when the market is
   expected open and no OHLC event has arrived for the configured timeout.

Each path now emits one INFO record with the exception class, safe reason,
close code/reason when exposed by the exception, seconds since the last ping
reply, seconds since the last OHLC tick, and connection age. Values that are
not available (for example, a failed TCP connect before a socket exists) are
reported as `unknown`.

## Ping and read ownership

The stream connects with `ping_interval=None`. Its application-level `ping`
request is sent by `_ping`, and `_await_reply` reads from the same synchronous
socket until the matching correlation ID is received. OHLC events encountered
while waiting for the reply are handled inline. There is no second reader
thread in this client, so the current implementation does not create a
ping/read race. The 8-minute interval is below Capital.com's 10-minute ping
expectation; a failed or timed-out reply is treated as a reconnect trigger.

## Owner-provided health observation

The owner reported nine recorder reconnects in roughly 30 minutes, all exposed
as `ConnectionClosedError`, plus two rejected candles with
`ask_close_below_bid_close`. No close code, close reason, or packet trace was
provided, so the exact peer-side cause cannot be proven from this observation.
The common `ConnectionClosedError` shape makes a remote/network/session
termination the leading hypothesis. The new fields will distinguish a close
after a stale ping, a close after a stale tick, and a short-lived connection on
the next safe read-only run.

## Conclusion and next safe check

There is no evidence for a concurrent ping/read bug in this synchronous client.
The safe next step is to run the minute-gap tool against the recorder lake with
the matching market-details JSON and status file, then correlate each gap with
the new reconnect INFO fields. A tiny reconnect behavior change is not
justified until close codes/reasons and the gap timeline are available.
