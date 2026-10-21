# T-216 recorder reconnect analysis

## Scope

This is a read-only code and evidence analysis. No broker calls, orders,
configuration, risk or gate files, dry-run settings, credentials, or reconnect
behaviour were changed.

## Observed evidence

The owner reported about 41 reconnects over roughly 2.2 hours, exposed as
`ConnectionClosedError`, at approximately one reconnect per three minutes.
The supplied observation has no close codes, close reasons, or timestamped
reconnect lines, so it cannot establish a fixed offset pattern. The count and
cadence are consistent with a repeated peer or network close, but they do not
prove whether Capital.com or an intermediate network device initiated each
close.

## Code path and keepalive timing

`CapitalComStreamClient.run_forever` owns the reconnect loop. It records the
exception type, close code/reason when available, connection age, and time
since the last ping reply and OHLC tick before invoking the recorder callback.
The callback is not a second reconnect source; it only publishes the safe
status fields.

The stream disables the WebSocket library ping (`ping_interval=None`) and sends
the Capital.com application-level `ping` every eight minutes. Each ping waits
up to 30 seconds for its correlated reply. The stream has one synchronous
reader, so ping and OHLC handling do not race. Eight minutes is below the
documented ten-minute keepalive expectation. The observed three-minute closes
therefore do not line up with the application ping interval, and this code
does not provide evidence that the ping timer itself is causing them.

The current evidence cannot identify a broker close versus a client-side
socket error. The existing reconnect log now contains the fields needed for a
future read-only correlation of close timestamps, close codes, ping age, tick
age, and connection age. No reconnect logic change is justified by the
available evidence.

## Recommendation

After the next owner-approved read-only run, collect the rotating recorder log
and correlate each reconnect line with close code/reason and the two age
fields. If close codes remain absent, compare the container's socket/network
termination evidence with the broker session log before changing reconnect or
keepalive behaviour. Keep the current bounded reconnect and eight-minute ping
settings until that correlation identifies a specific failure mode.
