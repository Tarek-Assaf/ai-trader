# T-111: Chat answers and demo LLM caps

Goal: a plain-English message in the owner's Telegram chat gets a useful answer, not a label such as "Request classified as query."

## Behaviour
1. `ChatRouter.route` keeps its order: fast commands, refusals and control clarification come first and never reach the LLM.
2. When the LLM returns an intent with no `clarifying_question`, the router runs a read-only handler and replies in plain text:
   - `query` -> the existing `/status`, `/positions`, `/pnl` or `/risk` handler, picked from `arguments.topic` (`status|positions|pnl|risk`; default `status`).
   - `report_request` -> the existing `/report` handler.
   - `chart_request` -> the existing `/chart` path with `arguments.symbol`.
   - `trade_question`, `settings_change_request`, `small_talk` -> one short fixed reply (no handler is invented). Settings changes stay refused here and point to the SettingsService flow.
   - `unknown` -> the existing clarifying question.
3. The reply is deterministic text from the handler. The LLM never writes numbers and never sees account data.
4. The classifier prompt lists the allowed `topic` values. Unknown topic values fall back to `status`.
5. The mock provider path is unchanged.

## Caps
`config/llm.yaml` budget becomes `daily_usd_cap: 2.0`, `monthly_usd_cap: 10.0` (owner holds $10 of credit). Update `tests/unit/test_llm_client.py` fixtures and `docs/specs/phase4.md` examples only if they assert on the old numbers.

## Acceptance criteria
- Tests: each intent maps to the right handler; missing handler gives "<cmd> is unavailable right now."; unknown topic falls back to status; control words still never reach the LLM; LLM error still gives the "limited right now" reply.
- No new broker or order access in the router; no secrets logged.
- Caps changed as above and the config loader test passes.
