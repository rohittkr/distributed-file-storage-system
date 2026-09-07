"""add content objects

Revision ID: 3ebbed207103
Revises: 57a687463f67
Create Date: 2026-09-07 12:53:48.694776

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3ebbed207103"
down_revision: Union[str, Sequence[str], None] = "57a687463f67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    op.create_table(
        "content_objects",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "content_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "size_bytes",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "reference_count",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "content_hash",
            "size_bytes",
            name="uq_content_objects_hash_size",
        ),
    )

    op.create_index(
        "ix_content_objects_content_hash",
        "content_objects",
        ["content_hash"],
        unique=False,
    )

    op.add_column(
        "chunks",
        sa.Column(
            "content_object_id",
            sa.BigInteger(),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_chunks_content_object_id",
        "chunks",
        ["content_object_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_chunks_content_object_id",
        "chunks",
        "content_objects",
        ["content_object_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_constraint(
        "fk_chunks_content_object_id",
        "chunks",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_chunks_content_object_id",
        table_name="chunks",
    )

    op.drop_column(
        "chunks",
        "content_object_id",
    )

    op.drop_index(
        "ix_content_objects_content_hash",
        table_name="content_objects",
    )

    op.drop_table("content_objects")