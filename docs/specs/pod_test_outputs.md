# Pod tests: outputs Codex must commit for Claude's review

Standing rule (owner decision 2026-10-05): Codex runs the Pod terminal tests and
completes the setup as fast as possible. Claude cannot read the Pod, so after
every Pod test Codex commits the redacted outputs below to `docs/evidence/` in
the same PR as the handoff update, and names the files in the handoff log.
Never include secrets, tokens, TOTP codes, full account ids or credentials; the
scripts already redact, check anyway. Save the originals under `/workspace` too.

Do not wait for Claude to ask: run each test, commit its outputs, hand over.

## 1. T-024 preflight and demo round trip (Q-029)
- Now: `docs/evidence/t024_preflight_20261005.json`, the full redacted JSON from
  `/workspace/demo_round_trip_20261005T121058Z_fd9ef556.json`, plus the image tag.
- Claude records written preflight approval, then Codex runs the ONE order.
- After the order: `docs/evidence/t024_round_trip_<date>.json` (full report: result,
  epic, requested and observed size/stop, rounding deltas, open/modify/close deal
  references, timings, cleanup, flat confirmation), the account response before
  and after (redacted ids), and the marker listing (`ls -l /workspace/.capital_demo_round_trip.once`).
- Also commit the proposed Capital.com account field mapping (which of
  balance/deposit/profitLoss/available is cash and which is equity) with the
  before/after figures that support it, and the margin/exposure source (Q-032).

## 2. Q-022 recorder soak
- The running report scheduler sends a bounded recorder health digest at 2 hours,
  at 24 hours, and every 24 hours thereafter. It includes the redacted
  `data_recorder` fields (status, ping_reply_ok, market_status, ticks_received,
  last_tick_at, last_closed_bar_at, reconnect_count, last_reconnect_reason,
  rejected_candles, last_reject_reason, stale_epics), the version and UTC window.
- The same digest is appended as a JSON line to
  `/workspace/state/evidence/recorder_health.jsonl`, so the next evidence upload
  contains the Pod-generated report without an owner being present.

## 3. T-060 backup restore drill
- Fresh Pod start, restore from GitHub and HuggingFace backups, row counts per
  table before and after, data lake file counts and checksums sample, the
  planted-secret push-abort log (secret value must not appear), backup job logs.

## 4. T-027 kill switch on the Pod (after PR #40 merges)
- Pause, kill and resume exercised through the file flag, the Redis key and the
  API (owner uses the authenticator for the API call): command used, response
  codes, `/health` before/after, broker position count before/after, and the
  alert received. No open position needed; do it with the paper broker.

## 5. T-017 calibration (after enough recorder data)
- Basis measurement HistData vs Capital.com on overlapping windows, spread and
  slippage per instrument, the proposed `config/instruments.yaml` values, and
  the data windows used.

## 6. Any other Pod run
- The exact command, image tag, UTC start and end time, exit code and the
  redacted stdout/stderr, stored in `docs/evidence/` with a descriptive name.
