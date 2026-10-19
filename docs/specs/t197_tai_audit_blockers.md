# T-197: Fix the T-192 audit blockers (Tai demo runtime)

Source: docs/evidence/t192/audit.md items 6-9. Dry-run stays ON; do not enable real demo orders.

## Scope (in order)
1. Item 7: wire the CME 17:00-18:00 New York break into production bars (`in_break` callback for
   `ThreeMinuteBarBuilder` in `api/app.py`), DST-correct (zoneinfo America/New_York). Test: bars
   spanning the break are not completed/used; a production-wiring test proves the callback is set.
2. Item 8: every refusal outcome (incomplete bar, pause, closed market, no signal, one-position,
   startup block, order-factory refusal, None receipt) writes a journal row; refusals that block a
   would-be entry also send one Telegram alert (throttled, no per-bar spam). Test per outcome.
3. Item 9: wrap exit-side modify/close/partial-close calls; on API error or partial fill: journal the
   event, send exactly one alert, keep the position state persisted (T-191) and fail safe (retry
   next bar, never drop a position). Tests for rejected modify, rejected close, partial fill.
4. Item 6 (spread ceiling): implement ONLY after the owner answers Q-075; until then leave it out.

## Acceptance
- Ruff check and format (line length 100), mypy, full CI green. No broker write calls in tests
  (fakes only), no holdout reads, no risk/gate edits, no secrets, no redeploy.
- Update audit.md with a short "resolved in T-197" table; final line states ready-to-enable yes/no.
- One log entry, hand the turn to claude, stop.
