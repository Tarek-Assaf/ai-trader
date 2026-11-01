# The default build frontend is enough here (no extended syntax is used), so no
# Docker Hub frontend image is pulled. CI may override PYTHON_IMAGE with a mirror.
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS builder

COPY --from=ghcr.io/astral-sh/uv:0.6.3 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    ROLE=all \
    TRADING_MODE=paper \
    BROKER_ENV=demo \
    LIVE_TRADING_ENABLED=false \
    WORKSPACE_ROOT=/workspace

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git gosu postgresql redis-server supervisor \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin trader \
    && mkdir -p /workspace/redisdata /workspace/logs /workspace/backups /var/lib/ai-trader/pgdata /app/baked \
    && chown -R trader:trader /workspace /var/lib/ai-trader

WORKDIR /app
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
COPY --from=builder --chown=trader:trader /app/.venv /app/.venv
COPY --chown=root:root config /app/config
COPY --chown=root:root config/risk_ceilings.yaml /app/baked/risk_ceilings.yaml
COPY --chown=root:root docker /app/docker
COPY --chown=root:root scripts /app/scripts
COPY --chown=root:root migrations /app/migrations
COPY --chown=root:root alembic.ini /app/alembic.ini
COPY --chown=root:root src /app/src
RUN chmod 0444 /app/baked/risk_ceilings.yaml \
    && chmod 0555 /app/docker/entrypoint.sh /app/scripts/start_postgres.sh /app/scripts/backup_postgres.sh /app/scripts/postgres_backup_loop.sh /app/scripts/restore_drill /app/scripts/backup_upload.py /app/scripts/start_redis.sh /app/scripts/start_api.sh /app/scripts/start_account_api.sh \
    && chown -R root:root /app

EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
