# T-214: Telegram startup visibility and SMTP report delivery fix

## Why
T-213 (V-512): after the 19:26Z restart the API log shows no Telegram startup, "disabled" or command-registration line, and the
21:30Z scheduled report failed with `SMTPServerDisconnected` during SMTP authentication. Requirements R-01, R-12, R-18.

## Do
1. Read-only first: from code (src/trader/api/app.py around the Telegram block, src/trader/reports/core.py) and the full API log (not
   only the tail) state which `_LOG` line should appear at startup for each case (token missing, owner ids missing, started) and why none
   was seen (log level, log rotation, thread never reached, or channel disabled). Counts only, never values.
2. Smallest fix: always emit one INFO line at startup, "Telegram channel started owner_ids=N commands=[...]" or the existing
   "disabled" reason; same for the report scheduler ("reports scheduled daily/weekly/monthly next=...; email=on|off telegram=on|off").
3. SMTP: `SMTPServerDisconnected` during auth usually means a port/encryption mismatch (port 465 needs SMTP_SSL, 587 needs STARTTLS).
   Make delivery choose SMTP_SSL when SMTP_PORT is 465, add a clear one-line error that names the step, never the credentials.
   Tests with a fake smtp factory for both modes.
4. No trading, risk, gate, dry-run or order changes. No secret values printed.

## Acceptance
Tests, ruff (line 100), mypy, CI green; `docs/evidence/t214/report.md`; one log entry; turn to claude; stop. The live `/status` proof stays
with the owner (Q-077) and is checked after the next Pod image is deployed by the owner.
