# T-210 Pod inventory (read-only)

**Observed:** 2026-10-20T21:17:51Z from Pod `env@cb87dd6435ad`; image `/health.version` `7f3214f1823c6324bcc5e69889c57e0daadebe2f`; `ROLE=all`. Commands were read-only and printed no secret values.

## Runtime and supervisor inventory

| Component | Observation | Assessment / evidence |
|---|---|---|
| Supervisord | PID 1 started 2026-10-20 19:25:57Z; current process uptime about 1h51m. `supervisord.all.conf` defines only `postgres`, `redis`, `api`, `scheduler`. | Running as designed for the current all-in-one image. `supervisorctl status` could not connect because this image exposes no supervisor control socket; restart counts are therefore not available from `supervisorctl`. `supervisord.log` shows one spawn for each program after the current start. |
| API / health | Uvicorn PID 63, `trader.api.app:app`, port 8000; `/health` returned HTTP 200. | Running as designed; `status=healthy`, DB and Redis reachable. |
| Recorder / scheduler | PID 66, `/app/.venv/bin/python -m trader.workers.recorder`; scheduler log has Capital.com stream, backfill and Tai heartbeat lines. | Running; feed is currently live at the observation, with `feed_stalled=false`, `stale_epics=[]`, `ticks_received=588`, and `last_tick_at=2026-10-20T21:17:17.985895Z`. |
| Redis | `redis-server 127.0.0.1:6379` (PID 54). | Running and `/health` reports reachable. |
| Postgres | PostgreSQL PID 140 on `127.0.0.1:5432`, with checkpointer/background-writer/WAL/autovacuum children. | Running and `/health` reports reachable. |
| PostgreSQL backup loop | `/app/scripts/postgres_backup_loop.sh` PID 168. | Running. `/health.last_backup_at=2026-10-20T21:16:14Z`, age 97s at the snapshot. |
| Worker pool | No `worker` program is present in `supervisord.all.conf` and no `trader.workers.placeholder` process is present. | Not enabled in `ROLE=all`; this is consistent with the single-pod starting role, but no worker autoscaling is active. |

## Architecture component check

| Architecture area | Status | Evidence and gap |
|---|---|---|
| Section 3 control/data/execution overview | **Running but degraded / partial** | API, recorder, Redis, Postgres, Capital.com data recorder and Tai runner are active. The all-role supervisor does not start a worker, Telegram, report, or autoscaler process. |
| 5.2 runtime agents | **Built; not observed in the live process** | `/app/src/trader/agents/` and runtime composition files exist in the image. No live process or log line showed Researcher, Analyst, Strategist, Critic, Reporter or an LLM decision cycle during this inventory. |
| 5.9 Telegram and deterministic controls | **Built and API-wired; runtime delivery not proven** | `channels/telegram.py`, `channels/router.py`, `api/control.py` and `api/app.py` are present. No separate Telegram process is configured in `ROLE=all`, and no successful Telegram delivery line was present in the inspected tail. |
| 5.9 daily/weekly/monthly reports | **Built; not scheduled in this image** | `reports/commands.py` and `reports/core.py` exist, but the only supervisor programs are postgres/redis/api/scheduler; no report scheduler or success line was observed. |
| 5.10 backups | **Running, destination uploads observed** | Backup loop is running; health reports a recent backup. Logs contain secret-free `PASS backup upload` lines with `github_files=... hf_files=...`. The destination-specific environment names are not printed; `HF_TOKEN` is set by name, while `GITHUB_REPO` and `HF_DATASET_REPO` were absent by name in this container. |
| 5.11 research/integrations | **Built; external research feeds not enabled/verified** | Integration/source modules are present. `SEARCH_API_KEY`, `NEWS_API_KEY`, and `FRED_API_KEY` were absent by name; no research/news/macro/calendar runtime activity was observed. Capital.com recorder is active. |
| 5.12 scaling/autoscaler | **Built; not running** | `scaling/autoscaler.py`, `scaling/runtime.py`, and RunPod client files exist. No worker process or autoscaler process was running; only `ROLE=all` core processes were present. |
| Execution plane (Risk Guardian / Order Manager) | **In-process, fail-safe posture; no order evidence** | `risk/guardian.py`, `execution/manager.py`, reconciliation and Tai wiring are present. `/health` reports `mode=paper`, `broker_env=demo`, `live_enabled=false`; no order event was observed in the inspected logs. |
| LLM client / spend cap | **Provider configured by name; live invocation not observed** | `LLM_PROVIDER` is set by name; no provider value or key was printed. No LLM decision-cycle line was observed. |
| Kill/pause controls | **Built and API-wired** | `api/control.py` and router are present; no dedicated process is expected. Runtime operation was not invoked by this read-only inventory. |
| Data recorder: NSXUSD/US100 | **Running** | Health and scheduler logs show US100 ticks, closed bars, reconnects and REST backfill. |
| Data recorder: XAUUSD/GOLD | **Recorded by backfill; no Tai strategy activity observed** | Scheduler log contains `epic=GOLD` backfill start/end lines. Tai heartbeat/startup lines are for `epic=US100`; no XAUUSD strategy-trade line was found. |

## Non-secret flags (names and safe values only)

`ROLE=all`, `TRADING_MODE=paper`, `BROKER_ENV=demo`, `CAPITAL_ENVIRONMENT=demo`, `LIVE_TRADING_ENABLED=false`, `TAI_DEMO_DRY_RUN=false`, `TAI_DEMO_EXPERIMENT=true`, `RUNPOD_CPU_COUNT=2`, `RUNPOD_GPU_COUNT=0`, `RUNPOD_MEM_GB=4`.

Secret presence was checked only by name, never by value. The environment included Capital.com, Telegram, GitHub, Anthropic and RunPod secret names; no secret values are in this report.

## Health snapshot

```json
{"status":"healthy","version":"7f3214f1823c6324bcc5e69889c57e0daadebe2f","mode":"paper","broker_env":"demo","live_enabled":false,"db":"reachable","redis":"reachable","feed_stalled":false,"stale_epics":[],"ticks_received":588,"last_tick_at":"2026-10-20T21:17:17.985895+00:00","last_closed_bar_at":"2026-10-20T21:16:00+00:00","minutes_since_last_bar":1.8,"reconnect_count":36}
```

The 1.8-minute bar lag is within the recorder's normal observed window at the snapshot. T-209's new `tai_demo` counters were not present in this deployed image because its `/health.version` predates the T-209 acceptance commit; this is an image deployment state, not a code change.

No restart, configuration change, order, broker write, or secret operation was performed.
