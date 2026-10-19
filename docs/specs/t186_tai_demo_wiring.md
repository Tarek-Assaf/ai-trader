# T-186: Connect the Tai demo runner to the live feed and Capital.com instrument rules

## Why
T-185 built the decision logic (docs/specs/t185_tai_demo_paper.md, D-066, D-067) and tested it with a
mock broker. It cannot trade because nothing feeds it closed 3m bars and signals, and it does not yet
read the broker's real trading rules. Owner 2026-10-19 12:49: "how to fix it?" -> this task.

## Build (demo only; nothing runs against the broker in tests)
1. Bar builder: take the recorder's closed 1m NAS100 (NSXUSD) mid bars and emit one `ClosedBar` per
   closed 3m UTC bucket (minute % 3 == 0 start), only when all three minutes are present; flag
   `market_open` and `in_break` from the existing market-hours code. A partial bucket is never emitted.
2. Signal provider: the T-162 Tai port with the T-182 session-anchored VWAP (18:00 New York reset), as a
   pure function inside src/trader/experiments (do not import from scripts/). Add a parity test that the
   copy returns the same signals as scripts/lab_t180_hist_tai.py on a fixed bar fixture. On start it
   loads enough recent 3m history for warm-up (EMA200 needs >= 200 bars; use at least 300) from the lake.
3. Instrument rules provider (read-only): read the Capital.com market details for the epic through the
   existing broker adapter and map `dealingRules` to `TaiInstrumentRules`: minimum deal size, size step
   (increment), minimum stop/limit distance, plus value per point. Cache for a session, refresh before
   each entry. Add a contract test with a recorded sample JSON (no network in tests). If a field is
   missing, refuse to trade and journal why; never assume defaults.
4. Balance provider (read-only): read the account balance through the existing adapter before each
   entry. Require TAI_DEMO_START_BALANCE (already enforced); journal it once at start.
5. Order factory: build the OrderRequest with price-level stop (the Pine structure stop) and price-level
   targets rounded to the instrument tick (stop rounded away from price, targets toward price). Reject
   the trade, with a journal line, if the stop or any target is closer than the minimum distance. Never
   widen the stop. Size comes from the runner's `_requested_size`.
6. Service wiring in the runtime composition behind TAI_DEMO_EXPERIMENT: one evaluation per closed 3m
   bar, a heartbeat line in the logs, Telegram alerts with the experiment label, kill-switch honoured.
7. DRY-RUN first: new flag TAI_DEMO_DRY_RUN, default true. In dry-run the runner computes sizes, levels,
   rules checks and signals and journals what it WOULD send, but makes no order call. Only the owner
   sets it to false.

## Rules and limits
No live account, no risk or gate file edits, no LLM in the order path, no secrets in code, logs or
backups, no broker call in tests (mock gateway and recorded JSON only). Demo only: the start guard
(BROKER_ENV=demo, TRADING_MODE=paper, LIVE_TRADING_ENABLED=false, TAI_DEMO_EXPERIMENT=true) stays.
Do not redeploy. Advance notice before any redeploy is the owner's call.

## Acceptance
Unit and contract tests for: 3m bar building (gap and partial bucket), signal parity with the lab
script, rules parsing and missing-field refusal, tick rounding and minimum-distance rejection,
dry-run makes no order call, start guard unchanged. CI green (ruff 0.9.6 line length 100, mypy on
src, pytest). One log entry, hand to claude.
