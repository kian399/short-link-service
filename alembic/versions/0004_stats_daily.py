"""add link_stats_daily table

Revision ID: 0004_stats_daily
Revises: 0003_fk_cascade
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_stats_daily"
down_revision = "0003_fk_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "link_stats_daily",
        sa.Column("link_id", sa.Integer(), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("clicks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["link_id"], ["links.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("link_id", "stat_date"),
    )


def downgrade() -> None:
    op.drop_table("link_stats_daily")
