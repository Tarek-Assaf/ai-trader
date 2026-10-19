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

## Questions from the 17:15 review block

### (f) Runtime dependency and the six-minute repetition

The supplied Pod line is only the redacted health label
`runtime_dependency_unavailable`; it does not include the dependency detail or
the traceback needed to name one failing service. The composition code checks
the runtime dependencies in this order: Redis, database, paper broker,
control channel, Guardian, instrument mapper/sizing limits, P&L guard and
settings service. The Tai wiring itself then requires a cycle source and the
Capital client. Therefore the current line cannot distinguish (for example)
missing Redis from a missing control channel; the next read-only check must
capture the full `runtime alert: dependencies unavailable (...)` line or the
health detail. The code does not silently convert a missing dependency into a
healthy runtime.

The repetition interval is explained by `_runtime_retry_loop`: it waits 30,
60, 120, 240, then 300 seconds, adding bounded jitter, before retrying the
same composition. The observed roughly six-minute cadence is therefore the
backoff cap plus jitter, not a Capital.com session-expiry timer. The API
Capital client is created once during lifespan and passed into each retry; a
retry does not construct a new client or deliberately log in again.

### (g) API and recorder Capital.com sessions

The API process and `trader.workers.recorder` each construct a
`CapitalComClient`, using the same configured demo key but in separate
processes. The module-level session cache is process-local, so the two clients
do **not** share a token. Each client lazily opens its own session; the API
client sends a keepalive ping after 300 seconds and only opens a new session
after a 401/invalidation. The recorder worker owns the stream login and
reconnect loop. Consequently the six-minute runtime retries do not cause API
logins, but the two independent clients may still be competing server-side
session owners if Capital.com permits only one active session per key. The
safe design conclusion is one process/session owner or a shared session/token
service plus login throttling; this diagnosis makes no risk or order-path
change.

### (h) Tai state, warm-up and heartbeat

Before this review fix, `_build_tai_demo_runtime` created a fresh
`TaiDemoRunner` on every successful runtime retry. That reset the runner's
`_managed` open-position map, the adapter's queued closed bars and the
three-minute builder's pending minutes. The new composition path snapshots and
restores the runner state, queued bars and builder object; it also keeps the
existing signal provider when the rebuild is in-process. The runner still
enforces the broker-side one-position rule from `get_positions()`, so an open
position cannot create a second entry while the runtime is recovering. On a
fresh process start, Tai signal warm-up is loaded from the lake, and the tailer
seeds after the last warm-up minute rather than replaying history as live bars.
A unit test covers a rebuild with an open position and verifies that the
restored runner blocks a duplicate entry and continues to manage the existing
target exit.

The supplied Pod evidence contains the startup line with `warmup_bars=300`,
but no `tai demo heartbeat` line. That means this capture proves the runtime
loaded warm-up but does not prove that a closed 3-minute bar was emitted. The
lake check found only the recently modified NAS100 and XAUUSD monthly Parquet
files; no minute-gap counts were available in the supplied output, so this
report intentionally claims no numeric gap result. Run the existing
read-only `scripts/lab_t189_minute_gaps.py` command against the Pod lake to
fill in the counts and correlate them with the reconnect timestamps.
