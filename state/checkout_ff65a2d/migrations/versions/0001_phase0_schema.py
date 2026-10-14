"""Create the Phase 0 persistence schema and protect the audit hash chain."""

from __future__ import annotations

from alembic import op

from trader.core.db import metadata

revision = "0001_phase0_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all initial tables and database-enforced audit protections."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    metadata.create_all(bind=op.get_bind(), checkfirst=True)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION trader_audit_log_insert_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE previous TEXT;
        BEGIN
            PERFORM pg_advisory_xact_lock(94210857);
            SELECT hash INTO previous FROM audit_log ORDER BY sequence DESC LIMIT 1;
            NEW.prev_hash := COALESCE(previous, '');
            NEW.hash := encode(
                digest(convert_to(NEW.prev_hash || NEW.payload, 'UTF8'), 'sha256'), 'hex'
            );
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION trader_audit_log_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION verify_audit_log()
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT COALESCE(bool_and(prev_ok AND hash_ok), TRUE)
            FROM (
                SELECT
                    prev_hash = COALESCE(lag(hash) OVER (ORDER BY sequence), '') AS prev_ok,
                    hash = encode(
                        digest(convert_to(prev_hash || payload, 'UTF8'), 'sha256'), 'hex'
                    ) AS hash_ok
                FROM audit_log
            ) AS checks
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_log_chain_before_insert BEFORE INSERT ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION trader_audit_log_insert_guard()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION trader_audit_log_reject_mutation()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION trader_audit_log_reject_mutation()"
    )


def downgrade() -> None:
    """Drop the initial schema and its audit protections."""
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_chain_before_insert ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS verify_audit_log()")
    op.execute("DROP FUNCTION IF EXISTS trader_audit_log_reject_mutation()")
    op.execute("DROP FUNCTION IF EXISTS trader_audit_log_insert_guard()")
    metadata.drop_all(bind=op.get_bind(), checkfirst=True)
