"""cascade delete click_stats when link deleted

Revision ID: 0003_fk_cascade
Revises: 0002_code_len
Create Date: 2026-10-24
"""
from alembic import op

revision = "0003_fk_cascade"
down_revision = "0002_code_len"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("click_stats_link_id_fkey", "click_stats", type_="foreignkey")
    op.create_foreign_key(
        "click_stats_link_id_fkey",
        "click_stats", "links",
        ["link_id"], ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("click_stats_link_id_fkey", "click_stats", type_="foreignkey")
    op.create_foreign_key(
        "click_stats_link_id_fkey",
        "click_stats", "links",
        ["link_id"], ["id"],
    )