# Note for Codex (from Claude, 2026-10-06 20:25 UTC)

I cannot edit the handoff while the turn is yours, so this note carries the instructions. Read it after `git fetch` and before anything else.

## You may be on stale state
- Main is at handoff revision 174, turn=codex. T-028, T-084, T-085, T-086, T-087 and T-070 are done and merged.
- Do NOT use or push to branch `codex/t028-q040-evidence`. It is at revision 159 and obsolete. Start new work on a new branch from `origin/main`.
- Never rewrite `agent_state/AGENT_HANDOFF.json` from tool output (it was truncated at 200,000 characters twice). Edit it with a script: json.load, append, json.dump, then `python agent_state/validate_handoff.py --previous <copy of origin/main's file>`. Validation `result` may only be pass, fail or pending.

## Do now (the owner redeployed the Pod from image `dd8ccb9edd10f98efd7fadb311c90c772c5a4746` at about 20:23 UTC, same environment variables, same volume)
1. Names-only env check on the Pod. Names only, never values. Expected set: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (the code now accepts that name as the owner chat id). Expected missing: LLM_PROVIDER, ANTHROPIC_API_KEY, OWNER_TOTP_SECRET, CONTROL_API_TOKEN.
2. Confirm the image has the scripts: `ls scripts/telegram_smoke.py scripts/telegram_set_commands.py`.
3. `python scripts/telegram_smoke.py` (fixture, no network), then `python scripts/telegram_set_commands.py`, then `python scripts/telegram_smoke.py --live`. The live run sends one "status OK" message to the owner chat. Record the exact UTC time it ran; the owner is watching the Telegram group.
4. Hand to Claude with that time and the redacted outputs in a validation (result pass, fail or pending), resolving B-008 only if the smoke test passed. No credentials, broker calls or orders.

## Then
- Fix the flaky `tests/unit/test_capital_rate_limit.py::test_two_clients_reuse_one_session_with_shared_http_client` by isolating the shared session cache in a fixture.
- Read `docs/specs/phase8.md`. I will add T-090 to T-094 to the handoff on my next turn.
