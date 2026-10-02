#!/bin/sh
set -eu

BACKUP_VOLUME="ai-trader-backup-$GITHUB_RUN_ID"
cleanup() {
    docker rm --force ai-trader-backup || true
    docker volume rm "$BACKUP_VOLUME" || true
}
docker volume create "$BACKUP_VOLUME" >/dev/null
trap cleanup EXIT

start_container() {
    docker run --detach --name ai-trader-backup --publish 8001:8000 \
        --env ROLE=all --env TRADING_MODE=paper --env BROKER_ENV=demo \
        --env LIVE_TRADING_ENABLED=false --volume "$BACKUP_VOLUME:/workspace" ai-trader:ci
    for attempt in $(seq 1 60); do
        if curl --fail --silent http://127.0.0.1:8001/health > /tmp/backup-health.json; then
            if grep --quiet '"status":"healthy"' /tmp/backup-health.json; then
                return 0
            fi
        fi
        sleep 2
    done
    cat /tmp/backup-health.json
    return 1
}

start_container
docker exec ai-trader-backup test -s /var/lib/ai-trader/pgdata/PG_VERSION
docker exec --user trader ai-trader-backup psql -h 127.0.0.1 -U trader -d trader -v ON_ERROR_STOP=1 \
    -c "INSERT INTO audit_log (id,event_type,payload) VALUES ('11111111-1111-4111-8111-111111111111','restore_test',jsonb_build_object('marker','first')::text)"
docker exec --user trader ai-trader-backup psql -h 127.0.0.1 -U trader -d trader -v ON_ERROR_STOP=1 \
    -c "INSERT INTO decisions (id,payload) VALUES ('22222222-2222-4222-8222-222222222222',jsonb_build_object('marker','first'))"
docker exec --user trader ai-trader-backup /app/scripts/backup_postgres.sh
docker exec --user trader ai-trader-backup sh -c 'for i in $(seq -w 1 50); do printf old > "/workspace/backups/trader-20000101T000000Z-test-$i.dump"; done'
docker exec --user trader ai-trader-backup psql -h 127.0.0.1 -U trader -d trader -v ON_ERROR_STOP=1 \
    -c "INSERT INTO audit_log (id,event_type,payload) VALUES ('33333333-3333-4333-8333-333333333333','restore_test',jsonb_build_object('marker','second')::text)"
docker exec --user trader ai-trader-backup psql -h 127.0.0.1 -U trader -d trader -v ON_ERROR_STOP=1 \
    -c "INSERT INTO decisions (id,payload) VALUES ('44444444-4444-4444-8444-444444444444',jsonb_build_object('marker','second'))"
docker exec --user trader ai-trader-backup /app/scripts/backup_postgres.sh
test "$(docker exec ai-trader-backup sh -c 'find /workspace/backups -maxdepth 1 -name "trader-*.dump" | wc -l')" -eq 48
docker exec --user trader ai-trader-backup sh -c 'printf corrupt > /workspace/backups/trader-99991231T235959Z-corrupt.dump'

# A graceful shutdown must create one final backup before PostgreSQL exits.
docker stop --time 120 ai-trader-backup
docker rm ai-trader-backup
start_container

docker exec ai-trader-backup test -s /var/lib/ai-trader/pgdata/PG_VERSION
docker exec --user trader ai-trader-backup psql -h 127.0.0.1 -U trader -d trader -Atqc \
    "SELECT count(*) FROM audit_log WHERE id IN ('11111111-1111-4111-8111-111111111111','33333333-3333-4333-8333-333333333333')" \
    | grep --quiet -x 2
docker exec --user trader ai-trader-backup psql -h 127.0.0.1 -U trader -d trader -Atqc \
    "SELECT count(*) FROM decisions WHERE id IN ('22222222-2222-4222-8222-222222222222','44444444-4444-4444-8444-444444444444')" \
    | grep --quiet -x 2
docker exec --user trader ai-trader-backup psql -h 127.0.0.1 -U trader -d trader -Atqc "SELECT verify_audit_log()" \
    | grep --quiet -x t
test "$(docker exec ai-trader-backup sh -c 'find /workspace/backups -maxdepth 1 -name "trader-*.dump" | wc -l')" -le 48
