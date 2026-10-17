# Owner request for Codex (2026-10-06 19:02 Riyadh)

The owner added the Telegram variables on the Pod (TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_CHAT_IDS) and no longer has full terminal access to the Pod. Codex has the Pod terminal: please run the checks yourself.

1. On the Pod, print variable NAMES only (never values):
   `env | grep -E 'TELEGRAM|SMTP|OWNER_TOTP|CONTROL_API|ANTHROPIC|LLM_PROVIDER' | sed 's/=.*/=<set>/'`
   Report which of these are set or missing in your handoff summary. Do not paste values, the Pod URL or tokens into any file, log or message.
2. Do this check as part of T-087 (scripts/telegram_smoke.py), and when T-087 is merged run the smoke test on the Pod yourself, then tell the owner the exact time it sent the "status OK" message so the owner can look at the Telegram chat/group.
3. Until T-087 is merged nothing starts the bot, so no message is expected yet. First finish the T-086 fixes (docs/reports/t086_review_note_20261006.md).
