# T-188: Give the Tai demo runtime a real bar feed (found on the Pod, 2026-10-19)

## Finding (evidence from the running Pod, owner terminal 15:16 Riyadh)
`ps aux` shows two separate processes: the API (`uvicorn trader.api.app`, pid 61) and the recorder worker
(`python -m trader.workers.recorder`, pid 65). The only live Capital.com stream is in the worker.
T-186 registers the minute-bar callback on `app.state.recorder`, which is a second `CapitalDataRecorder`
object created inside the API process (src/trader/api/app.py, recorder = CapitalDataRecorder(...)) that
is never connected to a stream. The Tai runtime is built in the API process, so its callback would never
fire: no 3m bar, no signal, no heartbeat. The T-186 CI tests passed because they call the callback by hand.

## Build (read-only; no broker call in tests; no redeploy)
1. Lake tailer in the API process, started only when the Tai experiment is enabled: every 20 seconds read
   the newest closed 1m bid/ask rows of the Tai epic from the data lake written by the recorder worker
   (same parquet files `TaiSignalProvider.from_lake` reads), build mid bars exactly as `from_lake` does,
   and feed each minute not yet seen, in order, to `TaiDemoRuntimeAdapter.on_minute_bar`. Never feed a
   minute twice; never feed an incomplete minute (bid and ask both present, minute closed); log one
   heartbeat line per fed 3m bar with its timestamp and warm-up count.
2. Remove the dead registration on the API-side recorder (or keep it but document it as unused); the
   tailer is the only feed. One feed path, so no double bars.
3. Staleness guard: if no new closed minute arrives for 5 minutes while the market is open, journal and
   Telegram a single "Tai feed stale" alert and do not place orders until bars flow again.
4. Startup log line stating, once: experiment on/off, dry-run on/off, epic, warm-up bar count, start
   balance present yes/no (never the value of secrets).

## Tests
Unit: tailer feeds a fixture lake minute by minute and the adapter receives each minute once, in order;
re-run after a restart does not replay already processed minutes beyond warm-up; incomplete minute
skipped; stale alert fires once. Integration-style test with a temporary parquet lake and a mock source:
60 recorded minutes -> 20 closed 3m bars -> heartbeat lines. CI green (ruff 0.9.6 line 100, mypy, pytest).

## Rules
No broker call in tests, no risk or gate edits, no secrets in logs. Demo only; dry-run default stays true.
Hand back to claude; Claude will then give the owner the redeploy notice.
