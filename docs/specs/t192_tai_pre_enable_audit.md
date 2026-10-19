# T-192: Pre-enable audit of the Tai demo runtime (read-only code audit, checklist for the owner)

## Task
Audit `src/trader/experiments/tai_demo.py`, `tai_wiring.py`, `tai_feed.py` and the composition in
`src/trader/api/app.py` and report pass/fail with the code line for each item, plus a unit test for any item that
lacks one (fix only trivial gaps; larger gaps are reported, not fixed):
1. Only the Capital.com DEMO host can be reached when the experiment is on (config check refuses a live URL/account).
2. Dry-run default true; switching it off needs an explicit setting and is logged once at startup.
3. Kill switch: KILL flattens Tai positions and blocks entries without the LLM; PAUSE blocks entries; both tested.
4. Guardian veto and daily loss/P&L guard apply to every entry; one-position rule enforced from the broker list.
5. Size: first trade 0.1, scaling by current balance over start balance, floor 0.1, rounded down to broker step,
   below broker minimum -> refuse and journal; a missing balance read -> refuse (never guess).
6. Stops and targets sent as price levels; minimum stop distance and broker step rules honoured; entry refused when
   the stop distance is below the broker minimum or the spread at entry exceeds a stated limit (propose the limit).
7. Market-hours and blackout handling (NY clock, CME break 17:00 to 18:00) and stale-feed guard stop entries.
8. Every entry, exit, refusal and veto reaches the journal and Telegram; no secrets or full account ids in logs.
9. Behaviour on partial fills, rejected modify/close calls, and API errors: no silent retry loop, one alert.
Deliver `docs/evidence/t192/audit.md` with a final line "ready to enable: yes/no, blockers: ...".

## Rules
Read-only apart from tests; no broker call; no risk or gate edits; no secrets. CI green. Hand back to claude.
