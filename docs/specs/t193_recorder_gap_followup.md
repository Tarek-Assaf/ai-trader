# T-193: Recorder reconnects and missing minutes: Pod measurement (read-only)

Run on the Pod, read-only: `scripts/lab_t189_minute_gaps.py` on the recorder lake for the last 7 days (NAS100 and
XAUUSD) with the market-details JSON and status file; capture the redacted /health data_recorder fields and the
full `runtime alert: dependencies unavailable (...)` line if present; correlate gaps with reconnect log lines
(close code/reason, seconds since last ping reply, connection age). Report missing-minute counts per day and
instrument, and name the most likely cause of the reconnects with the evidence for or against the
"two Capital.com sessions on one key" theory. If supported, propose (do not implement) one session owner or a login
throttle. No broker write, no redeploy, no risk edits, no secrets in output. Deliver `docs/evidence/t193/report.md`.
