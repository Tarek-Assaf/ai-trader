# RunPod deployment guide

This guide explains how to start the paper-trading container. You do not need to use a terminal for the steps below. Do not enable live trading for the initial deployment.

## Before you start

You will need a RunPod account, access to the private `ai-trader` container image, and the image tag supplied by the project owner. The image is published to the private GitHub Container Registry package `ghcr.io/tarek-assaf/ai-trader` after a successful CI run on `main`. Use the git SHA tag for a specific build or `latest` for the newest successful main build.

## 1. Create a read-only GitHub Container Registry token

1. In GitHub, open **Settings → Developer settings → Personal access tokens → Tokens (classic)** and choose **Generate new token (classic)**.
2. Give it a recognizable name such as `RunPod image pull`, set an expiration, and grant only the `read:packages` scope.
3. Generate the token and copy it once. Do not paste it into this repository, a pod environment variable, or chat.
4. If GitHub requires it for your account or organization, authorize the token for SSO before using it.

## 2. Create your account and add credit

1. Open [RunPod](https://www.runpod.io/) and create an account or sign in.
2. Open the billing or credits page in the account menu.
3. Add the amount of credit you want to use. Review the displayed billing terms before confirming.

## 3. Add the registry credentials in RunPod

1. In RunPod, open **Settings → Container Registry Credentials** (the menu name can vary).
2. Add a credential for `ghcr.io` using your GitHub username and the token from step 1 as the password.
3. Save the credential. Keep the token only in RunPod's registry credential store.

## 4. Create a persistent network volume

1. In the RunPod console, open **Storage** and choose **Network Volumes**.
2. Choose **Create Network Volume**.
3. Set the size to **100 GB** and choose an available Secure Cloud data center.
4. Save the volume ID and its data center. Keep the pod in the same data center: a network volume is attached when the pod is created and is not moved automatically between data centers.

PostgreSQL's active data directory is on the pod's container disk at `/var/lib/ai-trader/pgdata`. A backup job writes compressed database snapshots to `/workspace/backups` every five minutes and when the container receives a graceful SIGTERM. Keep the network volume mounted at `/workspace`: it holds those backups, Redis's rebuildable AOF cache, logs and other durable files. Treat Redis as a non-authoritative cache; trading records must not exist only in Redis. The application retains the newest 48 database backups and checks each one before restoring it. If a replacement pod starts with an empty PostgreSQL directory, it restores the newest valid backup and verifies the audit hash chain before starting the API.

A hard reset or abrupt loss of the pod can lose up to five minutes of database changes. This is suitable for paper-mode continuity only. Per D-005, move to managed PostgreSQL and Redis before enabling any live trading. RunPod charges for persistent storage while the pod is stopped too; deleting a pod does not delete its network volume. Check the current [network volume documentation](https://docs.runpod.io/storage/network-volumes) for current prices and behavior.

## 5. Add private values as RunPod Secrets

1. Open **Settings** and then **Secrets** (the exact menu label can change).
2. Create a secret for each credential the project owner tells you is needed. Names are listed in the repository's `.env.example`; examples include `CAPITAL_API_KEY`, `CAPITAL_IDENTIFIER`, and `CAPITAL_API_PASSWORD`.
3. Use the RunPod secret value field to enter each value. Do not paste credentials into this guide, a pod template, GitHub, or chat.
4. Do not add credentials for features that are not configured yet. Keep paper mode and demo broker settings for initial verification.

RunPod templates can reference a saved secret in an environment variable using the form `NAME={{ RUNPOD_SECRET_name }}`. See [RunPod's environment variable guide](https://docs.runpod.io/pods/templates/environment-variables). Secret names and required credentials should be confirmed with the project owner before starting.

## 6. Create a CPU pod

1. Open **Pods** and choose **Deploy Pod**.
2. Enter `ghcr.io/tarek-assaf/ai-trader` as the image and select the git SHA tag for the build you want (or `latest`). Select the `ghcr.io` registry credential you added above so RunPod can pull the private image.
3. Select a CPU option with **8 vCPU** and **32 GB RAM**.
4. Select the same data center as the network volume.
5. Attach the network volume created above and set its mount path to `/workspace`.
6. Add these environment variables:

   | Name | Value |
   | --- | --- |
   | `ROLE` | `all` |
   | `TRADING_MODE` | `paper` |
   | `BROKER_ENV` | `demo` |
   | `LIVE_TRADING_ENABLED` | `false` |

Leave `POSTGRES_DATA_DIR` unset for normal paper-mode use. The image default stores PostgreSQL on the pod's container disk and backs it up to the attached network volume. Keep live trading disabled. A custom data directory must be on container-disk storage that supports PostgreSQL's `0700`/`0750` permissions.

7. Add only the saved RunPod Secrets that the owner has confirmed are required. Never type their secret values into ordinary environment fields.
8. Expose the container's **8000** port as HTTP so the health page can be checked. Do not expose database or Redis ports.
9. Review the hourly compute and storage costs shown by RunPod, then choose **Deploy**.

## 7. Check that the pod is healthy

1. Wait for the pod status to show **Running**.
2. Open its **Connect** or **HTTP Service** link for port 8000.
3. Add `/health` to the end of that address.
4. Confirm the page returns JSON with a healthy status, role `all`, paper mode, demo broker, `live_enabled` set to `false`, and database and Redis reported reachable. It also shows the image Git SHA as `version`, plus `last_backup_at` and `last_backup_age_seconds`. For `ROLE=all`, health is degraded if no backup is present or the newest backup is more than 15 minutes old. The container creates a first snapshot at startup, then backs up every five minutes and on graceful shutdown.

When Capital.com credentials are configured, the recorder writes closed bid/ask M1 bars and close spread as monthly Parquet partitions under `/workspace/data/lake/{symbol}/1m_capital/{year}/{month}.parquet`. Rows are idempotently upserted by epic, UTC minute, and side; REST reconciliation takes precedence over duplicate WebSocket updates. The page includes `data_recorder` with ping reply, broker market status and its source, whether prices are expected, any stale epics, reconnect count/reason, and last update/closed-bar times. Capital.com market details provide `snapshot.marketStatus` (`TRADEABLE` or `CLOSED` in the documented and owner-verified samples) and `instrument.openingHours`; the broker status takes precedence over its weekly schedule. Configured holiday and early-close dates are explicit per-instrument overrides and should only contain owner-verified dates. A configured stale-feed threshold is used when broker status is unavailable. Closed markets, daily breaks, and holidays do not trigger stale-price degradation. Without Capital.com credentials, `data_recorder.status` is `not_configured` and the rest of `/health` remains usable.

If the service is not healthy, use the pod's **Logs** view and see Troubleshooting below. Do not copy log output that might contain private account information into a public issue.


### Optional Capital.com demo API check

After deploying a build that includes `scripts/verify_capital_demo.py`, open the pod's **Connect → Start Terminal** page and run:

```sh
cd /app
python scripts/verify_capital_demo.py
```

The check is read-only and refuses to run unless `BROKER_ENV=demo` and `LIVE_TRADING_ENABLED=false`. It uses the Capital.com API key, identifier and password already saved as RunPod Secrets, plus the pod's Redis limiter. Its JSON output contains only the environment, whether the session worked, the number of accounts, candidate market epics/names and the market-status value. It does not print credentials or account balances. Paste that JSON output to the project owner so the NAS100/Gold epic and market-status validation can be recorded. Never paste secrets or full account data.


### Optional Capital.com demo stream check

After deploying a build that includes `scripts/verify_capital_stream.py`, run:

```sh
cd /app
python scripts/verify_capital_stream.py
```

The script observes the demo stream for 90 seconds and prints one redacted JSON result with bid/ask update counts, first and last update times, ping status, reconnect count and epic. It refuses to run unless `BROKER_ENV=demo` and `LIVE_TRADING_ENABLED=false`. For a weekend connectivity check, run `python scripts/verify_capital_stream.py --epic BTCUSD`; BTCUSD is test-only and must never be added to `config/instruments.yaml` or traded. Paste only the resulting JSON to the project owner.



### One Capital.com demo NAS100 round trip (owner authorized)

This is the only command in this runbook that sends a broker order. The owner explicitly authorized one demo round trip for Q-029. Run it only on the intended Pod, while the account has no open positions or working orders. It submits one BUY at Capital.com's current minimum deal size, with a server-side stop, verifies the broker-side stop, tightens that stop once, closes the position, and checks that the account is flat. It refuses unless the process environment explicitly has `BROKER_ENV=demo`, `TRADING_MODE=paper`, and `LIVE_TRADING_ENABLED=false`. The market must be tradeable and publish valid size, tick, quote, and minimum-stop rules.

In **Connect → Start Terminal**, run this command once:

```sh
cd /app && python scripts/demo_round_trip.py
```

The script uses the Capital.com secrets already configured on the Pod and prints no credential values. It saves the same redacted JSON summary under `/workspace/demo_round_trip_*.json`, including order/modify/close deal references, requested and broker-observed size/stop levels, rounding deltas, timings, cleanup attempts and final result. The exclusive `/workspace/.capital_demo_round_trip.once` marker prevents a second order, including after an interrupted or failed run. Do not delete the marker or rerun the command; send the printed JSON summary to the project owner. On any error after submission, the script attempts to close observed positions and cancel working orders, then verifies that none remain. If cleanup cannot confirm a flat account, stop and contact the owner; do not retry.

The adapter currently requires exact equality for broker-confirmed size and stop values. If Capital.com rounds either value, the adapter treats that as a mismatch and requests a close. The report retains the last broker-observed size and stop plus their deltas so the rounding can be reviewed; a failed round trip does not imply a successful validation.

## 8. Import HistData history

The importer reads HistData's live archive selector before downloading. It uses yearly M1 ZIPs where only yearly archives are listed and monthly ZIPs where months are listed. Use a dry run first to see the provider's current coverage; absent early months and years are not requested. The selector currently shows NSXUSD from 2010 (first available month November) and XAUUSD from 2009 (first available month March). Do not substitute a guessed start year.

```sh
cd /app
python scripts/import_histdata.py --symbol NSXUSD --from-year 2010 --through-year 2026 --pause-seconds 1 --dry-run
```

Check Gold separately:

```sh
python scripts/import_histdata.py --symbol XAUUSD --from-year 2009 --through-year 2026 --pause-seconds 1 --dry-run
```

Then run one symbol at a time. The importer ends at the last completed month, even when the current year is selected:

```sh
python scripts/import_histdata.py --symbol NSXUSD --from-year 2010 --through-year 2026 --pause-seconds 1 \
  --raw /workspace/data/raw/histdata --lake /workspace/data/lake \
  --lineage /workspace/data_lineage
```

After reviewing that summary, run the Gold import separately:

```sh
python scripts/import_histdata.py --symbol XAUUSD --from-year 2009 --through-year 2026 --pause-seconds 1 \
  --raw /workspace/data/raw/histdata --lake /workspace/data/lake \
  --lineage /workspace/data_lineage
```

Do not run multiple import commands at the same time against the same volume. A yearly archive is downloaded once, split into isolated month CSVs, and recorded as completed month-by-month; conflicted M1 months use their corresponding monthly tick archive or are rejected without writing M1 rows. Completed months are skipped on reruns, while the yearly ZIP is hash-checked from the mounted cache.

The two August 2026 M1 checksums and two tick checksums in the manifest come from the reviewed HistData QA evidence. For other months and yearly archives, the importer pins the archive's SHA-256 when first downloaded and checks it on later runs. Those first-seen hashes provide cache integrity only; they are not independent verification of HistData's source file. The summary reports the per-year archive plan, imported and skipped months, rows read/written, invalid/conflicting rows, gaps, and rejected months. A partial result exits with status 1. Review rejected months and rerun after correcting the year range or a temporary download problem. Share the redacted JSON summary with the project owner; never share pod secrets.

## 9. Stop and restart safely

- To pause compute, use **Stop** in the pod actions menu. The container disk is temporary; PostgreSQL will recover from the latest valid database dump on the mounted `/workspace` volume when a replacement pod starts. A hard reset can lose up to five minutes of recent database changes. Compute billing stops or changes according to the current RunPod plan, but network-volume storage charges continue while the volume exists. Confirm current terms in the console and [pricing documentation](https://docs.runpod.io/pods/pricing).
- To resume, use **Start** on the same pod and wait for `/health` to become healthy. Keep the attached network volume in place.
- Do not delete the network volume when deleting or replacing the pod; it holds database backups and other persistent data.
- Before changing an image tag or deleting a volume, ask the project owner to confirm a backup exists.

## 10. Rotate the registry token

1. Create a replacement GitHub token with `read:packages` and an expiration.
2. Update the saved `ghcr.io` registry credential in RunPod with the replacement token.
3. Confirm RunPod can pull the image and the pod's `/health` endpoint is healthy.
4. Revoke the old token in GitHub only after the replacement works. Repeat this process before the replacement token expires.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| Image pull or authorization error | Check the exact image name/tag and private registry credentials with the project owner. |
| Pod cannot attach the volume | Confirm the volume and pod use the same Secure Cloud data center and that the volume is not attached elsewhere. |
| `/health` does not open | Check that the pod is running and HTTP port 8000 is exposed; then inspect the pod logs for startup errors. |
| PostgreSQL reports invalid data-directory permissions | The default PostgreSQL directory is on the pod's container disk. If you set `POSTGRES_DATA_DIR` manually, ensure it points to a directory that supports the required `0700`/`0750` POSIX modes. Do not put PostgreSQL's active data directory on a RunPod Network Volume. |
| Database or Redis is unreachable | Restart the pod once. If health remains degraded, stop it and ask the owner to review logs before changing or deleting storage. |
| Invalid configuration | Ask the owner to review the named config error. Do not edit risk limits or the baked risk ceilings on the pod. |
| Unexpected cost | Stop the pod, then review both compute and persistent-volume storage charges in RunPod billing. |

## Screenshots to add

Screenshots are intentionally placeholders until the owner supplies approved account-safe images:

- `[Screenshot: Network volume form with 100 GB selected]`
- `[Screenshot: CPU pod form with /workspace volume attached]`
- `[Screenshot: Pod HTTP service showing /health]`

Never include account IDs, secret values, API keys, or billing details in screenshots.
