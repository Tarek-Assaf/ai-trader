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
4. Confirm the page returns JSON with a healthy status, role `all`, paper mode, demo broker, `live_enabled` set to `false`, and database and Redis reported reachable.

If the service is not healthy, use the pod's **Logs** view and see Troubleshooting below. Do not copy log output that might contain private account information into a public issue.

## 8. Stop and restart safely

- To pause compute, use **Stop** in the pod actions menu. The container disk is temporary; PostgreSQL will recover from the latest valid database dump on the mounted `/workspace` volume when a replacement pod starts. A hard reset can lose up to five minutes of recent database changes. Compute billing stops or changes according to the current RunPod plan, but network-volume storage charges continue while the volume exists. Confirm current terms in the console and [pricing documentation](https://docs.runpod.io/pods/pricing).
- To resume, use **Start** on the same pod and wait for `/health` to become healthy. Keep the attached network volume in place.
- Do not delete the network volume when deleting or replacing the pod; it holds database backups and other persistent data.
- Before changing an image tag or deleting a volume, ask the project owner to confirm a backup exists.

## 9. Rotate the registry token

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
