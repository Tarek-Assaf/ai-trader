# T-209: Per-bar outcome telemetry for the Tai runner (code change)

## Why
T-207 could not verify what the runtime evaluated: logs and the audit table carry no per-bar outcome. Owners and reviewers need to see,
without reading logs, how many 3m bars were evaluated and why each did not become an entry.

## Do
1. In `TaiDemoRunner` (src/trader/experiments/tai_demo.py) count outcomes per epic: evaluated, no_signal, market_closed_or_break,
   stale_or_incomplete, refused (by reason), entry, exit. Reuse existing outcome strings; add no new decisions.
2. Expose the counters since startup in the existing /health payload under `tai_demo` (counts only, no prices, no secrets), and log one
   summary line per hour. For no_signal also keep the last failing gate name (ha_cross, ema200, adx, vwap_distance, swing, risk).
3. Persist nothing new. Counters reset at restart.
4. Unit tests for each counter and a health-payload test. Ruff (line 100), mypy, CI green.

## Rules
Dry-run flag, risk.yaml, gate files, strategy decisions unchanged. No order, broker or config behaviour change. No redeploy by Codex.

## Acceptance
Tests pass; /health shows the new block in a test; one log entry; turn to claude; stop.
