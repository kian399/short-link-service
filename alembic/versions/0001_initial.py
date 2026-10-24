"""create links and click_stats

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-24
"""

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_links_code", "links", ["code"], unique=True)
    op.create_index("ix_links_expires_at", "links", ["expires_at"], unique=False)

    op.create_table(
        "click_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("link_id", sa.Integer(), nullable=False),
        sa.Column("clicked_at", sa.DateTime(), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["link_id"], ["links.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_click_stats_link_id", "click_stats", ["link_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_click_stats_link_id", table_name="click_stats")
    op.drop_table("click_stats")
    op.drop_index("ix_links_expires_at", table_name="links")
    op.drop_index("ix_links_code", table_name="links")
    op.drop_table("links")
