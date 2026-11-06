# Local VM deployment preflight

Run this before replacing the bot image on the local Ubuntu VM:

```bash
cd /opt/ai-trader/src
bash scripts/local_vm_preflight.sh
```

If it lists an `ai-trader-pre-*` container, review it first. It is a stale
pre-production stack when it shares `/opt/ai-trader/workspace` or
`/opt/ai-trader/pgdata` with `ai-trader`. Remove only after confirmation:

```bash
bash scripts/local_vm_preflight.sh --remove-stale
```

The script disables the stale container's restart policy before stopping it and
removing only that container. It does not delete volumes or data. Then validate
the production container's `/health` endpoint, feed freshness, and the public
TradingView webhook before enabling trading.

The image cannot prevent a separately-created Docker container from restarting;
the preflight is therefore required for every local VM deployment.
