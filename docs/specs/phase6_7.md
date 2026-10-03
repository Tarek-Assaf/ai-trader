# Phase 6 and 7 Spec: Backups and Scaling

Author: Claude. For: Codex. Covers T-060 (backups) and T-070 (scaling). Read `ARCHITECTURE.md` 5.10, 5.12 and 12, `config/backup.yaml`, `config/scaling.yaml`.

## T-060: Backup job
Destinations and contents as in `config/backup.yaml`. Secrets are never backed up.

1. **GitHub (private repo `Tarek-Assaf/ai-trader`):** code, configs, specs, prompts, docs, `agent_state/`, small reports. Never raw data, dumps or secrets. The code is already in Git; this job only pushes generated state (for example strategy specs and small reports) on a dedicated branch `backup/state`, never `main`.
2. **HuggingFace (private dataset repo `Tarek-Assaf/ai-trader-data`):** Parquet lake (`/workspace/data/lake`), backtest results, experiment ledger, encrypted DB dumps, model artifacts. Upload is incremental by file hash so the nightly job does not resend unchanged months.
3. **Schedule:** nightly at `02:00 UTC` and on every strategy stage change.
4. **Local Postgres dumps** (T-008 job) keep the 5-minute cadence; add **hourly and daily snapshots** retention so recovery reaches back further than the 4 hours the 48 newest 5-minute dumps cover: keep 24 hourly and 14 daily, pruned automatically.
5. **Encryption:** DB dumps are encrypted before upload with a key from RunPod Secrets (`BACKUP_ENCRYPTION_KEY`), never stored with the backup.
6. **Secret scan** before every push (reuse the CI scanner). A hit aborts the entire backup, alerts the owner through Telegram and uploads nothing.
7. **Tokens:** fine-grained, repo-limited, in RunPod Secrets only (`GITHUB_BACKUP_TOKEN`, `HF_TOKEN`). They never appear in logs or exceptions (log-capture test on failure paths).
8. **Restore drill:** `scripts/restore_drill` brings up a fresh container from the image, pulls the latest backups, restores the database, verifies the audit hash chain, and checks the lake file hashes against the manifest. The owner runs it every 30 days per config; a report goes to Telegram.

Acceptance: a planted fake secret in a staged file aborts the push (test); a fresh pod restored from backups passes the drill (owner-run on a second pod, then terminated); incremental upload sends only changed files (test with a fake HF client); backups never include files matching secret patterns, `.env`, or the `pgdata` directory.

## T-070: Queue, workers, autoscaler
Policy from `config/scaling.yaml` (disabled until the owner enables it; confirm the hourly spend cap first).

1. **Queue:** Redis streams with priorities P0 to P3; tasks have idempotency keys, timeouts, retries and a dead-letter list.
2. **Workers** run the same image with `ROLE=worker`; they pull tasks by priority. P0 and P1 are never executed on workers that are about to scale down.
3. **Autoscaler** (runs in the core pod only, singleton): reads queue depth, oldest-task age and worker CPU; creates or terminates worker pods through the RunPod API using a token from RunPod Secrets with the narrowest scope available. Hard caps: `max_workers` and `max_hourly_spend_usd`. A kill switch for scaling (`scaling.enabled=false`) takes effect on the next evaluation.
4. **Priority shedding:** under overload, P3 tasks are delayed or dropped first, then P2; P0 and P1 are never shed.
5. **Singleton protection:** Risk Guardian, Execution Service and the decision loop run only on the core pod (leader lock in Redis). Tests prove that a worker cannot acquire these locks and that the autoscaler never targets the core pod.
6. **Orphan control:** worker pods are tagged; on core startup, orphaned worker pods beyond caps are terminated; a spend watchdog terminates workers if the hourly cap is exceeded.

Acceptance: load test with a fake RunPod API scales up and down according to the thresholds and respects the caps; singletons are never scheduled onto workers; shedding order is tested; the cost watchdog fires in a simulated runaway.
