#!/bin/sh
set -eu

SLEEP_PID=
NIGHTLY_MARKER=/workspace/state/.backup-upload-nightly
STAGE_TRIGGER=${BACKUP_STAGE_TRIGGER_FILE:-/workspace/state/.backup-stage-trigger}
STAGE_DONE=${BACKUP_STAGE_DONE_FILE:-/workspace/state/.backup-stage-uploaded}
UPLOAD_LOCK=/workspace/state/.backup-upload.lock
mkdir -p "$(dirname "$NIGHTLY_MARKER")"

run_backup_upload() {
    marker=$1
    value=$2
    if [ -z "${HF_TOKEN:-}" ] || { [ -z "${GITHUB_BACKUP_TOKEN:-}" ] && [ -z "${GITHUB_TOKEN:-}" ]; }; then
        return 0
    fi
    (
        flock -n 9 || exit 0
        if timeout --signal=TERM 1800 /app/scripts/backup_upload.py; then
            if [ -n "$marker" ]; then
                printf '%s' "$value" > "$marker"
            fi
        else
            echo "Scheduled backup upload failed; inspect the redacted error above" >&2
        fi
    ) 9>"$UPLOAD_LOCK" &
}

run_scheduled_upload() {
    utc_day=$(date -u +%Y%m%d)
    utc_hour=$(date -u +%H)
    if [ "$utc_hour" = "02" ] && [ ! -f "$NIGHTLY_MARKER-$utc_day" ]; then
        run_backup_upload "$NIGHTLY_MARKER-$utc_day" success
    fi
    if [ -f "$STAGE_TRIGGER" ]; then
        stage_mtime=$(stat -c '%Y' "$STAGE_TRIGGER" 2>/dev/null || true)
        stage_done=$(cat "$STAGE_DONE" 2>/dev/null || true)
        if [ -n "$stage_mtime" ] && [ "$stage_mtime" != "$stage_done" ]; then
            run_backup_upload "$STAGE_DONE" "$stage_mtime"
        fi
    fi
}

shutdown() {
    trap - TERM INT
    if [ -n "$SLEEP_PID" ]; then
        kill "$SLEEP_PID" 2>/dev/null || true
        wait "$SLEEP_PID" 2>/dev/null || true
    fi
    if ! /app/scripts/backup_postgres.sh; then
        echo "Final PostgreSQL backup failed; inspect the backup error above" >&2
    fi
    run_scheduled_upload
    exit 0
}
trap shutdown TERM INT

if ! /app/scripts/backup_postgres.sh; then
    echo "Initial PostgreSQL backup failed; inspect the backup error above" >&2
fi
run_scheduled_upload

while :; do
    sleep 300 &
    SLEEP_PID=$!
    wait "$SLEEP_PID" || true
    SLEEP_PID=
    if ! /app/scripts/backup_postgres.sh; then
        echo "Scheduled PostgreSQL backup failed; inspect the backup error above" >&2
    fi
    run_scheduled_upload
done
