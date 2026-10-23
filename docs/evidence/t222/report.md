# T-222 feed reliability and decision audit

## Scope and safety

This change was prepared in an isolated checkout from `origin/main`. The running
Pod was not modified, restarted, or given new credentials, and no broker order
was placed. The patch is paper-safe and leaves the existing live-trading gates
unchanged.

## Changes implemented

- Capital.com JSON keepalive now runs every 45 seconds, inside the observed
  two-to-three-minute idle-close window.
- WebSocket updates are queued immediately and persisted by the recorder worker
  in bounded batches. The receive callback no longer performs Parquet writes or
  backfill work.
- Every Tai decision now journals the closed bar's canonical OHLC, market flags,
  timeframe, volume, reason, action, and a deterministic SHA-256 fingerprint.
  This makes a screenshot/replay bar directly comparable to the live decision.
- Focused regression tests cover the keepalive bound, queued recorder draining,
  invalid drain limits, and deterministic decision audit fields.

## Verification

- `python -m compileall -q src tests`: passed.
- `git diff --check`: passed.
- Focused pytest execution was attempted but cannot run in this checkout because
  the dev environment is not installed (`pytest` is unavailable). CI or a
  checkout with `uv sync --extra dev` must run the focused tests before merge.

## Remaining operational check

After deployment, observe the existing health and journal endpoints for one
market-open interval. The expected result is no repeated two-to-three-minute
`ConnectionClosedError` cycle, no receive-thread lag caused by Parquet writes,
and one decision audit event per evaluated closed 3M bar. This is a read-only
verification; it does not authorize orders or configuration changes.

