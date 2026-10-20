# T-213: Prove Telegram commands and scheduled reports work on the Pod (read-only first, then minimal fix)

## Why
T-210 (V-510): Telegram bot, reports and the autoscaler are not separate supervisor programs. Telegram chat and the report scheduler are meant
to run inside the API process (src/trader/api/app.py), but no delivery or report line was seen in the log tail, and the owner has so far only
seen recorder and Tai alerts. Requirements R-01, R-12, R-18 depend on this.

## Do
1. Read-only: in the API log and code, show whether the Telegram channel thread started (startup line), the owner allow-list loaded
   (count only), commands registered (/status, /kill or stop, /pause), and whether the report scheduler has configured delivery and
   its next daily, weekly and monthly run times.
2. Ask the owner (through Claude) to send `/status` to the bot; then show the log line proving it was received and answered.
3. If any part is not running, name the cause and make the smallest fix (wiring or config default) with tests. Do not change trading,
   risk or gate behaviour. Never print token values.
4. List which of these are missing by name only: GITHUB_REPO, HF_DATASET_REPO, SMTP_*, REPORT_EMAIL_TO, SEARCH_API_KEY, NEWS_API_KEY,
   FRED_API_KEY, OWNER_TOTP_SECRET, and what each one enables.

## Acceptance
`docs/evidence/t213/report.md` + `summary.json`; any code fix with tests, ruff, mypy, CI green; one log entry; turn to claude; stop.
