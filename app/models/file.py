from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    mime_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )
    current_version_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    owner = relationship(
        "User",
        back_populates="files",
    )

    versions = relationship(
        "FileVersion",
        back_populates="file",
        cascade="all, delete-orphan",
    )

    shares = relationship(
        "Share",
        back_populates="file",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_files_owner_name", "owner_id", "name"),
    )


class FileVersion(Base):
    __tablename__ = "file_versions"

    __table_args__ = (
        UniqueConstraint(
            "file_id",
            "version_number",
            name="uq_file_versions_file_version",
        ),
        Index(
            "ix_file_versions_file_created",
            "file_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("files.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    checksum: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    file = relationship(
        "File",
        back_populates="versions",
    )

    chunks = relationship(
        "Chunk",
        back_populates="version",
        cascade="all, delete-orphan",
    )


class Chunk(Base):
    __tablename__ = "chunks"

    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "chunk_number",
            name="uq_chunks_version_number",
        ),
        Index(
            "ix_chunks_content_hash",
            "content_hash",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("file_versions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    chunk_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    checksum: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    content_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    version = relationship(
        "FileVersion",
        back_populates="chunks",
    )

    replicas = relationship(
        "ChunkReplica",
        back_populates="chunk",
        cascade="all, delete-orphan",
    )


class StorageNode(Base):
    __tablename__ = "storage_nodes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    node_id: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        index=True,
        nullable=False,
    )
    endpoint: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="healthy",
    )
    capacity_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )
    used_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )
    last_heartbeat: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    replicas = relationship(
        "ChunkReplica",
        back_populates="storage_node",
        cascade="all, delete-orphan",
    )


class ChunkReplica(Base):
    __tablename__ = "chunk_replicas"

    __table_args__ = (
        UniqueConstraint(
            "chunk_id",
            "storage_node_id",
            name="uq_chunk_replicas_chunk_node",
        ),
        Index(
            "ix_chunk_replicas_status",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chunk_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chunks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    storage_node_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("storage_nodes.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    storage_key: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="healthy",
    )
    checksum: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    chunk = relationship(
        "Chunk",
        back_populates="replicas",
    )

    storage_node = relationship(
        "StorageNode",
        back_populates="replicas",
    )


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    __table_args__ = (
        Index(
            "ix_upload_sessions_owner_status",
            "owner_id",
            "status",
        ),
        Index(
            "ix_upload_sessions_expires",
            "expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("files.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    mime_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    total_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    chunk_size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    total_chunks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    received_chunks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )