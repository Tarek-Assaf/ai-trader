# T-150: Analysis journal (screenshot coaching loop)

Owner request (2026-10-13 and later): the agent should write down its reading of a chart BEFORE the
outcome is known; the owner later shares the result (screenshot or text) and the agent compares,
so it learns from its own calls and the owner can coach it. Not a test of the Tai indicator; this
is a build task (D-040). No broker calls, no orders, no edits to risk or gate files.

## Scope
1. Storage: `analysis_journal` table (Postgres migration, plus SQLite-free in-memory repo for unit
   tests). One row per call. Fields: id, created_at (UTC), symbol, timeframe, source
   (`screenshot` / `recorded_data`), image_ref (path or hash, never the image bytes in the table),
   market_state (trend/range/transition/unclear), bias (long/short/none), key_levels (JSON list),
   plan (entry, stop, targets, JSON, may be empty), reasons (short text, max 600 chars),
   confidence (0..1), status (`open`/`resolved`/`void`), outcome (JSON: result in R, what happened,
   source: owner text, owner screenshot or recorded data), resolved_at, lesson (short text).
2. Write path: `journal.add_call(...)` validates fields and REFUSES to write a call whose
   created_at is later than any outcome data supplied (no after-the-fact entries). A call is
   immutable after creation; only the outcome fields can be filled once.
3. Resolve path: `journal.resolve(call_id, outcome)`; for `recorded_data` calls the outcome can be
   computed from the recorder lake (what price did after created_at) with the same cost
   assumptions as the lab (no gate or cost edits).
4. Report: `journal.report()` returns hit rate and mean R by confidence bucket, by market_state and
   by symbol/timeframe, number of open calls, and the list of lessons. Plain-language text plus JSON.
5. Placement: LLM-side module (`src/trader/agents/` or `src/trader/journal/`). It must not import
   the broker, execution or risk modules. The Telegram/chat layer may call add_call and resolve
   through a thin function only.

## Acceptance criteria
- Migration applies in CI; repository tests cover add, resolve-once, immutability, and refusing a
  call dated after its outcome.
- Report test with at least 12 synthetic calls checks bucket arithmetic by hand-computed values.
- Import-boundary test: journal module imports nothing from broker/execution/risk.
- No secrets, no image bytes in the DB, ruff check and ruff format (line length 100) clean.
- One PR, ONE log entry, validation entry, task set to review, turn to claude.
