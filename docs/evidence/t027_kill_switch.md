# T-027 kill switch and pause

The execution control reads independent sources through
`trader.execution.control.CompositeControlSource`. Any kill wins over pause;
any pause wins over run or resume. An unreadable Redis control key fails closed
as a pause, while an available kill flag still wins over a failing peer source.

## Triggers

- Volume flags: create `/workspace/control/pause.flag` to stop entries or
  `/workspace/control/kill.flag` to stop entries and flatten. Kill has priority
  if both files exist. A flag stays active until an operator removes it.
- Redis key: `ai_trader:execution:control`, values `pause`, `kill`, or `resume`.
  The key has no TTL. The authenticated API writes this key; the deterministic
  manager loop reads it before every loop and before and after each order call.
- API: `POST /control/pause`, `/control/kill`, or `/control/resume`, with
  `X-Control-Token` and a JSON body `{"totp_code":"123456"}`. Set
  `CONTROL_API_TOKEN` and `OWNER_TOTP_SECRET` as secrets. Missing or incorrect
  credentials fail closed with 401. The route has no LLM dependency.

Build the shared sources using `build_control_sources(redis_url=...)` and pass
both returned values plus the existing-position management callback to
`OrderManager`. The service loop must call `tick()` every iteration. The
position callback is required at construction: it is called while paused so it
can maintain existing protective management, and must never create entries.

Pause durably blocks new orders but leaves existing positions open and calls
that callback each loop. Kill durably latches and attempts every position close
and working-order cancellation; it verifies the broker reports no remaining
exposure and retries on subsequent loops after errors. The kill latch survives
process restart and cannot be cleared by removing a flag or Redis key. Only an
explicit owner-authenticated resume action may clear it, and only after all
external triggers are clear, broker state reconciles, and kill flattening is
verified. If a broker call or control store is unavailable, execution remains
paused or killed and alerts without consulting an LLM.

Unit checks cover file/Redis/API sources, auth and TOTP, control-source failure,
pause management of an open position, kill during an order, flatten retry,
working-order cancellation, restart/scale-up while killed, and blocked resume.
These are offline checks; no broker or Pod calls were made.
