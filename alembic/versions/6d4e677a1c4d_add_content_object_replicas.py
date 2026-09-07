"""add content object replicas

Revision ID: 6d4e677a1c4d
Revises: 3ebbed207103
Create Date: 2026-09-07 14:44:20.216605

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6d4e677a1c4d"
down_revision: Union[str, Sequence[str], None] = "3ebbed207103"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    op.create_table(
        "content_object_replicas",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "content_object_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "storage_node_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "storage_key",
            sa.String(length=1024),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "checksum",
            sa.String(length=64),
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
        sa.ForeignKeyConstraint(
            ["content_object_id"],
            ["content_objects.id"],
            name="fk_content_object_replicas_content_object_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["storage_node_id"],
            ["storage_nodes.id"],
            name="fk_content_object_replicas_storage_node_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "content_object_id",
            "storage_node_id",
            name="uq_content_object_replicas_object_node",
        ),
    )

    op.create_index(
        "ix_content_object_replicas_content_object_id",
        "content_object_replicas",
        ["content_object_id"],
        unique=False,
    )

    op.create_index(
        "ix_content_object_replicas_status",
        "content_object_replicas",
        ["status"],
        unique=False,
    )

    op.create_index(
        "ix_content_object_replicas_storage_node_id",
        "content_object_replicas",
        ["storage_node_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_index(
        "ix_content_object_replicas_storage_node_id",
        table_name="content_object_replicas",
    )

    op.drop_index(
        "ix_content_object_replicas_status",
        table_name="content_object_replicas",
    )

    op.drop_index(
        "ix_content_object_replicas_content_object_id",
        table_name="content_object_replicas",
    )

    op.drop_table("content_object_replicas")