"""Add the analysis journal for pre-outcome chart calls."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_t150_analysis_journal"
down_revision = "0002_t042_llm_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("analysis_journal"):
        return
    op.create_table(
        "analysis_journal",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("image_ref", sa.String(length=512), nullable=True),
        sa.Column("market_state", sa.String(length=32), nullable=False),
        sa.Column("bias", sa.String(length=16), nullable=False),
        sa.Column("key_levels", sa.JSON(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("reasons", sa.String(length=600), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.JSON(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lesson", sa.String(length=600), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_journal_created_at", "analysis_journal", ["created_at"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("analysis_journal"):
        op.drop_index("ix_analysis_journal_created_at", table_name="analysis_journal")
        op.drop_table("analysis_journal")
