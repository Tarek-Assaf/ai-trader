#!/bin/sh
set -eu
umask 077

BACKUP_DIR=/workspace/backups
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TEMP_FILE="$BACKUP_DIR/.trader-$STAMP-$$.tmp"
FINAL_FILE="$BACKUP_DIR/trader-$STAMP-$$.dump"
HOURLY_DIR="$BACKUP_DIR/hourly"
DAILY_DIR="$BACKUP_DIR/daily"
mkdir -p "$BACKUP_DIR" "$HOURLY_DIR" "$DAILY_DIR"
cleanup() { rm -f "$TEMP_FILE"; }
trap cleanup EXIT HUP INT TERM

if ! pg_dump --host=127.0.0.1 --username=trader --dbname=trader --format=custom --no-owner --no-privileges --file="$TEMP_FILE"; then
    echo "PostgreSQL backup failed: pg_dump could not create the snapshot" >&2
    exit 1
fi
if ! pg_restore --list "$TEMP_FILE" >/dev/null; then
    echo "PostgreSQL backup failed: pg_restore could not validate the snapshot" >&2
    exit 1
fi
if ! mv "$TEMP_FILE" "$FINAL_FILE"; then
    echo "PostgreSQL backup failed: could not publish the validated snapshot" >&2
    exit 1
fi
trap - EXIT HUP INT TERM

# Keep longer recovery points without moving PostgreSQL onto the network volume.
# The five-minute snapshots remain in the root and are retained separately.
cp "$FINAL_FILE" "$HOURLY_DIR/trader-$(date -u +%Y%m%dT%H00Z).dump"
cp "$FINAL_FILE" "$DAILY_DIR/trader-$(date -u +%Y%m%d).dump"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'trader-*.dump' -printf '%f\n' \
    | LC_ALL=C sort -r | sed -n '49,$p' \
    | while IFS= read -r old_backup; do
        [ -n "$old_backup" ] && rm -f "$BACKUP_DIR/$old_backup"
    done

find "$HOURLY_DIR" -maxdepth 1 -type f -name 'trader-*.dump' -printf '%f\n' \
    | LC_ALL=C sort -r | sed -n '25,$p' \
    | while IFS= read -r old_backup; do
        [ -n "$old_backup" ] && rm -f "$HOURLY_DIR/$old_backup"
    done

find "$DAILY_DIR" -maxdepth 1 -type f -name 'trader-*.dump' -printf '%f\n' \
    | LC_ALL=C sort -r | sed -n '15,$p' \
    | while IFS= read -r old_backup; do
        [ -n "$old_backup" ] && rm -f "$DAILY_DIR/$old_backup"
    done

# Extra trading accounts keep their records in their own databases (trader_02, trader_03, ...).
# Each is dumped, validated and kept the same way; a failure here is reported but never undoes the
# main backup above.
ACCOUNT_FAILURES=0
ACCOUNT_DATABASES=$(psql --host=127.0.0.1 --username=trader --dbname=postgres -Atqc \
    "SELECT datname FROM pg_database WHERE datname ~ '^trader_[0-9]{2,3}$' ORDER BY 1" 2>/dev/null || true)
for ACCOUNT_DB in $ACCOUNT_DATABASES; do
    NUMBER=${ACCOUNT_DB#trader_}
    ACCOUNT_DIR="$BACKUP_DIR/accounts/$NUMBER"
    mkdir -p "$ACCOUNT_DIR" "$ACCOUNT_DIR/daily"
    ACCOUNT_TEMP="$ACCOUNT_DIR/.$ACCOUNT_DB-$STAMP-$$.tmp"
    ACCOUNT_FINAL="$ACCOUNT_DIR/$ACCOUNT_DB-$STAMP-$$.dump"
    if pg_dump --host=127.0.0.1 --username=trader --dbname="$ACCOUNT_DB" --format=custom --no-owner --no-privileges --file="$ACCOUNT_TEMP" \
        && pg_restore --list "$ACCOUNT_TEMP" >/dev/null \
        && mv "$ACCOUNT_TEMP" "$ACCOUNT_FINAL"; then
        cp "$ACCOUNT_FINAL" "$ACCOUNT_DIR/daily/$ACCOUNT_DB-$(date -u +%Y%m%d).dump"
        find "$ACCOUNT_DIR" -maxdepth 1 -type f -name "$ACCOUNT_DB-*.dump" -printf '%f\n' \
            | LC_ALL=C sort -r | sed -n '49,$p' \
            | while IFS= read -r old_backup; do
                [ -n "$old_backup" ] && rm -f "$ACCOUNT_DIR/$old_backup"
            done
        find "$ACCOUNT_DIR/daily" -maxdepth 1 -type f -name "$ACCOUNT_DB-*.dump" -printf '%f\n' \
            | LC_ALL=C sort -r | sed -n '15,$p' \
            | while IFS= read -r old_backup; do
                [ -n "$old_backup" ] && rm -f "$ACCOUNT_DIR/daily/$old_backup"
            done
    else
        rm -f "$ACCOUNT_TEMP"
        echo "PostgreSQL backup of $ACCOUNT_DB failed" >&2
        ACCOUNT_FAILURES=$((ACCOUNT_FAILURES + 1))
    fi
done
[ "$ACCOUNT_FAILURES" -eq 0 ] || exit 1
