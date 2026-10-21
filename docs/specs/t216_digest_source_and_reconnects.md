# T-216: Recorder digest must use the live source; explain reconnect churn

## Problem
At 11:10Z the Telegram "Recorder health digest" reported `status: starting`, `ping_reply_ok: none`,
`market_status: unknown`, `ticks_received: 0`, while `/health` at the same time showed the recorder
`healthy` with 720 ticks and a live feed. The digest is wrong, not the recorder.
`_recorder_health_mapping` in `src/trader/runtime/services.py` reads a recorder object or status
file that the live recorder does not update in this Pod.

## Required
1. Find why the digest source differs from `/health` (which object/file each reads). Show both.
2. Make the digest read the same live status as `/health` (one shared accessor). No new
   data paths that could expose secrets. Fail safe: if the source is unreadable, say
   `unavailable` rather than `starting`.
3. Test: a fake live recorder with ticks>0 yields a digest with the same status, ticks and market
   status as `/health`; an unreadable source yields `unavailable`.
4. Reconnects (read-only analysis, no behaviour change): `reconnect_count` is ~41 in 2.2 h with
   `ConnectionClosedError`, about one per 3 min. Report whether closes happen at fixed offsets,
   whether the broker or our client closes, and whether ping/keepalive timing explains it. Put
   findings in `docs/evidence/t216/report.md` with a recommendation; do not change reconnect
   logic in this task.
5. Write the runner log to a rotating file under `/workspace/logs` (secret-free, size-bounded),
   in addition to container output, so Codex can read it from the terminal.

## Acceptance
- No trading, risk, gate, dry-run, order or secret change. Ruff (100), mypy, CI green.
- One log entry, turn to claude, stop.
