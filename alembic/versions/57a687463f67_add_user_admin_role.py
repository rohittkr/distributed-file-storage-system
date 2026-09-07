"""add user admin role

Revision ID: 57a687463f67
Revises: e3889a51ba5a
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "57a687463f67"
down_revision: Union[str, Sequence[str], None] = "e3889a51ba5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.alter_column(
        "users",
        "is_admin",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column(
        "users",
        "is_admin",
    )