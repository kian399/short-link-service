"""add is_active for soft delete

Revision ID: 0005_soft_delete
Revises: 0004_stats_daily
Create Date: 2026-11-15
"""

import sqlalchemy as sa

from alembic import op

revision = "0005_soft_delete"
down_revision = "0004_stats_daily"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "links",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_links_is_active", "links", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_links_is_active", table_name="links")
    op.drop_column("links", "is_active")
