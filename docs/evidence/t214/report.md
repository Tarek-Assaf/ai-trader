# T-214 evidence

## Scope

Implemented the smallest startup-visibility and SMTP transport fix from `docs/specs/t214_telegram_smtp_fix.md`. No trading, risk, gate, dry-run, or order code was changed. No broker calls, Pod changes, or secrets were used.

## Startup visibility

`src/trader/api/app.py` now emits an INFO line for the report scheduler on every startup path. When configured it reports the schedule, next target, and `email=on|off telegram=on|off`; when unavailable it reports `reports scheduler disabled; email=off telegram=off`. The Telegram branch emits an INFO line with the owner-id count and sorted command names after the long-poll thread starts. Missing token and missing owner IDs retain explicit disabled INFO lines.

`_ReportScheduler.startup_schedule_line()` derives the periods and next scheduled target from the validated schedule and the scheduler clock. It contains no credentials or message contents.

## SMTP transport

`SmtpEmailSender` selects `smtplib.SMTP_SSL` for port 465 and does not call STARTTLS on that path. Ports other than 465 continue to use the existing SMTP connection and STARTTLS flow when authentication is configured. Delivery failures now log only the failed stage and exception type (for example, `SMTP authentication (SMTPServerDisconnected)`), never the host credentials or exception text.

Unit coverage adds a fake SSL SMTP factory that fails if STARTTLS is attempted on port 465, alongside the existing port-587 fake path. Scheduler coverage checks the startup schedule line and next target.

## Validation

- Bundled Python 3.12 `compileall -q src tests`: pass.
- `git diff --check`: pass.
- Focused pytest, Ruff, mypy and `make check` were unavailable in this desktop runtime because the checkout has no installed pytest/Ruff/mypy toolchain and the bundled Python lacks project dependencies (`sqlalchemy` import unavailable). CI remains the required full validation.
- Live `/status` proof remains owner follow-up Q-077 after deployment.
