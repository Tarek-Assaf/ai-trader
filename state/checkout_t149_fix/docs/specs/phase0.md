# Phase 0 Spec: Skeleton (task C-001)

Author: Claude. For: Codex. Covers tasks T-001 to T-006. Read `ARCHITECTURE.md` and `AGENTS.md` first.

## Goal
A container that boots locally and on RunPod, loads and validates all configuration, reaches its database and Redis, serves `/health`, and has CI that protects the safety rules. **No trading logic in this phase.**

## Working practice
- Work in small commits on `main`. Every commit message starts with the task id, for example `T-001: repo scaffold`.
- CI must be green before you hand the turn back.
- Python 3.12, `src/trader/` layout, type hints everywhere.
- **Allowed dependencies (start here, keep it minimal):** pydantic, pydantic-settings, PyYAML, SQLAlchemy 2, alembic, psycopg (v3, binary), redis, fastapi, uvicorn, httpx, structlog, APScheduler, pandas, numpy, pyarrow, duckdb, orjson, tenacity, pyotp, jsonschema, supervisor; dev: pytest, pytest-asyncio, hypothesis, ruff, mypy, types-PyYAML.
- **Not allowed without asking Claude (add a `question`):** torch, tensorflow, jupyter, any CUDA library. No GPU in v1 and these bloat the image.
- Choose one lock-file tool (for example `uv` or `pip-tools`), pin versions, and record the choice in `decisions`.

## T-001: Repo scaffold
- Create every package in `ARCHITECTURE.md` section 11 as an importable module with a docstring saying which phase fills it in.
- `pyproject.toml`: ruff (line length 100), mypy (`strict = true` for `trader.risk`, `trader.execution`, `trader.core`), pytest config.
- `Makefile` targets: `dev`, `lint`, `typecheck`, `test`, `build`, `run-paper`, `validate-handoff`, `check` (= lint + typecheck + test + validate-handoff).
- `.env.example` is provided in the repo root. Do not add real values anywhere.
- **Invariant tests from day one** in `tests/invariants/` (they pass trivially now and must keep passing):
  1. AST-based test: no module under `agents/`, `research/`, `lab/`, `chat/`, `analysis/` imports `trader.execution.brokers` or `trader.integrations.capital_com`.
  2. Test: only `control/settings_service.py` may contain write access to `risk.yaml`, `risk_ceilings.yaml` or `strategy_lab.yaml` paths.
  3. Test: no file contains patterns that look like secrets (reuse the CI scanner).
- Acceptance: `make check` passes on the empty skeleton; layout test matches `ARCHITECTURE.md` section 11.

## T-002: Configuration system
- Load every file in `config/` into **frozen** pydantic models with `extra="forbid"` (unknown keys are errors).
- Validation errors must name the file and key, in plain English.
- `risk_ceilings.yaml` is **baked into the image** at a read-only path (for example `/app/baked/risk_ceilings.yaml`) and is never read from the volume. At startup, fail if any value in `risk.yaml` exceeds its ceiling or if `strategy_lab.yaml` values are looser than the floors in the ceilings file.
- Editable files (`risk.yaml`, `strategy_lab.yaml`, others) load from `/workspace/config/` if present, else from the baked defaults. Only the SettingsService (phase 3) will ever write there.
- Secrets come from environment variables only (names in `.env.example`). Never log them; add a redaction filter to the logger.
- Defaults that must never change: `TRADING_MODE=paper`, `BROKER_ENV=demo`, `LIVE_TRADING_ENABLED=false`.
- Acceptance: an invalid config stops startup with a clear message; one unit test per config file; a test proves a risk value above its ceiling is rejected at startup.

## T-003: Docker image and roles
- One image, multi-stage build, `python:3.12-slim` base, runs as a non-root user.
- `entrypoint.sh` reads `ROLE`: `all`, `core`, `worker`, `backtest`.
  - `all` (used first on RunPod): starts, under `supervisord`, the API, a placeholder scheduler, Postgres and Redis **inside the container**, with Postgres data on `/workspace/pgdata` and logs on `/workspace/logs`.
  - `core` and `worker`: same code, no embedded Postgres/Redis, use `DATABASE_URL` and `REDIS_URL`.
  - `backtest`: one-shot job runner (placeholder).
- Run migrations on start for `all` and `core`.
- `GET /health` returns JSON: status, role, version (git SHA), mode, broker_env, live_enabled, db and redis reachability, time (UTC).
- `docker/docker-compose.dev.yml` for local development only (separate Postgres and Redis services). RunPod has no docker-in-docker.
- Acceptance: image builds; `docker run -e ROLE=all` reports healthy; killing and restarting the container keeps data on the mounted volume; process runs as non-root.

## T-004: Database and migrations
- Alembic migrations create: `cycles`, `decisions`, `trade_intents`, `orders`, `fills`, `positions`, `snapshots`, `research_items`, `lessons`, `audit_log`, `worker_heartbeats`, `autoscale_events`, `settings_versions`, `strategy_stages`, `experiment_ledger`, `data_lineage`.
- Every table has `id`, `created_at` (UTC), and where relevant `cycle_id`.
- **`audit_log` is append-only:** a database trigger rejects UPDATE, DELETE and TRUNCATE; each row stores `prev_hash` and `hash` (hash chain) so tampering is detectable. Include a verification function and a test.
- Acceptance: migrations apply on an empty database and are reversible; append-only and hash-chain tests pass.

## T-005: CI
GitHub Actions on every push and pull request: ruff, mypy, pytest, `docker build`, secret scan, and `python agent_state/validate_handoff.py`. On pull requests also run the validator with `--previous` against the base branch version of the file. Acceptance: CI fails on a planted fake key; CI fails on a broken handoff file; CI is green on `main`.

## T-006: RunPod deployment guide (`infra/runpod/README.md`)
Written for a non-programmer, with no assumed knowledge. Cover: creating a RunPod account and adding credit; creating a 100 GB network volume (and why the region matters); creating a CPU pod from the image (8 vCPU, 32 GB RAM) with the volume mounted at `/workspace`; adding Secrets (names from `.env.example`) in RunPod, never in files; setting `ROLE=all`; checking `/health`; stopping and restarting safely; what costs money while stopped. Include screenshots placeholders and a troubleshooting section.

## Phase 0 exit criteria
- Container boots locally and on RunPod (owner confirms with the guide).
- All config validates; ceilings enforced at startup.
- CI green, including handoff validation and secret scan.
- Invariant tests present.
- Codex records a `validations` entry for each task with evidence paths, then hands the turn to Claude for review (C-100).

## Out of scope
Anything that talks to Capital.com, Telegram, an LLM or the internet. That starts in Phase 1 (data) and Phase 3.
