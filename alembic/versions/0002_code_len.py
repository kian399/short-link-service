"""widen links.code to 16 chars

Revision ID: 0002_code_len
Revises: 0001_initial
Create Date: 2026-10-24
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_code_len"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 自定义别名允许 4-16 位，原列宽 10 会把 11-16 位的别名打成 500
    op.alter_column(
        "links",
        "code",
        existing_type=sa.String(length=10),
        type_=sa.String(length=16),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "links",
        "code",
        existing_type=sa.String(length=16),
        type_=sa.String(length=10),
        existing_nullable=False,
    )
