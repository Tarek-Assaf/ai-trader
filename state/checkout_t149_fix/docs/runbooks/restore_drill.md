# T-060 restore drill

Run this from the fresh Pod image after the backup job has produced the
GitHub and HuggingFace artifacts:

```sh
/app/scripts/restore_drill
```

The script accepts `GITHUB_TOKEN` (or the legacy `GITHUB_BACKUP_TOKEN`),
`HF_TOKEN`, and `BACKUP_ENCRYPTION_KEY` from Pod Secrets. Never put a token or
key on the command line. It creates a timestamped scratch directory and the
`trader_restore_drill` database, refuses live target names, verifies the audit
chain and lake manifest hashes, and drops the scratch database before it
returns. The scratch evidence directory is retained unless `--cleanup` is
passed.

The final line is either `PASS SUMMARY` or `FAIL SUMMARY`. Paste the complete
redacted output into the T-060 evidence record; the script never prints token
or key values.

## Upload job

Run the uploader from the same image at `02:00 UTC` and after a strategy stage
change:

```sh
/app/scripts/backup_upload.py
```

It pushes generated state only to the GitHub `backup/state` branch and uploads
the lake, results, ledger and encrypted database snapshots to the private
Hugging Face dataset. Existing SHA-256 manifest entries are skipped. The
PostgreSQL backup script also keeps 24 hourly and 14 daily recovery points in
addition to the 48 five-minute snapshots. A secret-scan failure aborts before
either destination is pushed and sends a fixed redacted Telegram alert when
the Telegram owner secrets are configured.
