#!/bin/sh
set -eu
umask 077

BACKUP_DIR=/workspace/backups
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TEMP_FILE="$BACKUP_DIR/.trader-$STAMP-$$.tmp"
FINAL_FILE="$BACKUP_DIR/trader-$STAMP-$$.dump"
mkdir -p "$BACKUP_DIR"
cleanup() { rm -f "$TEMP_FILE"; }
trap cleanup EXIT HUP INT TERM

pg_dump --host=127.0.0.1 --username=trader --dbname=trader --format=custom --no-owner --no-privileges --file="$TEMP_FILE"
pg_restore --list "$TEMP_FILE" >/dev/null
mv "$TEMP_FILE" "$FINAL_FILE"
trap - EXIT HUP INT TERM

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'trader-*.dump' -printf '%f\n' \
    | LC_ALL=C sort -r | sed -n '49,$p' \
    | while IFS= read -r old_backup; do
        [ -n "$old_backup" ] && rm -f "$BACKUP_DIR/$old_backup"
    done
