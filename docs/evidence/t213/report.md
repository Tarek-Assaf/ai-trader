# T-213 Telegram and report live check

## Scope

Read-only inspection of the Pod image and the source wiring. No Telegram message was sent, no broker call or order was made, and no service was restarted.

## Pod evidence

- The latest API startup line is `2026-10-20 19:26:22,391 INFO Tai demo startup experiment=on dry_run=off ...` followed by `Application startup complete.`
- The API log has no Telegram startup, command-registration, or owner allow-list count line after that restart. Earlier pre-restart lines repeatedly reported `telegram request failed method=getUpdates ... (ConnectError)` and `telegram poll failed (RuntimeError); backing off 16.0s`.
- The scheduled report did run at `2026-10-20 21:30:02,418`, but email delivery failed with `smtplib.SMTPServerDisconnected: Connection unexpectedly closed` during SMTP authentication.
- Source wiring registers `/status`, `/pause`, `/stop`, `/kill`, and `/report`; the API creates a `telegram-long-poll` thread only when the token and owner chat-id allow-list are configured.

## Blocker

The acceptance step requiring proof that `/status` was received and answered needs the owner to send `/status` to the bot. This agent did not send an external Telegram message.

## Missing values

The requested secret names were not printed or inspected for values. Presence/value confirmation remains owner-controlled: `GITHUB_REPO`, `HF_DATASET_REPO`, `SMTP_*`, `REPORT_EMAIL_TO`, `SEARCH_API_KEY`, `NEWS_API_KEY`, `FRED_API_KEY`, and `OWNER_TOTP_SECRET`.
