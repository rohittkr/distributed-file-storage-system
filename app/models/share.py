from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Share(Base):
    __tablename__ = "shares"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("files.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    owner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    permission: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="viewer",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    file = relationship(
        "File",
        back_populates="shares",
    )